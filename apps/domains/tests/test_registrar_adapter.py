"""The Registrar adapter: its four calls, the register body pin, and every refusal before the
network. A fake transport under the real adapter; no network."""
import logging
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone

from apps.domains import context
from apps.domains.models import DomainRegistration
from apps.domains.providers.base import NotConfigured, Paused, ProviderError, Refused
from apps.domains.providers.cloudflare_registrar import RegistrarAdapter, cents
from apps.domains.providers.fake import FakeRegistrar
from apps.domains.tests.base import ACCOUNT, DomainsTestCase

TOKEN = "registrar-secret-token-value"
ROOT = f"/accounts/{ACCOUNT}/registrar"


class RegistrarAdapterTests(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeRegistrar(account_id=ACCOUNT)
        self.adapter = RegistrarAdapter(token=TOKEN, account_id=ACCOUNT, transport=self.fake)
        self.site_ = self.site(paid=True)

    def sent_row(self, name="brand.com", state="sent"):
        extra = {"register_sent_at": timezone.now(), "send_count": 1} if state != "intent" else {}
        return DomainRegistration.objects.create(site=self.site_, name=name, state=state, **extra)

    def test_check_reads_prices_and_posts_only_the_names(self):
        rows = self.adapter.check(["brand.com"])
        self.assertEqual(self.fake.calls, [("POST", f"{ROOT}/domain-check")])
        self.assertEqual(self.fake.requests[0][2], {"domains": ["brand.com"]})
        self.assertEqual(rows, [{
            "name": "brand.com", "registrable": True, "tier": "standard", "registration_cost_cents": 857,
            "renewal_cost_cents": 857, "currency": "USD",
        }])

    def test_check_refuses_more_than_twenty_names_and_bad_names(self):
        for names in ([], [f"a{i}.com" for i in range(21)], ["Brand.com"], ["a/b.com"], [None]):
            with self.subTest(names=names), self.assertRaises(Refused):
                self.adapter.check(names)
        self.assertEqual(self.fake.calls, [])

    def test_register_body_is_exactly_the_domain_name(self):
        self.sent_row()
        self.assertEqual(self.adapter.register("brand.com"), {"state": "succeeded"})
        self.assertEqual(self.fake.calls, [("POST", f"{ROOT}/registrations")])
        self.assertEqual(self.fake.register_bodies, [{"domain_name": "brand.com"}])

    @override_settings(DOMAINS_REGISTER_AUTO_RENEW=True)
    def test_auto_renew_is_added_only_when_step_zero_enabled_it(self):
        self.sent_row()
        self.adapter.register("brand.com")
        self.assertEqual(self.fake.register_bodies, [{"domain_name": "brand.com", "auto_renew": True}])

    def test_202_is_in_progress(self):
        self.sent_row()
        self.fake.register_mode = "202"
        self.assertEqual(self.adapter.register("brand.com"), {"state": "in_progress"})

    def test_register_refuses_a_name_that_has_not_been_marked_sent_by_step_one(self):
        self.sent_row(state="intent")
        for name in ("brand.com", "other.com", "BRAND.com", "", None):
            with self.subTest(name=name), self.assertRaises(Refused):
                self.adapter.register(name)
        self.assertEqual(self.fake.calls, [])

    def test_register_refuses_inside_a_web_request(self):
        self.sent_row()
        context._started(None)
        try:
            with self.assertRaises(Refused) as caught:
                self.adapter.register("brand.com")
            self.assertEqual(caught.exception.code, "in_request")
        finally:
            context._finished(None)
        self.assertEqual(self.fake.calls, [])
        self.adapter.register("brand.com")  # outside a request it goes through

    def test_register_needs_the_worker_only_token(self):
        self.sent_row()
        with override_settings(CLOUDFLARE_REGISTRAR_API_TOKEN=""):
            with self.assertRaises(NotConfigured):
                RegistrarAdapter(account_id=ACCOUNT, transport=self.fake).register("brand.com")
        self.assertEqual(self.fake.calls, [])

    @override_settings(DOMAINS_PROVIDER_PAUSED=True)
    def test_the_kill_switch_stops_every_call_including_register(self):
        self.sent_row()
        for call in (lambda: self.adapter.register("brand.com"), lambda: self.adapter.check(["a.com"]),
                     lambda: self.adapter.status("brand.com"), lambda: self.adapter.get("brand.com")):
            with self.assertRaises(Paused):
                call()
        self.assertEqual(self.fake.calls, [])

    def test_status_and_get_only_for_names_of_our_own_rows(self):
        self.sent_row()
        for call in (self.adapter.status, self.adapter.get):
            with self.assertRaises(Refused):
                call("unknown.com")
        self.assertEqual(self.fake.calls, [])
        self.fake.registrations["brand.com"] = {"expires_at": "2027-11-22T00:00:00Z", "auto_renew": True,
                                                "account_id": ACCOUNT}
        self.assertEqual(self.adapter.status("brand.com"), {"state": "succeeded", "code": ""})
        got = self.adapter.get("brand.com")
        self.assertEqual((got["name"], got["auto_renew"]), ("brand.com", True))
        self.assertEqual(got["expires_at"].year, 2027)

    def test_get_of_an_unregistered_name_is_none_and_another_account_is_refused(self):
        self.sent_row()
        self.assertIsNone(self.adapter.get("brand.com"))
        self.fake.registrations["brand.com"] = {"expires_at": None, "auto_renew": None, "account_id": "acct_other"}
        with self.assertRaises(Refused):
            self.adapter.get("brand.com")

    def test_no_other_call_exists_on_the_allowlist(self):
        for method, path in (("POST", f"{ROOT}/registrations/brand.com/renew"), ("DELETE", f"{ROOT}/registrations/brand.com"),
                             ("GET", f"{ROOT}/registrations"), ("POST", f"{ROOT}/transfer"),
                             ("PUT", f"{ROOT}/registrations/brand.com"), ("POST", "/zones")):
            with self.subTest(path=path), self.assertRaises(Refused):
                self.adapter._call(method, path)
        self.assertEqual(self.fake.calls, [])

    def test_error_maps(self):
        self.sent_row()
        for status, code, retryable in ((401, "auth", False), (403, "auth", False), (409, "conflict", False),
                                        (429, "rate_limited", True), (500, "unavailable", True)):
            with self.subTest(status=status):
                self.fake.fail_next(status)
                with self.assertRaises(ProviderError) as caught:
                    self.adapter.status("brand.com")
                self.assertEqual((caught.exception.code, caught.exception.retryable), (code, retryable))

    def test_the_token_is_never_in_an_error_a_repr_or_a_log(self):
        self.sent_row()
        self.fake.fail_next(403)
        with self.assertLogs(level=logging.DEBUG) as logs, self.assertRaises(ProviderError) as caught:
            logging.getLogger("x").debug("start")
            self.adapter.status("brand.com")
        self.assertNotIn(TOKEN, str(caught.exception) + repr(caught.exception) + repr(self.adapter) + " ".join(logs.output))

    def test_money_is_parsed_to_cents_rounding_up(self):
        self.assertEqual([cents(v) for v in ("8.57", 12, "12.001", "0", None, "x", "-1", True, "NaN")],
                         [857, 1200, 1201, 0, None, None, None, None, None])
        self.assertEqual(cents(Decimal("9.10")), 910)

    def test_status_words_are_whitelisted(self):
        self.sent_row()
        self.fake.statuses["brand.com"] = ["weird"]
        self.assertEqual(self.adapter.status("brand.com")["state"], "unknown")
