"""`served()` second branch, canonical_host, the domain-probe endpoint, and storefront isolation."""
from django.test import override_settings

from apps.billing.tests.base import TRIAL_END, days, make_active
from apps.domains.models import ClaimState, Domain, DomainHostname, DomainRegistration
from apps.domains import hosts
from apps.domains.tests.base import PLATFORM, PSL
from apps.pack import notices, policies
from apps.sites import custom_hosts
from apps.sites.api_public import served, site_for_host
from apps.storefront.tests.base import NOT_FOUND, SurfaceTestCase

BY_HOST = "/v1/public/sites/by-host/"
PROBE = "/storefront/v1/domain-probe/"


@override_settings(DOMAINS_ENABLED=True, DOMAINS_PSL_FILE=PSL, DOMAINS_PLATFORM_ZONES=PLATFORM)
class ServedTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.at(TRIAL_END + days(1))
        self.sub = make_active()
        self.site_ = self.sub.site

    def connect(self, site=None, name="brand.com", state=ClaimState.LIVE, **reg):
        site = site or self.site_
        DomainRegistration.objects.create(site=site, name=name, state="succeeded", register_sent_at=self.clock.now.return_value, send_count=1)
        claim = Domain.objects.create(site=site, name=name, state=state)
        DomainHostname.objects.create(claim=claim, role="www", hostname=f"www.{name}", holding=True)
        DomainHostname.objects.create(claim=claim, role="apex", hostname=name, holding=True)
        return claim

    def served_ids(self, host):
        found = served(host)
        return None if found is None else found[0].public_id

    def test_a_live_domain_serves_its_site(self):
        self.connect()
        self.assertEqual(self.served_ids("www.brand.com"), self.site_.public_id)
        self.assertEqual(self.served_ids("WWW.BRAND.COM:443".split(":")[0]), self.site_.public_id)
        response = self.client.get(BY_HOST, {"host": "www.brand.com"}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["custom_host"], "www.brand.com")
        self.assertEqual(self.call("www.brand.com").json()["id"], self.site_.public_id)

    def test_attention_still_serves(self):
        self.connect(state=ClaimState.ATTENTION)
        self.assertIsNotNone(served("www.brand.com"))

    def test_every_other_state_does_not_serve(self):
        claim = self.connect()
        for state in (ClaimState.CHECKING, ClaimState.CONFIRMED, ClaimState.REGISTERED, ClaimState.ZONE,
                      ClaimState.CONNECTING, ClaimState.ACTIVATING, ClaimState.FAILED, ClaimState.CONFLICT,
                      ClaimState.REMOVAL_PENDING, ClaimState.ENDED):
            with self.subTest(state=state):
                Domain.objects.filter(pk=claim.pk).update(state=state)
                self.assertIsNone(served("www.brand.com"))

    def test_the_apex_is_never_served_by_django(self):
        self.connect()
        self.assertIsNone(served("brand.com"))

    def test_off_when_the_switch_is_off(self):
        self.connect()
        with override_settings(DOMAINS_ENABLED=False):
            self.assertIsNone(served("www.brand.com"))
            self.assertEqual(hosts.canonical_host(self.site_), "")

    def test_a_removed_site_or_one_on_hold_is_not_served(self):
        self.connect()
        self.site_.operator_hold = True
        self.site_.save()
        self.assertIsNone(served("www.brand.com"))

    def test_billing_still_decides(self):
        self.connect()
        from apps.billing.tests.base import CYCLE_1_END
        self.at(CYCLE_1_END + days(9))  # suspended
        self.assertIsNone(served("www.brand.com"))

    def test_a_hostname_that_is_not_the_registered_name_is_refused(self):
        claim = self.connect()
        DomainHostname.objects.filter(claim=claim, role="www").update(hostname="www.forged.com")
        self.assertIsNone(served("www.forged.com"))
        DomainHostname.objects.filter(claim=claim, role="www").update(hostname="www.brand.com")
        Domain.objects.filter(pk=claim.pk).update(name="forged.com")
        self.assertIsNone(served("www.brand.com"))

    def test_a_platform_subdomain_still_works_and_cannot_be_shadowed(self):
        self.assertEqual(self.served_ids("anas-store.negosyotest.com"), self.site_.public_id)
        self.connect()
        self.assertIsNone(served("anas-store.negosyotest.com.evil.com"))

    def test_two_sites_two_domains_never_cross(self):
        other = make_active(email="b@example.test", name="bs-store").site
        self.connect()
        self.connect(site=other, name="other.com")
        self.assertEqual(self.served_ids("www.brand.com"), self.site_.public_id)
        self.assertEqual(self.served_ids("www.other.com"), other.public_id)

    def test_the_manual_custom_host_still_wins_and_is_not_competed_with(self):
        from apps.sites.models import SiteCustomHost
        SiteCustomHost.objects.create(site=self.site_, host="shop.manual.store", state="active")
        self.assertEqual(self.served_ids("shop.manual.store"), self.site_.public_id)
        self.connect()
        self.assertEqual(custom_hosts.public_host(self.site_), "shop.manual.store")

    # --- canonical host and the three emailed links ---------------------------------------

    def test_canonical_host_only_while_live(self):
        self.assertEqual(hosts.canonical_host(self.site_), "")
        claim = self.connect()
        self.assertEqual(hosts.canonical_host(self.site_), "www.brand.com")
        Domain.objects.filter(pk=claim.pk).update(state=ClaimState.CONNECTING)
        self.assertEqual(hosts.canonical_host(self.site_), "")

    def test_the_emailed_links_follow_the_live_domain_and_fall_back_to_the_subdomain(self):
        self.assertEqual(notices.host_for(self.site_), "anas-store.negosyotest.com")
        claim = self.connect()
        self.assertEqual(notices.host_for(self.site_), "www.brand.com")
        self.assertEqual(policies.host_for(self.site_), "www.brand.com")
        self.assertEqual(notices.dashboard_url(self.site_).split("/")[2], "www.brand.com")
        Domain.objects.filter(pk=claim.pk).update(state=ClaimState.FAILED)
        self.assertEqual(notices.host_for(self.site_), "anas-store.negosyotest.com")

    # --- the probe endpoint -----------------------------------------------------------------

    def test_probe_answers_by_domain_state_even_for_a_suspended_site(self):
        claim = self.connect(state=ClaimState.ACTIVATING)
        self.assertEqual(self.call("www.brand.com", path=PROBE).json(), {"site": self.site_.public_id})
        from apps.billing.tests.base import CYCLE_1_END
        self.at(CYCLE_1_END + days(9))
        self.assertEqual(self.call("www.brand.com", path=PROBE).status_code, 200)
        Domain.objects.filter(pk=claim.pk).update(state=ClaimState.CONNECTING)
        self.assert_answer(self.call("www.brand.com", path=PROBE), 404, NOT_FOUND)

    def test_probe_needs_the_server_credential_and_the_switch(self):
        self.connect()
        self.assertEqual(self.call("www.brand.com", path=PROBE, secret=None).status_code, 401)
        self.assertEqual(self.call("www.brand.com", path=PROBE, secret="wrong").status_code, 401)
        with override_settings(DOMAINS_ENABLED=False):
            self.assert_answer(self.call("www.brand.com", path=PROBE), 404, NOT_FOUND)

    def test_probe_answers_nothing_private(self):
        self.connect()
        body = self.call("www.brand.com", path=PROBE).json()
        self.assertEqual(set(body), {"site"})
