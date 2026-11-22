"""Allowlists and body pins, one adapter at a time, over mocked transports. No network."""
import json
import logging

from django.test import SimpleTestCase, override_settings

from apps.domains.providers.base import NotConfigured, Paused, ProviderError, Refused, default_transport
from apps.domains.providers.cloudflare_dns import CloudflareDnsAdapter
from apps.domains.providers.fake import FakeCloudflare, FakeVercel
from apps.domains.providers.vercel import VercelAdapter
from apps.domains.tests.base import ACCOUNT, PLATFORM, PROJECT, DomainsTestCase

TOKEN = "super-secret-token-value"
ALLOWED = frozenset({"www.brand.com", "brand.com"})


def vercel(fake=None, **kw):
    fake = fake or FakeVercel(project_id=PROJECT)
    return VercelAdapter(token=TOKEN, project_id=PROJECT, transport=fake, **kw), fake


class VercelAdapterTests(SimpleTestCase):
    def test_the_six_calls_and_the_pinned_project(self):
        adapter, fake = vercel()
        adapter.add("www.brand.com", allowed=ALLOWED)
        adapter.get("www.brand.com", allowed=ALLOWED)
        adapter.verify("www.brand.com", allowed=ALLOWED)
        adapter.config("www.brand.com", allowed=ALLOWED)
        adapter.list()
        adapter.remove("www.brand.com", allowed=ALLOWED)
        self.assertEqual(
            fake.calls,
            [("POST", f"/v10/projects/{PROJECT}/domains"), ("GET", f"/v9/projects/{PROJECT}/domains/www.brand.com"),
             ("POST", f"/v9/projects/{PROJECT}/domains/www.brand.com/verify"),
             ("GET", "/v6/domains/www.brand.com/config"), ("GET", f"/v9/projects/{PROJECT}/domains"),
             ("DELETE", f"/v9/projects/{PROJECT}/domains/www.brand.com")],
        )

    def test_a_name_that_is_not_the_claims_is_refused_before_any_call(self):
        adapter, fake = vercel()
        for name in ("evil.com", "www.other.com", "BRAND.COM", "brand.com/../x", "", None):
            with self.subTest(name=name):
                with self.assertRaises(Refused):
                    adapter.add(name, allowed=ALLOWED)
        self.assertEqual(fake.calls, [])

    def test_redirect_only_apex_to_www_with_308(self):
        adapter, fake = vercel()
        adapter.add("brand.com", allowed=ALLOWED, redirect_to="www.brand.com")
        self.assertEqual(fake.requests[0][2], {"name": "brand.com", "redirect": "www.brand.com", "redirectStatusCode": 308})
        for name, target in (("brand.com", "evil.com"), ("www.brand.com", "www.www.brand.com"), ("www.brand.com", "brand.com")):
            with self.subTest(name=name, target=target):
                with self.assertRaises(Refused):
                    adapter.add(name, allowed=ALLOWED | {"evil.com"}, redirect_to=target)
        self.assertEqual(len(fake.calls), 1)

    def test_add_body_is_exactly_the_name(self):
        adapter, fake = vercel()
        adapter.add("www.brand.com", allowed=ALLOWED)
        self.assertEqual(fake.requests[0][2], {"name": "brand.com".join(["www.", ""])})

    def test_the_project_id_is_pinned_from_settings_not_a_parameter(self):
        with override_settings(VERCEL_STOREFRONTS_PROJECT_ID="prj_from_settings", VERCEL_API_TOKEN=TOKEN):
            adapter = VercelAdapter(transport=FakeVercel(project_id="prj_from_settings"))
            self.assertEqual(adapter.project_id, "prj_from_settings")
            adapter.list()
        with override_settings(VERCEL_STOREFRONTS_PROJECT_ID="", VERCEL_API_TOKEN=TOKEN):
            fake = FakeVercel()
            with self.assertRaises(NotConfigured):
                VercelAdapter(transport=fake).list()
            self.assertEqual(fake.calls, [])

    def test_a_call_off_the_allowlist_raises_before_the_network(self):
        adapter, fake = vercel()
        with self.assertRaises(Refused):
            adapter._call("POST", "/v1/teams")
        with self.assertRaises(Refused):
            adapter._call("DELETE", f"/v9/projects/{PROJECT}")
        self.assertEqual(fake.calls, [])

    def test_no_token_means_no_call(self):
        fake = FakeVercel(project_id=PROJECT)
        with override_settings(VERCEL_API_TOKEN=""):
            with self.assertRaises(NotConfigured):
                VercelAdapter(project_id=PROJECT, transport=fake).list()
        self.assertEqual(fake.calls, [])

    @override_settings(DOMAINS_PROVIDER_PAUSED=True)
    def test_the_kill_switch_stops_every_call(self):
        adapter, fake = vercel()
        with self.assertRaises(Paused):
            adapter.list()
        with self.assertRaises(Paused):
            adapter.add("www.brand.com", allowed=ALLOWED)
        self.assertEqual(fake.calls, [])

    def test_records_come_from_the_answer(self):
        adapter, fake = vercel()
        data = adapter.config("www.brand.com", allowed=ALLOWED)
        self.assertEqual({r["type"] for r in data["records"]}, {"CNAME", "A"})
        self.assertIn(FakeVercel.CNAME, [r["content"] for r in data["records"]])

    def test_error_maps(self):
        for status, code, retryable in ((400, "bad_request", False), (402, "payment_required", False),
                                        (403, "auth", False), (409, "conflict", False),
                                        (429, "rate_limited", True), (503, "unavailable", True)):
            with self.subTest(status=status):
                adapter, fake = vercel()
                fake.fail_next(status)
                with self.assertRaises(ProviderError) as ctx:
                    adapter.list()
                self.assertEqual((ctx.exception.code, ctx.exception.retryable), (code, retryable))

    def test_the_token_is_never_in_an_error_a_repr_or_a_log(self):
        adapter, fake = vercel()
        fake.fail_next(403)
        with self.assertLogs(level=logging.DEBUG) as logs, self.assertRaises(ProviderError) as ctx:
            logging.getLogger("x").debug("start")
            adapter.list()
        self.assertNotIn(TOKEN, str(ctx.exception) + repr(ctx.exception) + repr(adapter) + " ".join(logs.output))

    def test_the_default_transport_refuses_plain_http(self):
        with self.assertRaises(ProviderError):
            default_transport("GET", "http://api.vercel.com/x", {}, None)


class CloudflareAdapterTests(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeCloudflare(account_id=ACCOUNT)
        self.adapter = CloudflareDnsAdapter(token=TOKEN, account_id=ACCOUNT, transport=self.fake)
        self.site_ = self.site()
        self.claim(self.site_, "brand.com")
        self.zone_id = self.fake.add_zone("brand.com")

    def zone(self):
        return self.adapter.zone_for(self.site_, "brand.com")

    def test_zone_read_by_exact_name_and_account(self):
        zone = self.zone()
        self.assertEqual((zone.id, zone.name), (self.zone_id, "brand.com"))
        self.assertEqual(self.fake.calls, [("GET", "/zones")])

    def test_a_zone_of_another_account_is_not_accepted(self):
        self.fake.zones["brand.com"]["account"]["id"] = "someone-else"
        self.assertIsNone(self.zone())

    def test_a_missing_zone_is_none(self):
        del self.fake.zones["brand.com"]
        self.assertIsNone(self.zone())

    def test_record_create_pins(self):
        zone = self.zone()
        made = self.adapter.create_record(zone, type="CNAME", name="www.brand.com", content="x.vercel-dns.example")
        self.assertEqual(self.fake.requests[-1][2], {"type": "CNAME", "name": "www.brand.com",
                                                      "content": "x.vercel-dns.example", "ttl": 1, "proxied": False})
        self.assertTrue(made["id"])
        for kwargs in (dict(type="SRV"), dict(type="A", name="evil.com"), dict(type="TXT", name="notbrand.com"),
                       dict(type="A", content="")):
            with self.subTest(kwargs=kwargs):
                args = dict(type="A", name="brand.com", content="203.0.113.1") | kwargs
                before = len(self.fake.calls)
                with self.assertRaises(Refused):
                    self.adapter.create_record(zone, **args)
                self.assertEqual(len(self.fake.calls), before)

    def test_proxied_is_always_false(self):
        zone = self.zone()
        self.adapter.create_record(zone, type="A", name="brand.com", content="203.0.113.1")
        self.assertIs(self.fake.requests[-1][2]["proxied"], False)

    def test_delete_only_a_recorded_id_in_its_recorded_zone(self):
        from apps.domains.models import Domain, DomainDnsRecord
        zone = self.zone()
        made = self.adapter.create_record(zone, type="A", name="brand.com", content="203.0.113.1")
        with self.assertRaises(Refused):  # not recorded
            self.adapter.delete_record(zone, made["id"])
        DomainDnsRecord.objects.create(
            claim=Domain.objects.get(site=self.site_), purpose="vercel", zone_id=zone.id,
            record_id=made["id"], record_type="A", name="brand.com", content="203.0.113.1", ttl=1,
        )
        self.adapter.delete_record(zone, made["id"])
        self.assertEqual(self.fake.calls[-1][0], "DELETE")

    def test_a_forged_zone_handle_is_refused(self):
        from apps.domains.providers.cloudflare_dns import Zone
        with self.assertRaises(Refused):
            self.adapter.list_records(Zone(id=self.zone_id, name="brand.com"))
        self.assertEqual(self.fake.calls, [])

    def test_a_call_off_the_allowlist_raises(self):
        with self.assertRaises(Refused):
            self.adapter._call("POST", "/zones")  # no zone creation
        with self.assertRaises(Refused):
            self.adapter._call("DELETE", f"/zones/{self.zone_id}")
