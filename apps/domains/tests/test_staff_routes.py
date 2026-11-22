"""The Domains staff routes: registered, closed while off, audited, and the four money rules of
"cancel request" and "confirmed registered". The staff session machinery is the one the other
staff tests use."""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.domains import names, purchase, staff
from apps.domains.models import (
    ClaimState, Domain, DomainEvent, DomainRegistration, RegistrationState,
)
from apps.domains.tests.base import DOMAIN_SETTINGS, DomainsTestCase, make_bundle
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff

BASE = "/staff/v1/domains/"
PATHS = ("list/", "retry/", "disconnect/", "cancel-request/", "confirmed-registered/")


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
    def refused(self, claim, reason):
        response = self.act("confirmed-registered/", claim)
        self.assertError(response, 409, "confirm_refused")
        self.assertEqual(response.json()["error"]["fields"]["reason"], [reason])
        reg = DomainRegistration.objects.get(site=claim.site)
        self.assertEqual(reg.state, RegistrationState.CONFLICT)
        return reg

    def test_it_is_refused_unless_the_row_is_in_conflict(self):
        for state, extra in (("intent", {}), ("sent", {"register_sent_at": self.now, "send_count": 1}),
                             ("succeeded", {"register_sent_at": self.now, "send_count": 1})):
            claim = self.request(f"{state}.com")
            DomainRegistration.objects.filter(site=claim.site).update(state=state, **extra)
            self.assertError(self.act("confirmed-registered/", claim), 409, "confirm_refused")
        self.assertEqual(self.audits("domains_confirmed_registered"), [])
        self.assertEqual(self.fake.calls, [])

    def test_it_is_refused_unless_the_provider_read_shows_the_name_in_our_account(self):
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
        with override_settings(DOMAINS_PROVIDER_PAUSED=True):
            self.refused(claim, "provider_unavailable")
        with mock.patch.object(staff.providers, "current", return_value=None):
            self.refused(claim, "provider_unavailable")
        self.assertEqual(self.fake.register_calls, before)
        self.assertEqual(len(self.audits("domains_confirm_refused")), 6)
        self.assertEqual(DomainEvent.objects.filter(kind="conflict_confirm_refused").count(), 6)

    def test_it_is_refused_while_the_status_is_not_settled(self):
        claim = self.conflicted()
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_test"}
        for state in ("in_progress", "failed", "action_required", "blocked"):
            self.fake.statuses["brand.com"] = [state]
            self.refused(claim, f"status_{state}")
        self.fake.statuses.clear()
        self.fake.status_error = 500
        self.refused(claim, "status_failed_unavailable")

    def test_it_records_what_exists_spends_nothing_and_changes_no_send_state(self):
        claim = self.conflicted()
        reg = DomainRegistration.objects.get(site=claim.site)
        sent_at, count, name = reg.register_sent_at, reg.send_count, reg.name
        sends = DomainEvent.objects.filter(kind="register_sent").count()
        calls = self.fake.register_calls
        self.fake.registrations["brand.com"] = {"expires_at": "2027-11-22T00:00:00Z", "auto_renew": True, "account_id": "acct_test"}
        response = self.act("confirmed-registered/", claim)
        self.assertEqual(response.json()["state"], "registered")
        reg.refresh_from_db()
        self.assertEqual((reg.state, reg.register_sent_at, reg.send_count, reg.name), ("succeeded", sent_at, count, name))
        self.assertEqual((reg.auto_renew, reg.expires_at.year), (True, 2027))
        self.assertEqual(self.fake.register_calls, calls)  # no register call
        self.assertEqual(DomainEvent.objects.filter(kind="register_sent").count(), sends)  # the cap counts are unchanged
        event = DomainEvent.objects.get(kind="conflict_confirmed_registered")
        self.assertEqual((event.actor_type, event.actor_id), ("operator", self.operator.pk))
        self.assertEqual(len(self.audits("domains_confirmed_registered")), 1)
        self.assertError(self.act("confirmed-registered/", claim), 409, "confirm_refused")  # not in conflict any more
