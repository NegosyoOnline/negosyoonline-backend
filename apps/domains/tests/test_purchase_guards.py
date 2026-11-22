"""Money guards (plan r4.3 item 2): the purchase sequence, every guard that stands between a
claim and a `register` POST. The real adapter runs over the fake Registrar; nothing touches the
network. Lock and race tests that need row locks are in test_purchase_locks.py (PostgreSQL only)."""
from datetime import timedelta
from unittest import mock

from django.db import IntegrityError
from django.test import override_settings
from django.utils import timezone

from apps.billing.models import Subscription
from apps.domains import context, purchase
from apps.domains.models import (
    ClaimState, Domain, DomainEvent, DomainRegistration, DomainTldPrice, RegistrationState,
)
from apps.domains.tests.base import DomainsTestCase, make_bundle

State = Subscription.State


class PurchaseCase(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.bundle = make_bundle()
        self.fake = self.bundle.fakes["registrar"]
        self.site_ = self.site(paid=True)
        self.t0 = timezone.now().replace(microsecond=0)

    def request(self, site=None, name="brand.com", state=ClaimState.CONFIRMED):
        """What the owner's Buy leaves: an unsent registration holding the name, and a claim."""
        site = site or self.site_
        DomainRegistration.objects.create(site=site, name=name)
        return Domain.objects.create(site=site, name=name, state=state)

    def advance(self, claim, minutes=0):
        word = purchase.advance(claim.pk, providers=self.bundle, now=self.t0 + timedelta(minutes=minutes))
        claim.refresh_from_db()
        return word

    def reg(self, claim):
        return DomainRegistration.objects.get(site=claim.site)

    def sends(self):
        return DomainEvent.objects.filter(kind="register_sent").count()

    def prepared(self):
        claim = self.request()
        reg = self.reg(claim)
        reg.check_name, reg.check_at, reg.check_registrable, reg.check_tier = "brand.com", self.t0, True, "standard"
        reg.check_registration_cost_cents = reg.check_renewal_cost_cents = 857
        reg.currency = "USD"
        reg.save()
        purchase.ledger()
        return claim, reg

    def assertSentNothing(self, claim):
        self.assertEqual(self.fake.register_calls, 0)
        reg = self.reg(claim)
        self.assertEqual((reg.state, reg.register_sent_at, reg.send_count), (RegistrationState.INTENT, None, 0))
        self.assertEqual(self.sends(), 0)


class HappyPathTests(PurchaseCase):
    def test_a_confirmed_request_is_bought_exactly_once_and_registered(self):
        claim = self.request()
        self.assertEqual(self.advance(claim), "registered")
        reg = self.reg(claim)
        self.assertEqual((reg.state, reg.send_count, reg.provider_status), (RegistrationState.SUCCEEDED, 1, "succeeded"))
        self.assertEqual(claim.state, ClaimState.REGISTERED)
        self.assertEqual(self.fake.register_calls, 1)
        self.assertEqual(self.fake.register_bodies, [{"domain_name": "brand.com"}])
        self.assertEqual(reg.expires_at.year, 2027)
        event = DomainEvent.objects.get(kind="register_sent")
        self.assertEqual(event.detail["check_registration_cost_cents"], 857)
        self.assertEqual(event.registration, reg)

    def test_a_second_pass_buys_nothing_more(self):
        claim = self.request()
        self.advance(claim)
        self.assertEqual(self.advance(claim), "idle")
        self.assertEqual(self.fake.register_calls, 1)

    def test_a_202_polls_and_never_posts_again(self):
        claim = self.request()
        self.fake.register_mode = "202"
        self.assertEqual(self.advance(claim), "registering")
        self.assertEqual(claim.state, ClaimState.REGISTERING)
        self.assertEqual(self.advance(claim, 1), "registering")
        self.fake.statuses["brand.com"] = ["succeeded"]
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_test"}
        self.assertEqual(self.advance(claim, 2), "registered")
        self.assertEqual(self.fake.register_calls, 1)
        self.assertEqual(self.reg(claim).state, RegistrationState.SUCCEEDED)


class GateAndSwitchTests(PurchaseCase):
    def test_nothing_is_bought_unless_the_gate_holds(self):
        cases = {}
        trial = self.site()  # on its trial, never paid
        cases["trial"] = trial
        for label, change in (
            ("suspended", {"state": State.SUSPENDED}),
            ("cancelled", {"state": State.CANCELLED, "service_ended_at": self.t0 - timedelta(days=1)}),
            ("attention", {"state": State.PAYMENT_REQUIRES_ATTENTION, "attention_started_at": self.t0}),
        ):
            site = self.site(paid=True)
            Subscription.objects.filter(site=site).update(**change)
            cases[label] = site
        site = self.site(paid=True)
        Subscription.objects.filter(site=site).update(billing_exempt=True)
        cases["exempt"] = site
        site = self.site(paid=True)
        site.operator_hold = True
        site.save(update_fields=["operator_hold"])
        cases["hold"] = site
        site = self.site()
        Subscription.objects.filter(site=site).update(state=State.ACTIVE)  # active, but no PAID cycle
        cases["no paid cycle"] = site
        for label, site in cases.items():
            with self.subTest(label):
                claim = self.request(site, f"{label.replace(' ', '')}.com")
                self.assertTrue(self.advance(claim).startswith("gate:"))
                self.assertEqual(claim.state, ClaimState.AWAITING_GATE)
        self.assertEqual(self.fake.calls, [])  # not even a Check
        self.assertEqual(self.sends(), 0)

    def test_the_gate_is_read_again_under_the_lock_at_step_one(self):
        claim = self.request()
        reg = self.reg(claim)
        purchase.ledger()
        Subscription.objects.filter(site=self.site_).update(state=State.SUSPENDED)
        reg.check_name, reg.check_at, reg.check_registrable, reg.check_tier = "brand.com", self.t0, True, "standard"
        reg.check_registration_cost_cents = reg.check_renewal_cost_cents = 857
        reg.currency = "USD"
        reg.save()
        self.assertEqual(purchase._step1(reg.pk, claim.pk, self.t0), ("wait", "gate:not_active"))
        self.assertEqual(self.fake.register_calls, 0)

    def test_an_early_first_payment_passes_the_gate(self):
        self.assertTrue(self.advance(self.request(self.site(), "early.com")).startswith("gate:"))
        claim = self.request(self.site(paid=True), "paid.com")
        self.assertEqual(self.advance(claim), "registered")

    @override_settings(DOMAINS_PURCHASE_ENABLED=False)
    def test_the_purchase_switch_off_stops_it_before_any_provider_call(self):
        claim = self.request()
        self.assertEqual(self.advance(claim), "purchase_off")
        self.assertEqual(self.fake.calls, [])
        self.assertSentNothing(claim)

    @override_settings(DOMAINS_PROVIDER_PAUSED=True)
    def test_the_kill_switch_stops_the_purchase(self):
        claim = self.request()
        self.assertEqual(self.advance(claim), "paused")
        self.assertEqual(self.fake.calls, [])
        self.assertSentNothing(claim)

    def test_without_a_registrar_nothing_happens(self):
        claim = self.request()
        self.assertEqual(purchase.advance(claim.pk, providers=None), "off")
        self.bundle.registrar = None
        self.assertEqual(purchase.advance(claim.pk, providers=self.bundle), "off")

    @override_settings(DOMAINS_ENABLED=False)
    def test_step_one_refuses_when_domains_are_off(self):
        claim, reg = self.prepared()
        self.assertEqual(purchase._step1(reg.pk, claim.pk, self.t0)[1], "switch_off")


class CeilingTests(PurchaseCase):
    def refused(self, name="brand.com"):
        claim = self.request(self.site(paid=True), name)
        self.assertEqual(self.advance(claim), "refused")
        self.assertEqual(claim.state, ClaimState.ENDED)
        reg = self.reg(claim)
        self.assertIsNone(reg.name)  # the name is released
        self.assertEqual((reg.check_name, reg.check_at, reg.currency), ("", None, ""))
        self.assertEqual(self.fake.register_calls, 0)
        self.assertEqual(self.sends(), 0)
        return claim

    def test_a_registration_price_over_the_ceiling_buys_nothing(self):
        self.fake.prices["com"] = (1201, 857)
        self.refused()

    def test_a_renewal_price_over_the_ceiling_buys_nothing(self):
        self.fake.prices["com"] = (857, 1201)
        self.assertIn("extension", self.refused().reason)

    def test_exactly_the_ceiling_is_allowed(self):
        self.fake.prices["com"] = (1200, 1200)
        self.assertEqual(self.advance(self.request()), "registered")

    def test_a_missing_ceiling_buys_nothing(self):
        with override_settings(DOMAINS_MAX_COST_CENTS=0):
            self.refused()

    def test_premium_non_usd_unlisted_extension_and_taken_names_are_refused(self):
        self.fake.tier["com"] = "premium"
        self.refused()
        self.fake.tier.clear()
        self.fake.currency = "EUR"
        self.refused("two.com")
        self.fake.currency = "USD"
        self.refused("shop.org")  # not in DOMAINS_ALLOWED_TLDS
        self.fake.taken.add("taken.com")
        self.assertIn("not available", self.refused("taken.com").reason)

    def test_a_refused_check_lets_another_site_take_the_released_name(self):
        self.fake.prices["com"] = (1201, 857)
        self.refused()
        self.fake.prices.clear()
        other = self.request(self.site(paid=True), "brand.com")
        self.assertEqual(self.advance(other), "registered")


class CapTests(PurchaseCase):
    def test_a_cap_of_zero_means_no_purchase_at_all(self):
        with override_settings(DOMAINS_DAILY_PURCHASE_CAP=0):
            claim = self.request()
            self.assertEqual(self.advance(claim), "cap_count")
            self.assertEqual(claim.state, ClaimState.AWAITING_GATE)
            self.assertEqual(self.fake.register_calls, 0)
        with override_settings(DOMAINS_DAILY_SPEND_CAP_CENTS=0):
            self.assertEqual(self.advance(claim, 1), "cap_spend")
            self.assertEqual(self.fake.register_calls, 0)

    def test_the_count_cap_holds_the_next_claim_until_the_day_passes(self):
        with override_settings(DOMAINS_DAILY_PURCHASE_CAP=1):
            first, second = self.request(name="one.com"), self.request(self.site(paid=True), "two.com")
            self.assertEqual(self.advance(first), "registered")
            self.assertEqual(self.advance(second, 1), "cap_count")
            self.assertEqual(second.state, ClaimState.AWAITING_GATE)
            self.assertEqual(self.fake.register_calls, 1)
            self.assertEqual(self.advance(second, 24 * 60 + 5), "registered")
            self.assertEqual(self.fake.register_calls, 2)

    def test_the_spend_cap_counts_what_was_sent(self):
        with override_settings(DOMAINS_DAILY_SPEND_CAP_CENTS=1500):
            self.assertEqual(self.advance(self.request(name="one.com")), "registered")
            second = self.request(self.site(paid=True), "two.com")
            self.assertEqual(self.advance(second, 1), "cap_spend")
            self.assertEqual(self.fake.register_calls, 1)

    def test_the_caps_count_events_so_a_reset_row_changes_nothing_and_cannot_send_again(self):
        claim = self.request()
        self.advance(claim)
        before = self.sends()
        reg = self.reg(claim)
        for reset in (
            lambda: DomainRegistration.objects.filter(pk=reg.pk).update(
                state="intent", register_sent_at=None, send_count=0),
            lambda: self.save_reset(reg),
        ):
            reset()
            self.assertEqual(self.sends(), before)  # the cap counts are unchanged
            claim.state = ClaimState.CONFIRMED
            Domain.objects.filter(pk=claim.pk).update(state=ClaimState.CONFIRMED)
            self.assertEqual(self.advance(claim, 5), "refused")  # the unique `register_sent` event refuses a second send
            self.assertEqual(self.fake.register_calls, 1)
            self.assertEqual(self.sends(), before)

    def save_reset(self, reg):
        reg.refresh_from_db()
        reg.state, reg.register_sent_at, reg.send_count = "intent", None, 0
        reg.save()

    def test_send_count_cannot_exceed_one(self):
        claim = self.request()
        self.advance(claim)
        with self.assertRaises(IntegrityError):
            DomainRegistration.objects.filter(pk=self.reg(claim).pk).update(send_count=2)


class StepOneTests(PurchaseCase):
    def test_a_name_changed_after_the_check_sends_nothing_until_a_fresh_check(self):
        claim, reg = self.prepared()
        DomainRegistration.objects.filter(pk=reg.pk).update(name="other.com")
        Domain.objects.filter(pk=claim.pk).update(name="other.com")
        self.assertEqual(purchase._step1(reg.pk, claim.pk, self.t0), ("wait", "check_name"))
        self.assertEqual(self.advance(claim), "registered")  # a fresh Check, for the new name
        self.assertEqual(self.fake.register_bodies, [{"domain_name": "other.com"}])

    def test_a_check_older_than_120_seconds_is_not_enough(self):
        claim, reg = self.prepared()
        self.assertEqual(purchase._step1(reg.pk, claim.pk, self.t0 + timedelta(seconds=121)), ("wait", "check_old"))
        self.assertEqual(purchase._step1(reg.pk, claim.pk, self.t0 + timedelta(seconds=120))[0], "sent")

    def test_a_second_step_one_on_a_sent_row_sends_nothing(self):
        claim, reg = self.prepared()
        self.assertEqual(purchase._step1(reg.pk, claim.pk, self.t0)[0], "sent")
        self.assertEqual(purchase._step1(reg.pk, claim.pk, self.t0), ("wait", "already_sent"))
        self.assertEqual(self.sends(), 1)

    def test_a_conditional_update_that_matches_nothing_sends_nothing(self):
        claim, reg = self.prepared()
        real = DomainRegistration.objects.filter
        with mock.patch.object(purchase.DomainRegistration.objects, "filter") as patched:
            patched.side_effect = lambda *a, **k: real(*a, **{**k, "send_count": 9}) if k.get("register_sent_at__isnull") else real(*a, **k)
            self.assertEqual(purchase._step1(reg.pk, claim.pk, self.t0), ("wait", "not_updated"))
        self.assertEqual(self.sends(), 0)


class CrashTests(PurchaseCase):
    def test_a_crash_before_step_one_sends_nothing_and_resumes(self):
        claim = self.request()
        with mock.patch.object(purchase, "_step1", side_effect=RuntimeError("crash")):
            self.assertEqual(self.advance(claim), "error")
        self.assertSentNothing(claim)
        self.assertEqual(self.advance(claim, 1), "registered")
        self.assertEqual(self.fake.register_calls, 1)

    def test_a_crash_after_step_one_never_sends_later_it_polls_then_conflicts(self):
        claim = self.request()
        with mock.patch.object(purchase, "_send", side_effect=RuntimeError("crash")):
            self.assertEqual(self.advance(claim), "error")
        reg = self.reg(claim)
        self.assertEqual((reg.state, reg.send_count), (RegistrationState.SENT, 1))
        self.assertEqual(claim.state, ClaimState.REGISTERING)
        self.assertEqual(self.advance(claim, 1), "registering")
        self.assertEqual(self.advance(claim, 29), "registering")
        self.assertEqual(self.fake.register_calls, 0)  # nothing was ever posted, and nothing will be
        self.assertEqual(self.advance(claim, 31), "conflict")
        self.assertEqual((claim.state, self.reg(claim).state), (ClaimState.CONFLICT, RegistrationState.CONFLICT))
        self.assertEqual(self.fake.register_calls, 0)

    def test_a_crash_after_step_two_polls_and_never_posts_again(self):
        claim = self.request()
        with mock.patch.object(purchase, "finalize", side_effect=RuntimeError("crash")):
            self.assertEqual(self.advance(claim), "error")
        self.assertEqual(self.fake.register_calls, 1)
        self.assertEqual(self.advance(claim, 1), "registered")
        self.assertEqual(self.fake.register_calls, 1)

    def test_a_lost_connection_leaves_the_outcome_unknown_and_is_never_retried(self):
        claim = self.request()
        self.fake.register_mode = "timeout"
        self.assertEqual(self.advance(claim), "unknown")
        self.assertEqual(self.reg(claim).state, RegistrationState.SENT)
        self.fake.register_mode = "201"  # even if the provider would now accept it
        self.assertEqual(self.advance(claim, 5), "registering")
        self.assertEqual(self.fake.register_calls, 1)

    def test_adoption_only_for_our_own_sent_post_with_an_unknown_outcome(self):
        claim = self.request()
        self.fake.register_mode = "timeout"
        self.advance(claim)
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_test"}
        self.fake.status_error = 404
        self.assertEqual(self.advance(claim, 2), "registered")
        self.assertEqual(self.fake.register_calls, 1)
        self.assertTrue(DomainEvent.objects.get(kind="registered").detail["adopted"])

    def test_domain_unavailable_to_our_own_post_is_never_adopted(self):
        claim = self.request()
        self.fake.register_mode = "409"
        self.assertEqual(self.advance(claim), "conflict")
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_test"}
        self.assertEqual(self.advance(claim, 1), "idle")
        self.assertEqual(self.reg(claim).state, RegistrationState.CONFLICT)
        self.assertEqual(self.fake.register_calls, 1)


class ConflictTests(PurchaseCase):
    def assertConflict(self, claim):
        self.assertEqual(claim.state, ClaimState.CONFLICT)
        self.assertEqual(self.reg(claim).state, RegistrationState.CONFLICT)
        self.assertEqual(DomainEvent.objects.filter(kind="registration_conflict").count(), 1)

    def test_a_refusal_to_our_own_post_goes_to_conflict(self):
        for mode in ("409", "400", "403"):
            with self.subTest(mode=mode):
                self.fake.register_mode = mode
                claim = self.request(self.site(paid=True), f"x{mode}.com")
                self.assertEqual(self.advance(claim), "conflict")
                self.assertEqual(self.reg(claim).state, RegistrationState.CONFLICT)

    def test_action_required_blocked_and_failed_go_to_conflict_and_stop_polling(self):
        for state in ("action_required", "blocked", "failed"):
            with self.subTest(state=state):
                self.fake.register_mode = "202"
                claim = self.request(self.site(paid=True), f"{state.replace('_', '')}.com")
                self.assertEqual(self.advance(claim), "registering")
                self.fake.statuses[claim.name] = [state]
                self.assertEqual(self.advance(claim, 1), "conflict")
                self.assertEqual(self.reg(claim).provider_status, state)
                before = len(self.fake.calls)
                self.assertEqual(self.advance(claim, 2), "idle")
                self.assertEqual(len(self.fake.calls), before)

    def test_unknown_after_thirty_minutes_goes_to_conflict(self):
        claim = self.request()
        self.fake.register_mode = "202"
        self.advance(claim)
        self.assertEqual(self.advance(claim, 29), "registering")
        self.assertEqual(self.advance(claim, 31), "conflict")
        self.assertConflict(claim)
        self.assertEqual(self.fake.register_calls, 1)

    def test_a_status_that_cannot_be_read_is_unknown_not_a_second_send(self):
        claim = self.request()
        self.fake.register_mode = "202"
        self.advance(claim)
        self.fake.status_error = 500
        self.assertEqual(self.advance(claim, 5), "registering")
        self.assertEqual(self.fake.register_calls, 1)

    def test_a_conflicted_registration_blocks_a_new_claim_from_buying(self):
        claim = self.request()
        self.fake.register_mode = "409"
        self.advance(claim)
        Domain.objects.filter(pk=claim.pk).update(state=ClaimState.ENDED)
        fresh = Domain.objects.create(site=self.site_, name="brand.com", state=ClaimState.CONFIRMED)
        self.assertEqual(self.advance(fresh), "conflict")
        self.assertEqual(self.fake.register_calls, 1)


class SentinelTests(PurchaseCase):
    def test_the_sentinel_stores_a_reading_per_allowed_extension_and_withdraws_over_ceiling(self):
        self.fake.prices["net"] = (857, 1500)
        out = purchase.sentinel(self.bundle, self.t0)
        self.assertEqual(out["read"], 2)
        com, net = DomainTldPrice.objects.get(tld="com"), DomainTldPrice.objects.get(tld="net")
        self.assertTrue(com.allowed)
        self.assertFalse(net.allowed)
        self.assertEqual(out["withdrawn"], ["net"])
        for request in self.fake.requests:
            self.assertTrue(request[2]["domains"][0].startswith("nego-"))  # a random unregistered name

    def test_a_failed_read_leaves_the_old_rows_which_then_go_stale(self):
        from apps.domains.services import offered_tlds
        purchase.sentinel(self.bundle, self.t0)
        self.assertEqual(offered_tlds(self.t0), ["com", "net"])
        self.fake.fail_next(500)
        purchase.sentinel(self.bundle, self.t0 + timedelta(hours=30))
        self.assertEqual(offered_tlds(self.t0 + timedelta(hours=30)), ["com", "net"])
        self.assertEqual(offered_tlds(self.t0 + timedelta(hours=37)), [])  # a stale reading withdraws it

    def test_a_non_usd_reading_withdraws_the_extension(self):
        purchase.sentinel(self.bundle, self.t0)
        self.fake.currency = "EUR"
        purchase.sentinel(self.bundle, self.t0 + timedelta(hours=1))
        self.assertFalse(DomainTldPrice.objects.filter(allowed=True).exists())


class WebRequestTests(PurchaseCase):
    def test_the_purchase_refuses_inside_a_web_request_before_step_one(self):
        claim = self.request()
        context._started(None)
        try:
            self.assertEqual(self.advance(claim), "in_request")
        finally:
            context._finished(None)
        self.assertEqual(self.fake.calls, [])
        self.assertSentNothing(claim)
        self.assertEqual(self.advance(claim, 1), "registered")  # the worker, outside a request

    def test_the_register_call_is_made_from_the_purchase_module_and_nowhere_else(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        callers = sorted(
            str(path.relative_to(root)) for path in root.rglob("*.py")
            if "tests" not in path.parts and re.search(r"\.register\(", path.read_text())
        )
        self.assertEqual(callers, ["purchase.py"])

    def test_a_plain_save_with_a_mismatched_name_on_a_live_claim_fails_closed(self):
        from apps.domains.models import NameMismatch
        claim = self.request()
        claim.name = "other.com"
        with self.assertRaises(NameMismatch):
            claim.save()
        claim.save(allow_name_release=True)  # only the explicit argument skips it
