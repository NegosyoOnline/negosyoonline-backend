"""The Resend adapter: five calls, every guard before any network, answers reduced. No network."""
import logging

from django.test import override_settings

from apps.domains.models import Domain
from apps.domains.providers.base import NotConfigured, Paused, ProviderError, Refused
from apps.domains.providers.fake import FakeResend
from apps.domains.providers.resend_domains import ResendDomainsAdapter
from apps.domains.tests.base import DomainsTestCase

TOKEN = "re_super_secret_key_value"


class ResendAdapterTests(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeResend()
        self.adapter = ResendDomainsAdapter(token=TOKEN, region="us-east-1", transport=self.fake)
        self.site_ = self.site()
        self.claim_ = self.claim(self.site_, "brand.com")

    def made(self):
        data = self.adapter.create(self.site_, "brand.com")
        Domain.objects.filter(pk=self.claim_.pk).update(resend_domain_id=data["id"])
        return data

    def test_the_five_calls_and_nothing_else(self):
        data = self.made()
        self.adapter.get(data["id"])
        self.adapter.verify(data["id"])
        self.adapter.list()
        self.adapter.delete(self.site_, data["id"], name="brand.com")
        did = data["id"]
        self.assertEqual(self.fake.calls, [
            ("POST", "/domains"), ("GET", f"/domains/{did}"), ("POST", f"/domains/{did}/verify"),
            ("GET", "/domains"), ("GET", f"/domains/{did}"), ("DELETE", f"/domains/{did}"),
        ])
        for method, path in (("POST", "/emails"), ("GET", "/api-keys"), ("PATCH", f"/domains/{did}"),
                             ("POST", "/domains/x/y"), ("DELETE", "/domains")):
            with self.subTest(method=method, path=path):
                with self.assertRaises(Refused):
                    self.adapter._call(method, path)
        self.assertEqual(len(self.fake.calls), 6)

    def test_create_body_is_exactly_name_region_and_sending_only(self):
        self.adapter.create(self.site_, "brand.com")
        self.assertEqual(self.fake.requests[0][2], {
            "name": "brand.com", "region": "us-east-1",
            "capabilities": {"sending": "enabled", "receiving": "disabled"},
        })

    def test_create_refuses_before_any_call(self):
        other = self.site()
        self.claim(other, "other.com")
        for name in ("other.com", "evil.com", "negosyonline.com", "mail.negosyonline.com", "BRAND.COM", "", None):
            with self.subTest(name=name):
                with self.assertRaises(Refused):
                    self.adapter.create(self.site_, name)
        self.assertEqual(self.fake.calls, [])

    def test_a_site_with_no_succeeded_registration_creates_nothing(self):
        bare = self.site()
        with self.assertRaises(Refused):
            self.adapter.create(bare, "brand.com")
        self.assertEqual(self.fake.calls, [])

    def test_unset_key_region_platform_zones_and_pause_refuse_first(self):
        cases = (
            (ResendDomainsAdapter(token="", region="us-east-1", transport=self.fake), {}, NotConfigured),
            (ResendDomainsAdapter(token=TOKEN, region="", transport=self.fake), {}, NotConfigured),
            (self.adapter, {"DOMAINS_PLATFORM_ZONES": ()}, Refused),
            (self.adapter, {"DOMAINS_PROVIDER_PAUSED": True}, Paused),
        )
        for adapter, extra, error in cases:
            with self.subTest(extra=extra, error=error), override_settings(**extra):
                with self.assertRaises(error):
                    adapter.create(self.site_, "brand.com")
        self.assertEqual(self.fake.calls, [])

    def test_delete_reads_first_and_needs_the_claims_name(self):
        data = self.made()
        self.fake.domains[data["id"]]["name"] = "someone-else.com"
        with self.assertRaises(Refused):
            self.adapter.delete(self.site_, data["id"], name="brand.com")
        self.assertEqual([m for m, _ in self.fake.calls], ["POST", "GET"])
        self.assertIn(data["id"], self.fake.domains)

    def test_delete_takes_only_a_recorded_id_of_this_site(self):
        data = self.adapter.create(self.site_, "brand.com")  # not recorded on any claim
        with self.assertRaises(Refused):
            self.adapter.delete(self.site_, data["id"], name="brand.com")
        other = self.site()
        self.claim(other, "other.com")
        with self.assertRaises(Refused):
            self.adapter.delete(other, data["id"], name="other.com")
        for bad in ("../x", "", None, "a b"):
            with self.assertRaises(Refused):
                self.adapter.delete(self.site_, bad, name="brand.com")
        self.assertNotIn("DELETE", [m for m, _ in self.fake.calls])

    def test_the_answer_is_reduced_to_whitelisted_fields(self):
        data = self.made()
        self.fake.domains[data["id"]]["api_key"] = "re_leak"
        self.fake.domains[data["id"]]["records"][0]["secret"] = "x"
        got = self.adapter.get(data["id"])
        self.assertEqual(set(got), {"id", "name", "status", "region", "records"})
        self.assertEqual(set(got["records"][0]), {"record", "name", "type", "value", "priority", "ttl", "status"})
        self.assertNotIn("re_leak", repr(got))

    def test_errors_are_mapped_and_the_key_is_never_shown(self):
        for status, code in ((401, "auth"), (403, "auth"), (404, "not_found"), (409, "conflict"),
                             (429, "rate_limited"), (500, "unavailable")):
            self.fake.fail_next(status)
            with self.subTest(status=status), self.assertLogs("domains", level=logging.ERROR) as logs:
                logging.getLogger("domains").error("anchor")
                with self.assertRaises(ProviderError) as ctx:
                    self.adapter.create(self.site_, "brand.com")
            self.assertEqual(ctx.exception.code, code)
            self.assertNotIn(TOKEN, str(ctx.exception) + repr(ctx.exception) + repr(self.adapter) + "".join(logs.output))
