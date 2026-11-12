"""GET /storefront/v1/catalog/products/ and /{public_id}/: what the storefronts server reads."""
from decimal import Decimal

from django.test import override_settings

from apps.billing.tests.base import make_subscription
from apps.catalog import pricing
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import MEDIA, make_product, retemplate, switched_on
from apps.storefront.tests.base import HOST, NOT_FOUND, UNAUTHENTICATED, SurfaceTestCase

LIST = "/storefront/v1/catalog/products/"
KEYS = {
    "id", "code", "name", "category", "short_description", "long_description", "image_url",
    "srp_centavos", "unit_members_price_centavos", "revision",
}
NEVER = ("earnings_rate", "is_active", "weight", "changed_by", "seed_", "source", "image_key",
         "_cm", "volume", "shipping")


@switched_on
class CatalogStorefrontTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.site = make_subscription().site

    def listed(self, host=HOST):
        return self.call(host, path=LIST)

    def test_it_needs_the_server_secret_like_every_storefront_route(self):
        make_product()
        for path in (LIST, f"{LIST}prod_x/"):
            self.assert_answer(self.call(secret=None, path=path), 401, UNAUTHENTICATED)
            self.assert_answer(self.call(secret="wrong", path=path), 401, UNAUTHENTICATED)

    def test_only_active_products_in_creation_order(self):
        make_product("B-TWO", "Two", 20000, "0.4000")
        make_product("A-ONE", "One", 10000, "0.4000")
        make_product("C-OFF", "Off", 30000, "0.4000", active=False)
        response = self.listed()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([p["code"] for p in response.json()["products"]], ["B-TWO", "A-ONE"])
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_an_empty_catalog_is_an_empty_list(self):
        self.assertEqual(self.listed().json(), {"products": []})

    def test_the_payload_keys_and_the_members_price(self):
        product = make_product("SPHB01001", "Fusion Coffee", 45000, "0.4000")
        CatalogProduct.objects.filter(pk=product.pk).update(category="drinks", short_description="s")
        body = self.listed().json()["products"][0]
        self.assertEqual(set(body), KEYS)
        self.assertEqual(body["unit_members_price_centavos"], 27000)
        self.assertEqual(
            body["unit_members_price_centavos"],
            pricing.unit_display(product.srp_centavos, Decimal("0.4000")),
        )
        self.assertEqual(
            (body["id"], body["srp_centavos"], body["category"], body["image_url"], body["revision"]),
            (product.public_id, 45000, "drinks", None, 1),
        )
        raw = self.listed().content.decode()
        for text in NEVER:
            self.assertNotIn(text, raw)

    def test_the_image_url_is_the_public_host_and_the_key(self):
        product = make_product()
        CatalogProduct.objects.filter(pk=product.pk).update(image_key="catalog/products/ab.png")
        with override_settings(**MEDIA):
            body = self.listed().json()["products"][0]
        self.assertEqual(body["image_url"], "https://media.example.test/catalog/products/ab.png")
        self.assertNotIn("image_key", body)

    def test_detail_returns_one_active_product(self):
        product = make_product()
        response = self.call(path=f"{LIST}{product.public_id}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), KEYS)
        self.assertEqual(response.json()["id"], product.public_id)

    def test_detail_404_for_inactive_unknown_and_a_row_number(self):
        off = make_product("OFF-1", active=False)
        on = make_product("ON-1")
        for key in (off.public_id, "prod_00000000000000000000", str(on.pk)):
            self.assert_answer(self.call(path=f"{LIST}{key}/"), 404, NOT_FOUND)

    def test_unknown_host_and_a_site_that_is_not_sante_are_the_same_404(self):
        product = make_product()
        self.assert_answer(self.listed("nobody.negosyotest.com"), 404, NOT_FOUND)
        self.assert_answer(self.listed(""), 404, NOT_FOUND)
        retemplate(self.site)
        self.assert_answer(self.listed(), 404, NOT_FOUND)
        self.assert_answer(self.call(path=f"{LIST}{product.public_id}/"), 404, NOT_FOUND)

    def test_switch_off_is_404_for_both_routes(self):
        product = make_product()
        for off in ({"SANTE_CATALOG_ENABLED": False}, {"SANTE_SETTINGS_ENABLED": False}):
            with override_settings(**off):
                self.assert_answer(self.listed(), 404, NOT_FOUND)
                self.assert_answer(self.call(path=f"{LIST}{product.public_id}/"), 404, NOT_FOUND)
