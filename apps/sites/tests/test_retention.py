import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.sites import retention
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
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
COUNTS = {"sites": 2, "business_profiles": 2, "web_addresses_retired": 2}


def rows():
    return {
        model._meta.label: list(model.objects.order_by("pk").values())
        for model in (Site, SiteBusinessProfile, Subdomain, Onboarding)
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
            {"sites": 0, "business_profiles": 0, "web_addresses_retired": 0},
        )
        self.assertFalse(Onboarding.objects.filter(account=bare).exists())

    def test_the_blanked_field_names_are_the_personal_ones(self):
        self.assertEqual(
            retention.BLANKED,
            {"site": [*PERSONAL, "display_name"], "site_business_profile": list(PROFILE)},
        )


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
