"""The Domains staff routes: registered, closed while off, audited, and the four money rules of
"cancel request" and "confirmed registered". The staff session machinery is the one the other
staff tests use."""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.domains import bridge, names, purchase, staff, tasks
from apps.domains.models import (
    ClaimState, Domain, DomainEvent, DomainRegistration, RegistrationState,
)
from apps.domains.tests.base import DOMAIN_SETTINGS, DomainsTestCase, make_bundle
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff

BASE = "/staff/v1/domains/"
PATHS = ("list/", "retry/", "disconnect/", "cancel-request/", "confirmed-registered/", "import-bridge/")


@on_staff
@override_settings(**DOMAIN_SETTINGS)
class StaffDomainCase(StaffTestCase):
    site = DomainsTestCase.site
    pay = DomainsTestCase.pay
    _n = 0

    def setUp(self):
        super().setUp()
        cache.clear()
        names._cache.update(key=None, rules=None)
        self.operator = self.enrolled().user
        self.staff_sign_in()
        self.bundle = make_bundle()
        self.fake = self.bundle.fakes["registrar"]
        patcher = mock.patch.object(staff.providers, "current", return_value=self.bundle)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = timezone.now()

    def request(self, name="brand.com", state=ClaimState.CONFIRMED):
        site = self.site(paid=True)
        DomainRegistration.objects.create(site=site, name=name)
        return Domain.objects.create(site=site, name=name, state=state)

    def conflicted(self, name="brand.com"):
        claim = self.request(name)
        self.fake.register_mode = "409"
        purchase.advance(claim.pk, providers=self.bundle, now=self.now)
        claim.refresh_from_db()
        self.assertEqual(claim.state, ClaimState.CONFLICT)
        return claim

    def act(self, path, claim):
        return self.call("POST", BASE + path, {"claim": claim.public_id})

    def audits(self, action):
        return list(OperatorAuditEntry.objects.filter(action=action))


class RegistrationTests(StaffDomainCase):
    def test_the_routes_are_registered_as_posts_and_nothing_else_is_there(self):
        self.assertEqual(sorted(staff.ROUTES), sorted(BASE.removeprefix("/staff/v1/") + p for p in PATHS))
        for path in staff.ROUTES:
            self.assertEqual(staff_views.ROUTES[path][0], "POST")

    def test_no_route_can_spend_money_or_record_a_purchase_by_hand(self):
        words = ("i-registered", "registered-it", "allow", "one-more", "manual", "purchase", "buy", "send", "sent")
        for path in list(staff.ROUTES) + [p for p in staff_views.ROUTES if p.startswith("domains")]:
            leaf = path.removeprefix("domains/")
            self.assertFalse([w for w in words if w in leaf], path)
        from apps.domains import api_owner
        for _prefix, router in __import__("config.api", fromlist=["api"]).api._routers:
            if router is api_owner.router:
                for path in router.path_operations:
                    self.assertFalse([w for w in words if w in path.removeprefix("/sites/{site_id}/domain/") and w != "buy"], path)
        self.assertEqual(sorted(p.removeprefix("/sites/{site_id}/domain/") for p in api_owner.router.path_operations),
                         ["", "buy/", "check/", "retry/"])

    @override_settings(DOMAINS_ENABLED=False)
    def test_every_route_is_a_404_while_domains_are_off(self):
        claim = self.request()
        for path in PATHS:
            self.assertError(self.act(path, claim), 404, "not_found")
        self.assertFalse(OperatorAuditEntry.objects.filter(action__startswith="domains_").exists())

    def test_an_unknown_claim_is_a_404(self):
        self.assertError(self.call("POST", BASE + "retry/", {"claim": "dom_00000000000000000000"}), 404, "not_found")
        self.assertError(self.call("POST", BASE + "retry/", {}), 404, "not_found")


class ListRetryDisconnectTests(StaffDomainCase):
    def test_the_list_shows_claims_eligible_sites_and_the_cap_counters(self):
        claim = self.conflicted()
        body = self.call("POST", BASE + "list/").json()
        self.assertEqual([c["claim"] for c in body["claims"]], [claim.public_id])
        self.assertEqual(body["claims"][0]["registration"], "conflict")
        self.assertEqual((body["claims"][0]["site"], body["claims"][0]["site_name"]),
                         (claim.site.public_id, claim.site.display_name))
        DomainEvent.objects.create(site=claim.site, kind="eligible")
        self.assertEqual(self.call("POST", BASE + "list/").json()["eligible_sites"],
                         [{"id": claim.site.public_id, "name": claim.site.display_name}])
        self.assertEqual(body["counters"], {"purchases_24h": 1, "spend_24h_cents": 857, "purchase_cap": 3,
                                            "spend_cap_cents": 4000, "purchase_enabled": True})

    def test_retry_moves_a_failed_claim_back_to_setup_and_is_audited(self):
        claim = self.request(state=ClaimState.FAILED)
        DomainRegistration.objects.filter(site=claim.site).update(
            state="succeeded", register_sent_at=self.now, send_count=1)
        response = self.act("retry/", claim)
        self.assertEqual(response.json()["state"], "zone")
        self.assertEqual(self.audits("domains_retry")[0].target_id, claim.site.public_id)
        event = DomainEvent.objects.get(kind="operator_retry")
        self.assertEqual((event.actor_type, event.actor_id), ("operator", self.operator.pk))
        self.assertEqual(self.fake.register_calls, 0)
        self.assertError(self.act("retry/", claim), 409, "nothing_to_retry")

    def test_disconnect_queues_the_removal_and_is_audited(self):
        claim = self.request(state=ClaimState.LIVE)
        DomainRegistration.objects.filter(site=claim.site).update(
            state="succeeded", register_sent_at=self.now, send_count=1)
        self.assertEqual(self.act("disconnect/", claim).json()["state"], "removal_pending")
        self.assertEqual(len(self.audits("domains_disconnect")), 1)
        self.assertError(self.act("disconnect/", claim), 409, "already_ending")


class CancelRequestTests(StaffDomainCase):
    def test_cancel_releases_the_name_ends_the_claim_and_is_audited(self):
        claim = self.request()
        reg = DomainRegistration.objects.get(site=claim.site)
        DomainRegistration.objects.filter(pk=reg.pk).update(check_name="brand.com", check_at=self.now, currency="USD")
        self.assertEqual(self.act("cancel-request/", claim).json()["state"], "ended")
        reg.refresh_from_db()
        self.assertEqual((reg.name, reg.check_name, reg.check_at, reg.currency, reg.state), (None, "", None, "", "intent"))
        self.assertEqual(len(self.audits("domains_request_cancelled")), 1)
        self.assertEqual(DomainEvent.objects.get(kind="request_cancelled").actor_id, self.operator.pk)
        self.assertEqual(self.fake.calls, [])
        # the site may Buy again later, and the name is free for another site
        other = self.request("brand.com")
        self.assertEqual(other.name, "brand.com")

    def test_cancel_is_refused_for_everything_that_has_sent_or_is_in_conflict(self):
        sent = self.conflicted("one.com")
        self.assertError(self.act("cancel-request/", sent), 409, "not_cancellable")  # in conflict
        for state in (RegistrationState.SENT, RegistrationState.SUCCEEDED):
            claim = self.request(f"{state}.com")
            DomainRegistration.objects.filter(site=claim.site).update(
                state=state, register_sent_at=self.now, send_count=1)
            Domain.objects.filter(pk=claim.pk).update(state=ClaimState.REGISTERING)
            self.assertError(self.act("cancel-request/", claim), 409, "not_cancellable")
        self.assertEqual(self.audits("domains_request_cancelled"), [])
        reg = DomainRegistration.objects.get(name="one.com")
        self.assertEqual((reg.state, reg.send_count), (RegistrationState.CONFLICT, 1))

    def test_a_claim_in_conflict_with_an_unsent_row_is_not_cancellable(self):
        claim = self.request()
        Domain.objects.filter(pk=claim.pk).update(state=ClaimState.CONFLICT)
        self.assertError(self.act("cancel-request/", claim), 409, "not_cancellable")


class ConfirmedRegisteredTests(StaffDomainCase):
    """Route side: validate, audit, 202. Worker side (`purchase.process_confirm`): the lock, the
    provider read and every refusal."""

    def ask(self, claim):
        response = self.act("confirmed-registered/", claim)
        self.assertEqual(response.status_code, 202, response.content)
        return response

    def work(self, claim):
        result = purchase.process_confirm(claim.pk, self.bundle, now=self.now)
        tasks._audit_confirm(claim.pk, result)
        return result

    def refused(self, claim, reason):
        self.ask(claim)
        self.assertEqual(self.work(claim), reason)
        reg = DomainRegistration.objects.get(site=claim.site)
        self.assertEqual(reg.state, RegistrationState.CONFLICT)
        self.assertFalse(purchase.confirm_pending(reg))
        return reg

    def test_the_route_refuses_unless_the_row_is_in_conflict_and_never_calls_a_provider(self):
        for state, extra in (("intent", {}), ("sent", {"register_sent_at": self.now, "send_count": 1}),
                             ("succeeded", {"register_sent_at": self.now, "send_count": 1})):
            claim = self.request(f"{state}.com")
            DomainRegistration.objects.filter(site=claim.site).update(state=state, **extra)
            self.assertError(self.act("confirmed-registered/", claim), 409, "confirm_refused")
        self.assertEqual(DomainEvent.objects.filter(kind="confirm_requested").count(), 0)
        self.assertEqual(self.fake.calls, [])

    def test_the_route_writes_the_request_and_the_audit_entry_and_answers_202(self):
        claim = self.conflicted()
        calls = len(self.fake.calls)
        response = self.ask(claim)
        self.assertEqual(response.json(), {"claim": claim.public_id, "status": "requested"})
        event = DomainEvent.objects.get(kind="confirm_requested")
        self.assertEqual((event.actor_type, event.actor_id), ("operator", self.operator.pk))
        self.assertEqual(len(self.audits("domains_confirm_requested")), 1)
        self.assertEqual(len(self.fake.calls), calls)  # nothing read here: the api has no token
        listed = self.call("POST", BASE + "list/").json()["claims"][0]["confirm"]
        self.assertEqual(listed["state"], "pending")

    def test_a_second_request_while_one_is_pending_is_idempotent(self):
        claim = self.conflicted()
        self.ask(claim)
        self.assertEqual(self.ask(claim).json()["status"], "pending")
        self.assertEqual(DomainEvent.objects.filter(kind="confirm_requested").count(), 1)
        self.assertEqual(len(self.audits("domains_confirm_requested")), 1)

    def test_the_worker_with_nothing_pending_does_nothing(self):
        claim = self.conflicted()
        self.assertEqual(self.work(claim), "none")
        self.assertEqual(self.fake.get_calls if hasattr(self.fake, "get_calls") else 0, 0)

    def test_the_worker_refuses_unless_the_provider_read_shows_the_name_in_our_account(self):
        claim = self.conflicted()
        before = self.fake.register_calls
        self.refused(claim, "not_in_our_account")  # 404
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_other"}
        self.refused(claim, "read_failed_other_account")
        self.fake.registrations["brand.com"]["account_id"] = "acct_test"
        with mock.patch.object(self.bundle.registrar, "get", return_value={"name": "different.com", "expires_at": None, "auto_renew": None}):
            self.refused(claim, "not_in_our_account")
        self.fake.get_error = 500
        self.refused(claim, "read_failed_unavailable")
        self.fake.get_error = None
        self.assertEqual(self.fake.register_calls, before)
        self.assertEqual(DomainEvent.objects.filter(kind="conflict_confirm_refused").count(), 4)
        self.assertEqual(len(self.audits("domains_confirm_refused")), 4)

    def test_paused_and_no_provider_record_a_refusal_and_change_nothing(self):
        claim = self.conflicted()
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_test"}
        calls = len(self.fake.calls)
        with override_settings(DOMAINS_PROVIDER_PAUSED=True):
            self.refused(claim, "paused")
        self.ask(claim)
        self.assertEqual(purchase.process_confirm(claim.pk, None, now=self.now), "provider_unavailable")
        self.assertEqual(len(self.fake.calls), calls)
        reg = DomainRegistration.objects.get(site=claim.site)
        self.assertEqual((reg.state, reg.send_count), (RegistrationState.CONFLICT, 1))
        last = self.call("POST", BASE + "list/").json()["claims"][0]["confirm"]
        self.assertEqual((last["state"], last["reason"]), ("refused", "provider_unavailable"))

    def test_the_worker_refuses_while_the_status_is_not_settled(self):
        claim = self.conflicted()
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_test"}
        for state in ("in_progress", "failed", "action_required", "blocked"):
            self.fake.statuses["brand.com"] = [state]
            self.refused(claim, f"status_{state}")
        self.fake.statuses.clear()
        self.fake.status_error = 500
        self.refused(claim, "status_failed_unavailable")

    def test_the_worker_records_what_exists_spends_nothing_and_changes_no_send_state(self):
        claim = self.conflicted()
        reg = DomainRegistration.objects.get(site=claim.site)
        sent_at, count, name = reg.register_sent_at, reg.send_count, reg.name
        sends = DomainEvent.objects.filter(kind="register_sent").count()
        calls = self.fake.register_calls
        self.fake.registrations["brand.com"] = {"expires_at": "2027-11-22T00:00:00Z", "auto_renew": True, "account_id": "acct_test"}
        self.ask(claim)
        self.assertEqual(self.work(claim), "done")
        claim.refresh_from_db()
        reg.refresh_from_db()
        self.assertEqual(claim.state, ClaimState.REGISTERED)
        self.assertEqual((reg.state, reg.register_sent_at, reg.send_count, reg.name), ("succeeded", sent_at, count, name))
        self.assertEqual((reg.auto_renew, reg.expires_at.year), (True, 2027))
        self.assertEqual(self.fake.register_calls, calls)
        self.assertEqual(DomainEvent.objects.filter(kind="register_sent").count(), sends)
        event = DomainEvent.objects.get(kind="conflict_confirmed_registered")
        self.assertEqual((event.actor_type, event.actor_id), ("operator", self.operator.pk))
        self.assertEqual(len(self.audits("domains_confirmed_registered")), 1)
        self.assertError(self.act("confirmed-registered/", claim), 409, "confirm_refused")  # no longer in conflict
        self.assertEqual(self.work(claim), "none")

    def test_the_sweep_finds_a_request_whose_task_was_lost(self):
        claim = self.conflicted()
        self.assertEqual(purchase.pending_confirms(), [])
        self.ask(claim)
        self.assertEqual(purchase.pending_confirms(), [claim.pk])


class BridgeImportTests(StaffDomainCase):
    """The bridge import: the route half (validate, audit, 202) and the worker half
    (`bridge.process_import`): the registrar read, every refusal, and no `register` ever."""
    URL = BASE + "import-bridge/"

    def ask(self, site, name="brand.com", expect=202):
        response = self.call("POST", self.URL, {"site": site.public_id, "name": name})
        self.assertEqual(response.status_code, expect, response.content)
        return response

    def refusal(self, response, reason, status=409):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], "import_refused")
        self.assertEqual(response.json()["error"]["fields"]["reason"], [reason])

    def work(self, site):
        result = bridge.process_import(site.pk, self.bundle, now=self.now)
        tasks._audit_import(site.pk, result)
        return result

    def ours(self, name="brand.com", expires="2027-11-22T00:00:00Z"):
        self.fake.registrations[name] = {"expires_at": expires, "auto_renew": False, "account_id": "acct_test"}

    def paid(self):
        return self.site(paid=True)

    def test_the_route_is_open_only_to_staff_and_404_while_off(self):
        site = self.paid()
        with override_settings(DOMAINS_ENABLED=False):
            self.assertError(self.call("POST", self.URL, {"site": site.public_id, "name": "brand.com"}), 404, "not_found")
        self.assertError(self.call("POST", self.URL, {"site": "site_nope", "name": "brand.com"}), 404, "not_found")
        self.assertError(self.call("POST", self.URL, {}), 404, "not_found")

    def test_the_route_writes_the_request_and_audit_and_calls_no_provider(self):
        site = self.paid()
        self.assertEqual(self.ask(site).json(), {"site": site.public_id, "name": "brand.com", "status": "requested"})
        reg = DomainRegistration.objects.get(site=site)
        self.assertEqual((reg.name, reg.state, reg.send_count, reg.register_sent_at), ("brand.com", "intent", 0, None))
        event = DomainEvent.objects.get(kind="import_requested")
        self.assertEqual((event.actor_type, event.actor_id), ("operator", self.operator.pk))
        self.assertEqual(len(self.audits("domains_import_requested")), 1)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(Domain.objects.filter(site=site).exists())
        listed = self.call("POST", BASE + "list/").json()["imports"]
        self.assertEqual([(r["site"], r["name"], r["state"]) for r in listed], [(site.public_id, "brand.com", "pending")])

    def test_a_repeat_request_is_pending_and_writes_nothing_new(self):
        site = self.paid()
        self.ask(site)
        self.assertEqual(self.ask(site).json()["status"], "pending")
        self.assertEqual(DomainEvent.objects.filter(kind="import_requested").count(), 1)
        self.assertEqual(len(self.audits("domains_import_requested")), 1)
        self.refusal(self.ask(site, "other.com", expect=409), "has_registration")

    def test_a_bad_name_is_a_400_and_a_platform_name_is_refused(self):
        site = self.paid()
        self.assertEqual(self.ask(site, "https://x.com", expect=400).json()["error"]["code"], "invalid_request")
        self.refusal(self.ask(site, "negosyonline.com", expect=409), "name_refused")
        self.assertFalse(DomainRegistration.objects.filter(site=site, name__isnull=False).exists())

    def test_refused_for_exempt_deleted_and_no_subscription_sites(self):
        exempt = self.paid()
        from apps.billing.models import Subscription
        Subscription.objects.filter(site=exempt).update(billing_exempt=True)
        self.refusal(self.ask(exempt, expect=409), "exempt")
        gone = self.paid()
        gone.deleted_at = self.now
        gone.save()
        self.refusal(self.ask(gone, expect=409), "site_off")
        self.assertEqual(DomainEvent.objects.filter(kind="import_requested").count(), 0)

    def test_refused_for_a_site_with_a_claim_or_a_registration(self):
        claimed = self.request("one.com")
        self.refusal(self.ask(claimed.site, "two.com", expect=409), "has_registration")
        site = self.paid()
        Domain.objects.create(site=site, name="three.com", state=ClaimState.CHECKING)
        self.refusal(self.ask(site, "four.com", expect=409), "has_claim")

    def test_refused_for_a_name_another_site_holds(self):
        self.request("brand.com")
        self.refusal(self.ask(self.paid(), expect=409), "name_held")

    def test_the_worker_imports_a_name_in_our_account_without_ever_registering(self):
        site = self.paid()
        self.ours()
        self.ask(site)
        sends = DomainEvent.objects.filter(kind="register_sent").count()
        self.assertEqual(self.work(site), "done")
        reg = DomainRegistration.objects.get(site=site)
        self.assertEqual((reg.state, reg.name, reg.auto_renew), ("succeeded", "brand.com", False))
        self.assertEqual(reg.expires_at.year, 2027)
        self.assertEqual(reg.send_count, 1)  # the table's rule for a succeeded row; nothing was sent
        claim = Domain.objects.get(site=site)
        self.assertEqual((claim.state, claim.name), (ClaimState.REGISTERED, "brand.com"))
        self.assertEqual(self.fake.register_calls, 0)
        self.assertEqual(DomainEvent.objects.filter(kind="register_sent").count(), sends)
        event = DomainEvent.objects.get(kind="bridge_imported")
        self.assertEqual((event.actor_type, event.actor_id), ("operator", self.operator.pk))
        self.assertEqual(self.audits("domains_bridge_imported")[0].operator_id, self.operator.pk)
        self.assertEqual(self.call("POST", BASE + "list/").json()["imports"][0]["state"], "done")
        self.assertEqual(self.work(site), "none")
        self.refusal(self.ask(site, "brand.com", expect=409), "has_registration")

    def test_the_imported_claim_goes_on_only_through_the_machine_and_its_switches(self):
        site = self.paid()
        self.ours()
        self.ask(site)
        self.work(site)
        claim = Domain.objects.get(site=site)
        self.assertEqual(machine_advance(claim, self.bundle, self.now), "zone")  # the normal first step
        self.assertEqual(machine_advance(claim, self.bundle, self.now), "waiting_zone")  # no zone yet: waits
        self.assertEqual(self.fake.register_calls, 0)
        self.assertEqual(self.bundle.fakes["vercel"].calls, [])  # nothing is attached before the zone exists

    def test_the_worker_refuses_and_gives_the_name_back_unless_the_read_shows_it_in_our_account(self):
        site = self.paid()
        self.ask(site)
        self.assertEqual(self.work(site), "not_in_our_account")  # 404
        reg = DomainRegistration.objects.get(site=site)
        self.assertEqual((reg.name, reg.state, reg.send_count), (None, "intent", 0))
        self.assertFalse(Domain.objects.filter(site=site).exists())
        self.assertEqual(self.audits("domains_import_refused")[0].detail["reason"], "not_in_our_account")
        row = self.call("POST", BASE + "list/").json()["imports"][0]
        self.assertEqual((row["state"], row["reason"]), ("refused", "not_in_our_account"))
        # another account, no expiry, unsettled status, a failed read, paused, no provider
        self.ask(site)
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_other"}
        self.assertEqual(self.work(site), "read_failed_other_account")
        self.ask(site)
        self.fake.registrations["brand.com"]["account_id"] = "acct_test"
        self.assertEqual(self.work(site), "no_expiry")
        self.ask(site)
        self.ours()
        self.fake.statuses["brand.com"] = ["in_progress"]
        self.assertEqual(self.work(site), "status_in_progress")
        self.fake.statuses.clear()
        self.ask(site)
        self.fake.get_error = 500
        self.assertEqual(self.work(site), "read_failed_unavailable")
        self.fake.get_error = None
        self.ask(site)
        with override_settings(DOMAINS_PROVIDER_PAUSED=True):
            self.assertEqual(self.work(site), "paused")
        self.ask(site)
        self.assertEqual(bridge.process_import(site.pk, None, now=self.now), "provider_unavailable")
        reg.refresh_from_db()
        self.assertEqual((reg.name, reg.state, reg.send_count), (None, "intent", 0))
        self.assertEqual(self.fake.register_calls, 0)
        self.assertFalse(Domain.objects.filter(site=site).exists())

    def test_the_worker_rechecks_exemption_and_claims_after_the_request(self):
        site = self.paid()
        self.ours()
        self.ask(site)
        from apps.billing.models import Subscription
        Subscription.objects.filter(site=site).update(billing_exempt=True)
        self.assertEqual(self.work(site), "exempt")
        self.assertEqual(DomainRegistration.objects.get(site=site).name, None)
        self.assertFalse(Domain.objects.filter(site=site).exists())

    def test_the_sweep_finds_a_request_whose_task_was_lost(self):
        site = self.paid()
        self.assertEqual(bridge.pending_imports(), [])
        self.ask(site)
        self.assertEqual(bridge.pending_imports(), [site.pk])
        self.ours()
        self.work(site)
        self.assertEqual(bridge.pending_imports(), [])


def machine_advance(claim, bundle, now):
    from apps.domains import machine  # noqa: PLC0415

    return machine.advance(claim.pk, providers=bundle, now=now)
