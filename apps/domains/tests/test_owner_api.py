"""The Subscriber's Domain routes: gate, Check, Buy (writes rows only), re-attach, retry, status.
Drives the real routes with a signed-in owner; the worker side runs through `purchase.advance`
over the fake Registrar. No network."""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Subscription
from apps.billing.tests.base import method_on_file
from apps.domains import machine, purchase
from apps.domains.models import ClaimState, Domain, DomainEvent, DomainRegistration, RegistrationState
from apps.domains.tests.base import DOMAIN_SETTINGS, make_bundle
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase


@override_settings(**DOMAIN_SETTINGS)
class OwnerDomainTestCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.bundle = make_bundle()
        self.fake = self.bundle.fakes["registrar"]
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.path = f"/v1/sites/{self.site_id}/domain/"
        purchase.sentinel(self.bundle, timezone.now())  # the offered list: .com and .net
        self.fake.calls.clear()

    def pay(self, site=None):
        subscription = Subscription.objects.get(site=site or self.site)
        with method_on_file():
            billing.first_payment_succeeded(
                subscription, paid_at=timezone.now(), reference=f"first-{subscription.pk}", early=True
            )

    def check(self, name="brand.com", **kw):
        return self.post(self.path + "check/", {"name": name}, **kw)

    def buy(self, name="brand.com", **kw):
        return self.post(self.path + "buy/", {"name": name}, **kw)

    def claim(self):
        return Domain.objects.filter(site=self.site).exclude(state=ClaimState.ENDED).first()

    def advance(self, minutes=0):
        claim = Domain.objects.filter(site=self.site).order_by("-pk").first()
        return purchase.advance(claim.pk, providers=self.bundle, now=timezone.now() + timedelta(minutes=minutes))

    def quoted(self, name="brand.com"):
        self.pay()
        self.assertEqual(self.check(name).status_code, 200)
        self.assertEqual(self.advance(), "quoted")


class OffTests(OwnerDomainTestCase):
    @override_settings(DOMAINS_ENABLED=False)
    def test_every_route_is_a_404_while_domains_are_off_signed_in_or_not(self):
        self.pay()
        for client in (self.client, Client()):
            for method, suffix in (("get", ""), ("post", "check/"), ("post", "buy/"), ("post", "retry/")):
                response = getattr(client, method)(self.path + suffix, secure=True)
                self.assertEqual(response.status_code, 404, (method, suffix))
        self.assertFalse(DomainRegistration.objects.exists())


class GateTests(OwnerDomainTestCase):
    def test_a_trial_sees_it_locked_and_can_neither_check_nor_buy(self):
        got = self.get(self.path).json()
        self.assertFalse(got["eligible"])
        self.assertIn("first payment", got["locked_reason"])
        for response in (self.check(), self.buy(), self.post(self.path + "retry/", {})):
            self.error(response, 409, "not_eligible")
        self.assertFalse(DomainRegistration.objects.exists())
        self.assertEqual(self.fake.calls, [])

    def test_the_first_confirmed_payment_unlocks_it_and_the_answer_holds_no_price(self):
        self.pay()
        got = self.get(self.path)
        self.assertTrue(got.json()["eligible"])
        self.assertEqual(got.json()["extensions"], [".com", ".net"])
        self.assertEqual(got["Cache-Control"], "no-store")
        text = got.content.decode().lower()
        for word in ("cost", "price", "cents", "usd", "cloudflare", "token"):
            self.assertNotIn(word, text)

    def test_suspended_and_held_sites_are_refused(self):
        self.pay()
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)
        self.error(self.check(), 409, "not_eligible")
        Site.objects.filter(pk=self.site.pk).update(operator_hold=False)
        Subscription.objects.filter(site=self.site).update(state="suspended")
        self.error(self.check(), 403, "billing_only")


class CheckTests(OwnerDomainTestCase):
    def test_check_writes_a_claim_and_a_name_less_registration_and_never_buys(self):
        self.pay()
        got = self.check().json()
        self.assertEqual(got["domain"]["state"], "checking")
        reg = DomainRegistration.objects.get(site=self.site)
        self.assertEqual((reg.name, reg.state), (None, RegistrationState.INTENT))
        self.assertEqual(self.fake.calls, [])  # the route calls no provider
        self.assertEqual(self.advance(), "quoted")
        reg.refresh_from_db()
        self.assertEqual((reg.name, reg.check_name, reg.check_registration_cost_cents), (None, "brand.com", 857))
        got = self.get(self.path).json()["domain"]
        self.assertEqual((got["state"], got["can_buy"]), ("quoted", True))
        self.assertEqual(self.fake.register_calls, 0)

    def test_refused_names(self):
        self.pay()
        for name, word in (("negosyonline.com", "NegosyoOnline"), ("sub.brand.com", "main domain"),
                           ("www.brand.com", "main domain"), ("brand.org", "extension"), ("bad name", "bare name"),
                           ("http://brand.com", "bare"), ("com", "dotted")):
            with self.subTest(name=name):
                body = self.error(self.check(name), 400, "invalid_request")
                self.assertIn(word, body["fields"]["name"][0])
        self.assertFalse(Domain.objects.exists())

    def test_a_taken_name_ends_the_claim_with_a_plain_reason(self):
        self.pay()
        self.fake.taken.add("brand.com")
        self.check()
        self.assertEqual(self.advance(), "refused")
        got = self.get(self.path).json()["domain"]
        self.assertEqual((got["state"], got["reason"]), ("ended", "That name is not available. Please choose another."))

    def test_a_new_check_replaces_a_quote(self):
        self.quoted("one.com")
        self.assertEqual(self.check("two.com").status_code, 200)
        self.assertEqual((self.claim().name, self.claim().state), ("two.com", ClaimState.CHECKING))
        self.assertEqual(DomainRegistration.objects.get(site=self.site).check_name, "")

    def test_check_rate_limits_per_site_and_per_account(self):
        self.pay()
        with override_settings(DOMAINS_CHECKS_PER_HOUR_SITE=2):
            self.assertEqual(self.check().status_code, 200)
            self.assertEqual(self.check().status_code, 200)
            self.error(self.check(), 429, "rate_limited")
        cache.clear()
        with override_settings(DOMAINS_CHECKS_PER_HOUR_ACCOUNT=1):
            self.assertEqual(self.check().status_code, 200)
            self.error(self.check(), 429, "rate_limited")

    def test_an_expired_quote_ends_the_claim(self):
        self.quoted()
        self.assertEqual(self.advance(minutes=16), "expired")
        self.assertEqual(self.get(self.path).json()["domain"]["state"], "ended")


class BuyTests(OwnerDomainTestCase):
    def test_buy_only_writes_rows_and_never_calls_a_provider_even_with_eager_tasks(self):
        self.quoted()
        calls = len(self.fake.calls)
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            got = self.buy().json()
        self.assertEqual(got["domain"]["state"], "confirmed")
        reg = DomainRegistration.objects.get(site=self.site)
        self.assertEqual((reg.name, reg.state, reg.check_name, reg.send_count), ("brand.com", RegistrationState.INTENT, "", 0))
        self.assertEqual((len(self.fake.calls), self.fake.register_calls), (calls, 0))
        event = DomainEvent.objects.get(kind="buy")
        self.assertEqual((event.actor_type, event.actor_id, event.detail["name"]), ("subscriber", self.account.pk, "brand.com"))
        self.assertEqual(event.detail["shown_registration_cents"], 857)
        # the worker then buys it, once
        self.assertEqual(self.advance(), "registered")
        self.assertEqual(self.fake.register_calls, 1)

    def test_buy_needs_a_check_first_and_a_live_quote(self):
        self.pay()
        self.error(self.buy(), 409, "check_first")
        self.check()
        self.error(self.buy(), 409, "check_first")  # still `checking`
        self.advance()
        self.error(self.buy("other.com"), 409, "check_first")
        self.assertEqual(self.buy().status_code, 200)

    def test_an_expired_quote_cannot_be_bought(self):
        self.quoted()
        Domain.objects.filter(site=self.site).update(quote_expires_at=timezone.now() - timedelta(seconds=1))
        self.error(self.buy(), 409, "quote_expired")

    @override_settings(DOMAINS_PURCHASE_ENABLED=False)
    def test_a_buy_that_could_lead_to_a_purchase_is_refused_while_the_switch_is_off(self):
        self.quoted()
        self.error(self.buy(), 409, "purchase_not_open")
        self.assertIsNone(DomainRegistration.objects.get(site=self.site).name)

    def test_two_sites_for_one_name_the_second_is_told_it_is_taken(self):
        self.quoted()
        self.buy()
        other = self.other_client()
        other_id = self.create_site(client=other, subdomain="bens-store", display_name="Ben's")["id"]
        other_site = Site.objects.get(public_id=other_id)
        self.pay(other_site)
        other_path = f"/v1/sites/{other_id}/domain/"
        self.post(other_path + "check/", {"name": "brand.com"}, client=other)
        claim = Domain.objects.get(site=other_site)
        purchase.advance(claim.pk, providers=self.bundle, now=timezone.now())
        self.error(self.post(other_path + "buy/", {"name": "brand.com"}, client=other), 409, "name_taken")
        self.assertEqual(DomainRegistration.objects.filter(name="brand.com").count(), 1)
        self.assertEqual(self.fake.register_calls, 0)

    def test_a_confirmed_request_can_be_renamed_in_place_before_anything_is_sent(self):
        self.quoted()
        self.buy()
        self.assertEqual(self.buy("other.com").status_code, 200)
        reg = DomainRegistration.objects.get(site=self.site)
        self.assertEqual((reg.name, self.claim().name, self.claim().state), ("other.com", "other.com", ClaimState.CONFIRMED))
        self.assertEqual(self.advance(), "registered")
        self.assertEqual(self.fake.register_bodies, [{"domain_name": "other.com"}])

    def test_a_site_that_has_sent_cannot_start_another_request(self):
        self.quoted()
        self.buy()
        self.fake.register_mode = "409"
        self.assertEqual(self.advance(), "conflict")
        Domain.objects.filter(site=self.site).update(state=ClaimState.ENDED)
        for response in (self.check("again.com"), self.buy("again.com"), self.buy()):
            self.error(response, 409, "domain_in_progress")
        self.assertEqual(self.fake.register_calls, 1)


class ReattachTests(OwnerDomainTestCase):
    def registered(self):
        """A site whose registration succeeded long ago and whose claim ended."""
        self.pay()
        reg = DomainRegistration.objects.create(
            site=self.site, name="brand.com", state=RegistrationState.SUCCEEDED,
            register_sent_at=timezone.now(), send_count=1,
        )
        Domain.objects.create(site=self.site, name="brand.com", state=ClaimState.ENDED)
        return reg

    @override_settings(DOMAINS_PURCHASE_ENABLED=False, DOMAINS_DAILY_PURCHASE_CAP=0, DOMAINS_DAILY_SPEND_CAP_CENTS=0,
                       DOMAINS_MAX_COST_CENTS=0, DOMAINS_ALLOWED_TLDS=())
    def test_a_reattach_is_not_a_purchase_it_works_with_the_switch_and_caps_off(self):
        self.registered()
        self.assertEqual(self.check().status_code, 200)  # no provider call: a quote of our own name
        got = self.buy().json()
        self.assertEqual(got["domain"]["state"], "registered")
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(DomainEvent.objects.filter(kind="register_sent").count(), 0)
        self.assertEqual(DomainEvent.objects.get(kind="reattach").detail, {"name": "brand.com"})

    @override_settings(DOMAINS_PURCHASE_ENABLED=False)
    def test_a_buy_for_any_other_name_is_still_refused_and_registers_nothing(self):
        self.registered()
        for name in ("other.com", "brand.net"):
            self.error(self.buy(name), 409, "domain_exists")
        self.assertEqual(self.fake.register_calls, 0)

    def test_the_gate_is_read_at_buy_and_in_the_worker(self):
        self.registered()
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)
        self.error(self.buy(), 409, "not_eligible")
        Site.objects.filter(pk=self.site.pk).update(operator_hold=False)
        self.buy()
        claim = self.claim()
        for label, change in (("hold", lambda: Site.objects.filter(pk=self.site.pk).update(operator_hold=True)),
                              ("suspended", lambda: Subscription.objects.filter(site=self.site).update(state="suspended")),
                              ("cancelled", lambda: Subscription.objects.filter(site=self.site).update(
                                  state="cancelled", service_ended_at=timezone.now() - timedelta(days=1)))):
            with self.subTest(label):
                Site.objects.filter(pk=self.site.pk).update(operator_hold=False)
                Subscription.objects.filter(site=self.site).update(state="active", service_ended_at=None)
                change()
                self.assertEqual(machine.advance(claim.pk, providers=self.bundle, now=timezone.now()), "site_off")
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.bundle.fakes["vercel"].calls, [])


class RetryTests(OwnerDomainTestCase):
    def test_retry_goes_back_to_the_setup_steps_only_and_never_registers(self):
        self.pay()
        DomainRegistration.objects.create(site=self.site, name="brand.com", state=RegistrationState.SUCCEEDED,
                                          register_sent_at=timezone.now(), send_count=1)
        Domain.objects.create(site=self.site, name="brand.com", state=ClaimState.FAILED, reason="Connecting took too long.")
        got = self.post(self.path + "retry/", {}).json()
        self.assertEqual(got["domain"]["state"], "zone")
        self.assertEqual(self.fake.register_calls, 0)
        self.error(self.post(self.path + "retry/", {}), 409, "nothing_to_retry")

    def test_a_site_with_nothing_failed_has_nothing_to_retry(self):
        self.pay()
        self.error(self.post(self.path + "retry/", {}), 409, "nothing_to_retry")


class OnePerSiteForLifeTests(OwnerDomainTestCase):
    def test_removal_then_buy_registers_nothing_and_there_is_one_post_per_site_ever(self):
        self.quoted()
        self.buy()
        self.assertEqual(self.advance(), "registered")
        claim = self.claim()
        self.assertEqual(self.fake.register_calls, 1)
        Domain.objects.filter(pk=claim.pk).update(state=ClaimState.LIVE)
        machine.request_removal(Domain.objects.get(pk=claim.pk), reason="operator_disconnect")
        self.assertEqual(machine.advance(claim.pk, providers=self.bundle, now=timezone.now()), "ended")
        self.assertIsNone(self.claim())
        # the owner comes back: the same name re-attaches, anything else is refused
        self.error(self.buy("other.com"), 409, "domain_exists")
        self.assertEqual(self.check().status_code, 200)
        self.assertEqual(self.buy().json()["domain"]["state"], "registered")
        self.assertEqual(self.fake.register_calls, 1)
        self.assertEqual(DomainEvent.objects.filter(kind="register_sent").count(), 1)
