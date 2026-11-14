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
    "srp_centavos", "unit_members_price_centavos", "revision", "image_urls", "features",
    "specifications", "advantages", "benefits", "servings_per_pack", "faq", "ingredients",
    "price_per_serving_centavos", "members_price_per_serving_centavos",
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


@switched_on
class ServingsAndFaqStorefrontTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.site = make_subscription().site

    def body(self, product, host=HOST):
        response = self.call(host, path=f"{LIST}{product.public_id}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_without_servings_the_per_serving_values_are_null_and_the_faq_is_empty(self):
        body = self.body(make_product())
        self.assertEqual(
            (body["servings_per_pack"], body["faq"], body["price_per_serving_centavos"],
             body["members_price_per_serving_centavos"]),
            (None, [], None, None),
        )

    def test_per_serving_is_rounded_half_up_from_the_srp_and_the_members_price(self):
        # 120000 / 7 = 17142.857 -> 17143; members 120000 x 0.6 = 72000 / 7 = 10285.714 -> 10286
        product = make_product("SERV-1", "Serv", 120000, "0.4000")
        CatalogProduct.objects.filter(pk=product.pk).update(
            servings_per_pack=7, faq=[{"q": "Why?", "a": "Because."}]
        )
        body = self.body(product)
        self.assertEqual(body["servings_per_pack"], 7)
        self.assertEqual(body["faq"], [{"q": "Why?", "a": "Because."}])
        self.assertEqual(body["price_per_serving_centavos"], 17143)
        self.assertEqual(body["members_price_per_serving_centavos"], 10286)
        self.assertEqual(
            body["members_price_per_serving_centavos"],
            pricing.per_serving(body["unit_members_price_centavos"], 7),
        )

    def test_a_half_centavo_rounds_up(self):
        product = make_product("SERV-2", "Half", 1001, "0.2000")
        CatalogProduct.objects.filter(pk=product.pk).update(servings_per_pack=2)
        body = self.body(product)
        self.assertEqual(body["price_per_serving_centavos"], 501)  # 500.5
        self.assertEqual(body["members_price_per_serving_centavos"], 401)  # 801 / 2 = 400.5

    def test_another_site_reads_the_same_values(self):
        product = make_product("SERV-3", "Shared", 30000, "0.4000")
        CatalogProduct.objects.filter(pk=product.pk).update(servings_per_pack=30)
        other = make_subscription("ben@example.test", "bens-store").site
        first = self.body(product)
        second = self.body(product, host="bens-store.negosyotest.com")
        self.assertEqual(first, second)
        self.assertEqual(second["price_per_serving_centavos"], 1000)
        self.assertEqual(other.template, self.site.template)


@switched_on
class IngredientsStorefrontTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.site = make_subscription().site

    def test_each_item_points_at_its_photo_and_an_empty_slot_gives_null(self):
        product = make_product()
        CatalogProduct.objects.filter(pk=product.pk).update(
            image_key_2="catalog/products/b.png",
            ingredients=[
                {"name": "Barley", "line": "Green.", "photo_slot": 2},
                {"name": "Kale", "line": "", "photo_slot": 4},
                {"name": "Oat", "line": "", "photo_slot": None},
            ],
        )
        with override_settings(**MEDIA):
            listed = self.call(path=LIST).json()["products"][0]
            detail = self.call(path=f"{LIST}{product.public_id}/").json()
        for body in (listed, detail):
            self.assertEqual(
                [i["photo_url"] for i in body["ingredients"]],
                ["https://media.example.test/catalog/products/b.png", None, None],
            )
            self.assertEqual([i["name"] for i in body["ingredients"]], ["Barley", "Kale", "Oat"])

    def test_a_product_without_them_returns_an_empty_list(self):
        body = self.call(path=f"{LIST}{make_product().public_id}/").json()
        self.assertEqual(body["ingredients"], [])
