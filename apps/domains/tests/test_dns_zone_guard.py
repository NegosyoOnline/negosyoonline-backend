"""Platform-zone protection (plan r4.3 item 7): the guard runs before any network call."""
import logging

from django.test import override_settings

from apps.domains import sweeps
from apps.domains.models import DomainRegistration, RegistrationState
from apps.domains.providers.base import Refused
from apps.domains.providers.cloudflare_dns import CloudflareDnsAdapter
from apps.domains.providers.fake import FakeCloudflare
from apps.domains.tests.base import ACCOUNT, PLATFORM, DomainsTestCase, make_bundle


class ZoneGuardTests(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeCloudflare(account_id=ACCOUNT)
        self.adapter = CloudflareDnsAdapter(token="t", account_id=ACCOUNT, transport=self.fake)
        self.mine = self.site()
        self.claim(self.mine, "brand.com")
        for zone in (*PLATFORM, "brand.com", "other.com"):
            self.fake.add_zone(zone)

    def refused(self, site, name):
        before = len(self.fake.calls)
        with self.assertRaises(Refused):
            self.adapter.zone_for(site, name)
        self.assertEqual(len(self.fake.calls), before, "no network call may happen")

    def test_every_platform_zone_is_refused(self):
        for zone in PLATFORM:
            with self.subTest(zone=zone):
                self.refused(self.mine, zone)

    def test_a_name_under_or_over_a_platform_zone_is_refused(self):
        self.refused(self.mine, "shop.negosyonline.com")
        self.refused(self.mine, "com")

    def test_another_sites_registration_is_refused(self):
        other = self.site()
        self.claim(other, "other.com")
        self.refused(self.mine, "other.com")

    def test_a_name_with_no_succeeded_registration_is_refused(self):
        DomainRegistration.objects.filter(site=self.mine).update(state=RegistrationState.CONFLICT)
        self.refused(self.mine, "brand.com")
        self.refused(self.mine, "unregistered.com")

    def test_the_site_own_zone_is_allowed(self):
        self.assertIsNotNone(self.adapter.zone_for(self.mine, "brand.com"))

    @override_settings(DOMAINS_PLATFORM_ZONES=())
    def test_empty_platform_zones_fail_closed_for_every_operation(self):
        with self.assertRaises(Refused):
            self.adapter.zone_for(self.mine, "brand.com")
        with self.assertRaises(Refused):
            self.adapter.list_zone_names()
        self.assertEqual(self.fake.calls, [])

    @override_settings(DOMAINS_PLATFORM_ZONES=())
    def test_the_audit_reports_an_error_and_reads_nothing_with_no_platform_zones(self):
        bundle = make_bundle()
        with self.assertLogs("domains", level=logging.ERROR):
            result = sweeps.zone_audit(bundle)
        self.assertEqual(result, {"error": "platform_zones_unset"})
        self.assertEqual(bundle.fakes["cloudflare"].calls, [])


class ZoneAuditTests(DomainsTestCase):
    def audit(self, bundle):
        with self.assertLogs("domains", level=logging.ERROR) as logs:
            logging.getLogger("domains").error("anchor")
            result = sweeps.zone_audit(bundle)
        return result, [m for m in logs.output if "anchor" not in m]

    def test_quiet_for_customer_zones_and_alerts_on_unknown_ones(self):
        bundle = make_bundle()
        cf = bundle.fakes["cloudflare"]
        self.claim(self.site(), "brand.com")
        cf.add_zone("brand.com")
        cf.add_zone("throwaway-c0.com")
        result, alerts = self.audit(bundle)
        self.assertEqual(result["unknown"], ["throwaway-c0.com"])
        self.assertEqual(len(alerts), 1)
        self.assertIn("throwaway-c0.com", alerts[0])
        self.assertNotIn("brand.com", alerts[0])

    def test_a_zone_between_register_and_succeeded_alerts(self):
        bundle = make_bundle()
        self.claim(self.site(), "pending.com", registration=RegistrationState.SENT)
        bundle.fakes["cloudflare"].add_zone("pending.com")
        result, _ = self.audit(bundle)
        self.assertEqual(result["unknown"], ["pending.com"])

    def test_a_platform_zone_the_token_can_see_alerts(self):
        bundle = make_bundle()
        bundle.fakes["cloudflare"].add_zone("negosyonline.com")
        result, alerts = self.audit(bundle)
        self.assertEqual(result["platform_visible"], ["negosyonline.com"])
        self.assertIn("platform_zone_not_denied", alerts[0])
