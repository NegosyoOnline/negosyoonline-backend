from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.common.ids import is_public_id
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.base import make_account, make_site


class SiteModelTests(TestCase):
    def setUp(self):
        self.account = make_account()

    def test_site_gets_a_public_id(self):
        site = make_site(self.account)

        self.assertTrue(is_public_id(site.public_id, "site"))

    def test_template_choices_are_sante_only(self):
        choices = [value for value, _label in Site._meta.get_field("template").choices]

        self.assertEqual(choices, ["sante"])

    def test_subdomain_name_is_unique(self):
        make_site(self.account)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Subdomain.objects.create(name="anas-store", state=Subdomain.State.OPERATOR_RESERVED)

    def test_subdomain_must_be_lowercase_in_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subdomain.objects.create(name="Anas", state=Subdomain.State.OPERATOR_RESERVED)

    def test_a_site_has_at_most_one_active_subdomain(self):
        site = make_site(self.account)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Subdomain.objects.create(name="second-name", site=site)

    def test_a_retired_row_does_not_block_a_new_active_row_for_the_same_site(self):
        site = make_site(self.account)
        Subdomain.objects.filter(site=site).update(state=Subdomain.State.RETIRED)

        Subdomain.objects.create(name="new-name", site=site)

        self.assertEqual(site.subdomains.filter(state="active").count(), 1)

    def test_business_profile_carries_the_suggested_shipping_estimates(self):
        profile = make_site(self.account).business_profile

        self.assertEqual(profile.shipping_processing_time, "1-2 business days")
        self.assertEqual(profile.shipping_luzon_estimate, "2-5 business days")
        self.assertEqual(profile.shipping_other_areas_estimate, "")
        self.assertFalse(profile.shipping_estimates_confirmed)
        self.assertEqual(profile.business_name, "")

    def test_one_business_profile_per_site(self):
        site = make_site(self.account)

        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteBusinessProfile.objects.create(site=site)

    def test_one_onboarding_row_per_account(self):
        Onboarding.objects.create(account=self.account)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Onboarding.objects.create(account=self.account)
