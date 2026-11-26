from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.sites import subdomains
from apps.sites.models import Site, Subdomain
from apps.sites.tests.base import make_account, make_site


class RulesTests(SimpleTestCase):
    def test_normalise_trims_and_lowercases(self):
        self.assertEqual(subdomains.normalise("  Anas-Store "), "anas-store")
        self.assertEqual(subdomains.normalise(None), "")

    def test_format(self):
        for name in ("abc", "a-b", "shop123", "123", "a" * 30):
            with self.subTest(name=name):
                self.assertTrue(subdomains.is_valid_format(name))
        for name in ("ab", "a" * 31, "-abc", "abc-", "a--b", "a_b", "a.b", "añb", "a b", "", "ABC"):
            with self.subTest(name=name):
                self.assertFalse(subdomains.is_valid_format(name))

    def test_every_infrastructure_label_is_reserved(self):
        self.assertEqual(len(subdomains.INFRA_LABELS), 28)
        for name in subdomains.INFRA_LABELS:
            with self.subTest(name=name):
                self.assertTrue(subdomains.is_reserved(name))

    def test_every_brand_name_is_reserved(self):
        self.assertEqual(len(subdomains.BRAND_NAMES), 5)
        for name in subdomains.BRAND_NAMES:
            with self.subTest(name=name):
                self.assertTrue(subdomains.is_reserved(name))

    def test_combined_forms_are_reserved(self):
        for name in ("sante-ph", "sante2ph", "ph-sante", "santecorporate", "my-official-store",
                     "w-w-w", "api2", "sante-1", "negosyo-online"):
            with self.subTest(name=name):
                self.assertTrue(subdomains.is_reserved(name))

    def test_the_sms_gateway_labels_are_reserved(self):
        for name in ("sms", "gateway", "s-m-s", "sms1", "gateway2", "gate-way"):
            with self.subTest(name=name):
                self.assertTrue(subdomains.is_reserved(name))
                self.assertEqual(subdomains.check(name), (name, subdomains.RESERVED))
        for name in ("sms-store", "mygateway"):
            with self.subTest(name=name):
                self.assertFalse(subdomains.is_reserved(name))

    def test_the_platform_host_labels_are_reserved_in_hyphenated_form(self):
        for name in (
            "hq-api", "hq-app", "storefronts", "api-staging", "hq-staging", "hq-api-staging",
            "hq-app-staging", "dash-staging", "www-staging", "storefronts-staging",
        ):
            with self.subTest(name=name):
                self.assertTrue(subdomains.is_reserved(name))
                self.assertEqual(subdomains.check(name), (name, subdomains.RESERVED))

    def test_ordinary_names_are_not_reserved(self):
        for name in ("anas-store", "mysantestore", "barley-shop", "santeria", "apiary"):
            with self.subTest(name=name):
                self.assertFalse(subdomains.is_reserved(name))


class CheckTests(TestCase):
    def setUp(self):
        self.account = make_account()

    def status(self, raw):
        return subdomains.check(raw)[1]

    def test_a_normal_name_is_still_available_beside_the_platform_labels(self):
        self.assertEqual(self.status("anas-store"), "available")

    def test_returns_the_normalised_name(self):
        self.assertEqual(subdomains.check("  My-Store "), ("my-store", "available"))

    def test_reserved_is_answered_before_invalid(self):
        self.assertEqual(self.status("hq"), "reserved")
        self.assertEqual(self.status("ab"), "invalid")

    def test_an_active_name_is_taken(self):
        make_site(self.account)

        self.assertEqual(self.status("anas-store"), "taken")
        self.assertEqual(self.status("ANAS-STORE"), "taken")

    def test_a_deleted_sites_name_is_taken(self):
        site = make_site(self.account)
        Site.objects.filter(pk=site.pk).update(deleted_at=timezone.now())

        self.assertEqual(self.status("anas-store"), "taken")

    def test_a_retired_name_is_taken(self):
        site = make_site(self.account)
        Subdomain.objects.filter(site=site).update(
            state=Subdomain.State.RETIRED, retired_at=timezone.now()
        )

        self.assertEqual(self.status("anas-store"), "taken")

    def test_an_operator_reserved_name_is_taken(self):
        Subdomain.objects.create(name="kept-name", state=Subdomain.State.OPERATOR_RESERVED)

        self.assertEqual(self.status("kept-name"), "taken")

    def test_a_released_name_is_available(self):
        Subdomain.objects.create(
            name="old-name", state=Subdomain.State.RETIRED, released_at=timezone.now()
        )

        self.assertEqual(self.status("old-name"), "available")

    def test_each_status_has_its_message(self):
        self.assertEqual(subdomains.MESSAGES["available"], "")
        self.assertEqual(
            subdomains.MESSAGES["invalid"],
            "Use 3 to 30 lowercase letters, digits or single hyphens.",
        )
        self.assertEqual(
            subdomains.MESSAGES["reserved"], "That name is reserved. Please choose another."
        )
        self.assertEqual(
            subdomains.MESSAGES["taken"], "That name is already taken. Please choose another."
        )
