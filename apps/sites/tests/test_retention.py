import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.sites import custom_hosts, retention
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, SiteCustomHost, Subdomain
from apps.sites.tests.base import make_account, make_site

PERSONAL = {
    "contact_email": "store@example.test",
    "contact_mobile": "+639171234567",
    "facebook_url": "https://www.facebook.com/ana",
    "instagram_url": "https://www.instagram.com/ana",
    "tiktok_url": "https://www.tiktok.com/@ana",
}
PROFILE = {
    "business_name": "Ana Trading",
    "registered_name": "Ana Reyes Trading",
    "tin": "123-456-789-000",
    "dti_business_number": "BN7654321",
    "bir_rsn": "113RC20260000017598",
    "business_address": "1 Mabini St, Davao City",
    "complaints_contact_name": "Ana R.",
    "privacy_contact_email": "privacy@example.test",
}
COUNTS = {"sites": 2, "business_profiles": 2, "web_addresses_retired": 2, "custom_hosts_retired": 0}


def rows():
    return {
        model._meta.label: list(model.objects.order_by("pk").values())
        for model in (Site, SiteBusinessProfile, Subdomain, SiteCustomHost, Onboarding)
    }


class SiteRetentionTestCase(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.account = make_account()
        self.first = self.site(self.account, "anas-store")
        self.second = self.site(self.account, "anas-other-store", first=False)
        self.other = make_account("ben@example.test")
        self.theirs = self.site(self.other, "bens-store")

    def site(self, account, name, **extra):
        site = make_site(account, name=name, accent_color="#112233", **PERSONAL, **extra)
        SiteBusinessProfile.objects.filter(site=site).update(**PROFILE)
        return site


class BlankSitesTests(SiteRetentionTestCase):
    def test_every_site_of_the_account_is_blanked_closed_and_its_name_retired(self):
        self.assertEqual(retention.blank_sites_of(self.account, self.now), COUNTS)
        for site in Site.objects.filter(account=self.account):
            for field in PERSONAL:
                self.assertEqual(getattr(site, field), "", field)
            self.assertEqual(site.display_name, f"Deleted site #{site.pk}")
            self.assertEqual(site.deleted_at, self.now)
            self.assertEqual((site.template, site.accent_color), ("sante", "#112233"))
            profile = SiteBusinessProfile.objects.get(site=site)
            for field in PROFILE:
                self.assertEqual(getattr(profile, field), "", field)
            self.assertEqual(profile.shipping_processing_time, "1-2 business days")
            name = Subdomain.objects.get(site=site)
            self.assertEqual((name.state, name.retired_at), ("retired", self.now))
        self.assertEqual(
            sorted(Subdomain.objects.values_list("name", flat=True)),
            ["anas-other-store", "anas-store", "bens-store"],
        )

    def test_the_sites_of_another_account_are_untouched(self):
        def theirs():
            return (
                list(Site.objects.filter(account=self.other).values()),
                list(SiteBusinessProfile.objects.filter(site=self.theirs).values()),
                list(Subdomain.objects.filter(site=self.theirs).values()),
                list(Onboarding.objects.filter(account=self.other).values()),
            )

        before = theirs()
        retention.blank_sites_of(self.account, self.now)
        self.assertEqual(theirs(), before)

    def test_the_onboarding_row_is_kept_as_it_is(self):
        before = list(Onboarding.objects.filter(account=self.account).values())
        retention.blank_sites_of(self.account, self.now)
        self.assertEqual(list(Onboarding.objects.filter(account=self.account).values()), before)

    def test_a_site_already_closed_keeps_its_date_and_a_retired_name_its_own(self):
        earlier = self.now - timedelta(days=5)
        Site.objects.filter(pk=self.second.pk).update(deleted_at=earlier)
        Subdomain.objects.filter(site=self.second).update(state="retired", retired_at=earlier)
        counts = retention.blank_sites_of(self.account, self.now)
        self.assertEqual(counts, {**COUNTS, "web_addresses_retired": 1})
        self.assertEqual(Site.objects.get(pk=self.second.pk).deleted_at, earlier)
        self.assertEqual(Subdomain.objects.get(site=self.second).retired_at, earlier)
        self.assertEqual(Site.objects.get(pk=self.second.pk).contact_email, "")

    def test_the_dry_run_counts_the_same_and_changes_nothing(self):
        before = rows()
        self.assertEqual(retention.blank_sites_of(self.account, self.now, dry_run=True), COUNTS)
        self.assertEqual(rows(), before)

    def test_an_account_without_a_site_counts_zero_and_gets_no_onboarding_row(self):
        bare = make_account("cora@example.test")
        self.assertEqual(
            retention.blank_sites_of(bare, self.now),
            {"sites": 0, "business_profiles": 0,
             "web_addresses_retired": 0, "custom_hosts_retired": 0},
        )
        self.assertFalse(Onboarding.objects.filter(account=bare).exists())

    def test_the_blanked_field_names_are_the_personal_ones(self):
        self.assertEqual(
            retention.BLANKED,
            {"site": [*PERSONAL, "display_name"], "site_business_profile": list(PROFILE)},
        )


class BlankSitesCustomHostTests(SiteRetentionTestCase):
    def setUp(self):
        super().setUp()
        self.host = SiteCustomHost.objects.create(host="shop.example.test", site=self.first)
        self.theirs_host = SiteCustomHost.objects.create(host="bens.example.test", site=self.theirs)

    def test_blanking_retires_the_active_custom_host(self):
        counts = retention.blank_sites_of(self.account, self.now)
        self.assertEqual(counts, {**COUNTS, "custom_hosts_retired": 1})
        self.host.refresh_from_db()
        self.assertEqual((self.host.state, self.host.retired_at), ("retired", self.now))

    def test_the_dry_run_counts_it_and_writes_nothing(self):
        before = rows()
        counts = retention.blank_sites_of(self.account, self.now, dry_run=True)
        self.assertEqual(counts, {**COUNTS, "custom_hosts_retired": 1})
        self.assertEqual(rows(), before)

    def test_another_accounts_site_keeps_its_host(self):
        retention.blank_sites_of(self.account, self.now)
        self.theirs_host.refresh_from_db()
        self.assertEqual((self.theirs_host.state, self.theirs_host.retired_at), ("active", None))

    def test_a_site_without_a_custom_host_counts_zero(self):
        counts = retention.blank_sites(Site.objects.filter(pk=self.second.pk), self.now)
        self.assertEqual(counts["custom_hosts_retired"], 0)

    def test_a_retired_host_is_not_counted_or_touched(self):
        earlier = self.now - timedelta(days=3)
        SiteCustomHost.objects.filter(pk=self.host.pk).update(state="retired", retired_at=earlier)
        counts = retention.blank_sites_of(self.account, self.now)
        self.assertEqual(counts["custom_hosts_retired"], 0)
        self.host.refresh_from_db()
        self.assertEqual(self.host.retired_at, earlier)

    def test_afterwards_the_host_does_not_resolve_and_is_not_the_public_host(self):
        self.assertEqual(custom_hosts.public_host(self.first), "shop.example.test")
        retention.blank_sites_of(self.account, self.now)
        self.assertIsNone(custom_hosts.active_row_for_host("shop.example.test"))
        self.assertNotEqual(custom_hosts.public_host(Site.objects.get(pk=self.first.pk)), "shop.example.test")
        self.assertIsNone(custom_hosts.active_host(Site.objects.get(pk=self.first.pk)))


class ExportSitesTests(SiteRetentionTestCase):
    def test_it_holds_each_site_with_its_names_profile_and_the_onboarding_dates(self):
        Onboarding.objects.filter(account=self.account).update(started_at=self.now)
        data = retention.export_sites_of(self.account)
        self.assertEqual(data["onboarding"]["started_at"], self.now.isoformat())
        self.assertIsNone(data["onboarding"]["minimum_completed_at"])
        self.assertEqual(
            [site["public_id"] for site in data["sites"]],
            [self.first.public_id, self.second.public_id],
        )
        site = data["sites"][0]
        self.assertEqual(site["display_name"], "Ana's Store")
        self.assertEqual(site["contact_mobile"], "+639171234567")
        self.assertEqual(site["business_profile"]["tin"], "123-456-789-000")
        self.assertEqual(site["business_profile"]["shipping_luzon_estimate"], "2-5 business days")
        self.assertEqual(
            [(name["name"], name["state"]) for name in site["web_addresses"]],
            [("anas-store", "active")],
        )
        json.dumps(data)  # plain values only

    def test_it_holds_nothing_of_another_account_and_no_row_number(self):
        text = json.dumps(retention.export_sites_of(self.account))
        self.assertNotIn("bens-store", text)
        self.assertNotIn(self.theirs.public_id, text)
        self.assertNotIn('"id"', text)
        self.assertNotIn("account", text)

    def test_an_account_without_a_site_exports_empty(self):
        bare = make_account("cora@example.test")
        self.assertEqual(retention.export_sites_of(bare), {"onboarding": None, "sites": []})

    def test_it_changes_nothing(self):
        before = rows()
        retention.export_sites_of(self.account)
        self.assertEqual(rows(), before)


class SealRetentionTests(SiteRetentionTestCase):
    def test_a_disposal_deletes_the_seal_files_and_blanks_the_keys_of_the_account_only(self):
        from apps.common import private_files
        from apps.payments.tests.base import PNG, stored_files, use_local_private_store

        directory = use_local_private_store(self)
        keys = {}
        for name, site in (("mine", self.first), ("theirs", self.theirs)):
            keys[name] = private_files.save(f"sites/seal/site-{site.pk}", PNG, "image/png")
            SiteBusinessProfile.objects.filter(site=site).update(seal_key=keys[name])
        retention.blank_sites_of(self.account, self.now)
        self.assertEqual(SiteBusinessProfile.objects.get(site=self.first).seal_key, "")
        self.assertEqual(SiteBusinessProfile.objects.get(site=self.theirs).seal_key, keys["theirs"])
        self.assertEqual(stored_files(directory), [keys["theirs"]])
