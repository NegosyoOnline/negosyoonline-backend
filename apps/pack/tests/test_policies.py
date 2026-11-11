"""The store policy pages' data: live only when every value is filled, one 404 shape, footer links,
and the version recorded on an order."""
from django.test import override_settings

from apps.pack import policies
from apps.pack.models import PackOrder
from apps.pack.tests.base import HOST, NOT_FOUND, OTHER_HOST, PackTestCase
from apps.sites.models import SiteBusinessProfile
from apps.storefront.tests.base import SECRET

SLUGS = list(policies.PAGES)


@override_settings(SITE_PARENT_HOST="negosyotest.com")
class PolicyTests(PackTestCase):
    def get(self, slug, host=HOST, secret=SECRET):
        return self.client.get(
            f"/storefront/v1/policies/{slug}/", {"host": host}, secure=True,
            HTTP_AUTHORIZATION=f"Bearer {secret}", HTTP_X_REAL_IP="198.51.100.200",
        )

    def fill(self, site=None, **extra):
        site = site or self.site
        site.contact_email, site.contact_mobile = "store@example.test", "+639171234567"
        site.save()
        SiteBusinessProfile.objects.filter(site=site).update(
            registered_name="Ana Reyes Trading", business_address="12 Mabini St", **extra
        )

    def test_unfilled_every_page_is_the_one_404(self):
        for slug in SLUGS + ["nope"]:
            r = self.get(slug)
            self.assertEqual((r.status_code, r.json()), (404, NOT_FOUND), slug)

    def test_identity_makes_terms_returns_payment_live_and_the_rest_wait(self):
        self.fill()
        for slug in ("terms-and-conditions", "return-refund-policy", "payment-policy"):
            self.assertEqual(self.get(slug).status_code, 200, slug)
        self.assertEqual(self.get("privacy-policy").status_code, 404)
        self.assertEqual(self.get("shipping-policy").status_code, 404)

    def test_privacy_needs_its_contact_and_shipping_needs_the_confirmed_estimates(self):
        self.fill(privacy_contact_email="privacy@example.test", shipping_estimates_confirmed=True)
        self.assertEqual(self.get("privacy-policy").status_code, 200)
        self.assertEqual(self.get("shipping-policy").status_code, 200)

    def test_the_page_prints_the_owners_details_and_never_the_tin(self):
        self.fill(tin="123-456-789-00000", dti_business_number="1234567")
        body = self.get("terms-and-conditions").json()
        self.assertEqual(body["business"]["name"], "Ana Reyes Trading")
        self.assertEqual(body["business"]["registration_details"], "DTI Business Number 1234567")
        self.assertNotIn("123-456", str(body))
        self.assertEqual((body["version"], body["effective_date"]), ("1", None))

    def test_another_store_shows_its_own_details(self):
        self.fill()
        self.fill(self.other_site)
        SiteBusinessProfile.objects.filter(site=self.other_site).update(registered_name="Ben Trading")
        self.assertEqual(self.get("terms-and-conditions", host=OTHER_HOST).json()["business"]["name"], "Ben Trading")

    def test_unknown_host_and_switch_off_are_404(self):
        self.fill()
        self.assertEqual(self.get("terms-and-conditions", host="nowhere.example.test").status_code, 404)
        with override_settings(PACK_ENABLED=False):
            self.assertEqual(self.get("terms-and-conditions").status_code, 404)

    def test_site_chrome_lists_only_live_pages_and_the_order_records_the_version(self):
        site = self.client.get("/storefront/v1/site/", {"host": HOST}, secure=True,
                               HTTP_AUTHORIZATION=f"Bearer {SECRET}", HTTP_X_REAL_IP="198.51.100.200")
        self.assertEqual(site.json()["policy_links"], [])
        self.placed(); self.assertEqual(PackOrder.objects.get().policy_version, "")
        self.fill()
        site = self.client.get("/storefront/v1/site/", {"host": HOST}, secure=True,
                               HTTP_AUTHORIZATION=f"Bearer {SECRET}", HTTP_X_REAL_IP="198.51.100.200")
        self.assertEqual([l["path"] for l in site.json()["policy_links"]],
                         ["/terms-and-conditions/", "/return-refund-policy/", "/payment-policy/"])
        self.assertEqual(policies.accepted_version(self.site), "1")
