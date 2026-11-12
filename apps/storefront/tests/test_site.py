"""GET /storefront/v1/site/: the store, its registration card and its distributor card."""
from apps.accounts.models import Account
from apps.billing.models import BillingCycle, Subscription, SubscriptionTransition
from apps.billing.tests.base import make_subscription
from apps.sites.models import Site, SiteBusinessProfile
from apps.storefront.tests.base import HOST, NOT_FOUND, SurfaceTestCase

KEYS = {
    "id", "template", "display_name", "accent_color", "facebook_url", "instagram_url",
    "tiktok_url", "business", "distributor", "policy_links", "free_shipping_banner",
    "meta_pixel_id",
}
BUSINESS_KEYS = {
    "registered_name", "tin", "dti_business_number", "contact_email", "contact_mobile",
    "has_seal",
}
DISTRIBUTOR_KEYS = {"name", "mobile", "has_photo"}


class SiteTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.subscription = make_subscription()
        self.site = self.subscription.site

    def answer(self, host=HOST):
        response = self.call(host)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        return response.json()

    def fill(self):
        Account.objects.filter(pk=self.site.account_id).update(name="Ana Reyes")
        Site.objects.filter(pk=self.site.pk).update(
            accent_color="#1a7f37", facebook_url="https://facebook.com/anas",
            instagram_url="https://instagram.com/anas", tiktok_url="https://tiktok.com/@anas",
            contact_email="store@example.test", contact_mobile="+639171234567",
        )
        SiteBusinessProfile.objects.filter(site=self.site).update(
            business_name="Ana's Wellness", registered_name="Ana Reyes Trading",
            tin="123-456-789-00000", dti_business_number="1234567",
            bir_rsn="113RC20260000017598", business_address="12 Mabini St, Quezon City",
            complaints_contact_name="Ana R.", privacy_contact_email="privacy@example.test",
        )

    def test_a_filled_site_answers_exactly_these_values(self):
        self.fill()

        self.assertEqual(self.answer(), {
            "id": self.site.public_id, "template": "sante", "display_name": "Ana's Store",
            "accent_color": "#1a7f37", "facebook_url": "https://facebook.com/anas",
            "instagram_url": "https://instagram.com/anas",
            "tiktok_url": "https://tiktok.com/@anas",
            "business": {
                "registered_name": "Ana Reyes Trading", "tin": "123-456-789-00000",
                "dti_business_number": "1234567", "contact_email": "store@example.test",
                "contact_mobile": "+639171234567", "has_seal": False,
            },
            "distributor": {"name": "Ana Reyes", "mobile": "+639171234567", "has_photo": False},
            "policy_links": [],
            "free_shipping_banner": False,
            "meta_pixel_id": "",
        })

    def test_an_empty_site_answers_the_same_keys_with_empty_values(self):
        body = self.answer()

        self.assertEqual(set(body), KEYS)
        self.assertEqual(set(body["business"]), BUSINESS_KEYS)
        self.assertEqual(set(body["distributor"]), DISTRIBUTOR_KEYS)
        self.assertEqual(body["business"], {
            "registered_name": "", "tin": "", "dti_business_number": "", "contact_email": "",
            "contact_mobile": "", "has_seal": False,
        })
        self.assertEqual(
            body["distributor"], {"name": "Ana's Store", "mobile": "", "has_photo": False}
        )

    def test_nothing_else_about_the_owner_or_the_site_is_answered(self):
        self.fill()

        text = self.call().content.decode()

        for private in (
            "ana@example.test", "privacy@example.test", "113RC20260000017598", "Mabini",
            "Ana R.", "Ana's Wellness", self.site.account.public_id,
            self.site.account.referral_code, "trial", "operator_hold",
        ):
            with self.subTest(private=private):
                self.assertNotIn(private, text)

    def test_the_distributor_name_falls_back_to_the_store_name(self):
        for name, shown in (("", "Ana's Store"), ("   ", "Ana's Store"), (" Ana ", "Ana")):
            with self.subTest(name=name):
                Account.objects.filter(pk=self.site.account_id).update(name=name)
                self.assertEqual(self.answer()["distributor"]["name"], shown)

    def test_the_distributor_mobile_is_shown_only_when_it_is_a_philippine_mobile(self):
        for stored, shown in (
            ("+639171234567", "+639171234567"), ("09171234567", "+639171234567"),
            ("", ""), ("12345", ""), ("+14155550100", ""),
        ):
            with self.subTest(stored=stored):
                Site.objects.filter(pk=self.site.pk).update(contact_mobile=stored)
                self.assertEqual(self.answer()["distributor"]["mobile"], shown)

    def test_the_contact_email_falls_back_to_the_privacy_contact(self):
        SiteBusinessProfile.objects.filter(site=self.site).update(
            privacy_contact_email="privacy@example.test"
        )
        self.assertEqual(self.answer()["business"]["contact_email"], "privacy@example.test")

        Site.objects.filter(pk=self.site.pk).update(contact_email="store@example.test")
        self.assertEqual(self.answer()["business"]["contact_email"], "store@example.test")

    def test_a_site_with_no_business_profile_row_is_still_answered(self):
        SiteBusinessProfile.objects.filter(site=self.site).delete()

        self.assertEqual(self.answer()["business"]["registered_name"], "")

    def test_one_host_never_answers_with_another_site(self):
        ben = make_subscription(email="ben@example.test", name="bens-store")
        Account.objects.filter(pk=ben.site.account_id).update(name="Ben Cruz")

        self.assertEqual(self.answer()["id"], self.site.public_id)
        other = self.answer("bens-store.negosyotest.com")
        self.assertEqual(other["id"], ben.site.public_id)
        self.assertEqual(other["distributor"]["name"], "Ben Cruz")

    def test_a_missing_or_empty_host_is_not_found(self):
        self.assert_answer(self.call(None), 404, NOT_FOUND)
        self.assert_answer(self.call(""), 404, NOT_FOUND)

    def test_the_call_writes_nothing(self):
        def rows():
            return (
                list(Site.objects.values()), list(Account.objects.values()),
                list(Subscription.objects.values()), SubscriptionTransition.objects.count(),
                BillingCycle.objects.count(), list(SiteBusinessProfile.objects.values()),
            )

        before = rows()
        self.answer()
        self.call("nobody-here.negosyotest.com")

        self.assertEqual(rows(), before)
