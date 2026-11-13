"""Arrange: the owner sets the shop order of the shared catalog (CAT-1, display_order)."""
from unittest import mock

from django.core.cache import cache
from django.db import connection
from unittest import skipUnless

from django.test import TransactionTestCase

from apps.accounts.models import Account
from apps.billing.tests.base import make_subscription
from apps.catalog import services
from apps.catalog.models import CatalogProduct, CatalogProductRevision
from apps.catalog.tests.base import (
    MISSING_PRODUCT, MISSING_SITE, CatalogApiTestCase, catalog_rows, make_product, operator,
    order_url, switched_on,
)
from apps.common.errors import ApiError
from apps.sante import services as sante
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site
from apps.sites.tests.test_concurrency import run_together
from apps.storefront.tests.base import SurfaceTestCase

STOREFRONT = "/storefront/v1/catalog/products/"


class ArrangeApiTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()
        self.a = make_product("FX-A", "Alpha")
        self.b = make_product("FX-B", "Beta")
        self.c = make_product("FX-C", "Gamma", active=False)
        self.d = make_product("FX-D", "Delta")

    def ids(self, *products):
        return [product.public_id for product in products]

    def owner_codes(self):
        return [row["code"] for row in self.listed()["products"]]

    def test_the_order_is_creation_order_until_the_first_arrange(self):
        self.assertEqual(self.owner_codes(), ["FX-A", "FX-B", "FX-C", "FX-D"])
        self.assertEqual(set(CatalogProduct.objects.values_list("display_order", flat=True)), {0})

    def test_an_arrange_shows_in_the_owner_list_and_answers_with_it(self):
        response = self.arrange(self.ids(self.d, self.b, self.a, self.c))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body, self.listed())
        self.assertTrue(body["can_edit"])
        self.assertEqual([p["code"] for p in body["products"]], ["FX-D", "FX-B", "FX-A", "FX-C"])
        # Another Santé store reads the same order, active products only.
        self.assertEqual(
            [p["code"] for p in self.listed(self.ben_site, self.ben)["products"]],
            ["FX-D", "FX-B", "FX-A"],
        )

    def test_an_arrange_writes_no_revision_and_does_not_stale_a_form(self):
        before = list(CatalogProductRevision.objects.values_list("pk", "revision"))
        self.assertEqual(self.arrange(self.ids(self.b, self.a, self.c, self.d)).status_code, 200)
        self.assertEqual(list(CatalogProductRevision.objects.values_list("pk", "revision")), before)
        self.assertEqual(set(CatalogProduct.objects.values_list("revision", flat=True)), {1})
        self.assertEqual(
            self.update(self.a.public_id, {"revision": 1, "name": "Alpha 2"}).status_code, 200
        )

    def test_the_same_order_again_changes_nothing(self):
        order = self.ids(self.c, self.d, self.a, self.b)
        self.arrange(order)
        before = catalog_rows()
        self.assertEqual(self.arrange(order).status_code, 200)
        self.assertEqual(catalog_rows(), before)

    def test_a_product_created_after_an_arrange_goes_last(self):
        self.arrange(self.ids(self.d, self.c, self.b, self.a))
        self.created(code="FX-NEW")
        self.assertEqual(self.owner_codes(), ["FX-D", "FX-C", "FX-B", "FX-A", "FX-NEW"])

    def test_products_created_before_any_arrange_go_last_in_creation_order(self):
        self.created(code="FX-NEW")
        self.created(code="FX-NEWER")
        self.assertEqual(
            self.owner_codes(), ["FX-A", "FX-B", "FX-C", "FX-D", "FX-NEW", "FX-NEWER"]
        )

    def test_only_the_catalog_owner_arranges(self):
        before = catalog_rows()
        order = self.ids(self.d, self.c, self.b, self.a)
        for site, client in ((self.ben_site, self.ben), (self.cora_site, self.cora)):
            with self.subTest(site=site.display_name):
                self.error(self.arrange(order, site=site, client=client), 403, "catalog_read_only")
        self.assertEqual(catalog_rows(), before)

    def test_a_signed_out_request_is_refused(self):
        before = catalog_rows()
        self.client.cookies.clear()
        self.error(self.arrange(self.ids(self.d, self.c, self.b, self.a)), 401, "unauthenticated")
        self.assertEqual(catalog_rows(), before)

    def test_a_list_that_is_not_exactly_the_catalog_is_refused(self):
        other = make_product("FX-OTHER", key="othercat")
        every = self.ids(self.a, self.b, self.c, self.d)
        wrong = {
            "missing one": every[:-1],
            "empty": [],
            "an extra unknown id": [*every, MISSING_PRODUCT],
            "a duplicate": [*every, every[0]],
            "a duplicate in place of one": [every[0], every[0], every[2], every[3]],
            "a product of another catalog in place of one": [*every[:3], other.public_id],
            "another catalog's product added": [*every, other.public_id],
            "a row number": [*every[:3], str(self.d.pk)],
        }
        before = catalog_rows()
        for name, ids in wrong.items():
            with self.subTest(name):
                response = self.arrange(ids)
                self.assertEqual(self.fields(response), {"product_ids": [services.ORDER_MSG]})
        self.assertEqual(catalog_rows(), before)
        self.assertEqual(CatalogProduct.objects.get(pk=other.pk).display_order, 0)

    def test_a_body_of_the_wrong_shape_is_refused(self):
        before = catalog_rows()
        url = order_url(self.site.public_id)
        for body in ({}, {"product_ids": "FX"}, {"product_ids": [1, 2]}, {"product_ids": None},
                     {"product_ids": [], "code": "X"}, {"ids": []}):
            with self.subTest(body=body):
                self.error(self.post(url, body), 400, "invalid_request")
        self.assertEqual(catalog_rows(), before)

    def test_another_accounts_or_missing_site_answers_the_same_404(self):
        reference = self.get(f"/v1/sites/{MISSING_SITE}/catalog/products/").json()
        before = catalog_rows()
        order = self.ids(self.d, self.c, self.b, self.a)
        for site_id in (MISSING_SITE, self.ben_site.public_id):
            with self.subTest(site_id=site_id):
                response = self.post(order_url(site_id), {"product_ids": order})
                self.assertEqual((response.status_code, response.json()), (404, reference))
        self.assertEqual(catalog_rows(), before)

    def test_an_arrange_is_one_of_the_limited_writes(self):
        order = self.ids(self.a, self.b, self.c, self.d)
        with mock.patch.object(services, "WRITE_LIMIT", 2):
            answers = [self.arrange(order).status_code for _ in range(3)]
        self.assertEqual(answers, [200, 200, 429])


@switched_on
class ArrangeStorefrontTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.site = make_subscription().site
        self.owner = make_site(
            make_account("owner@example.test"), "owner-store", "Owner Store", first=False
        )
        sante.set_catalog_owner(self.owner, operator=operator())
        self.a = make_product("A-ONE", "One")
        self.b = make_product("B-TWO", "Two")
        self.c = make_product("C-OFF", "Off", active=False)
        self.d = make_product("D-FOUR", "Four")

    def codes(self):
        response = self.call(path=STOREFRONT)
        self.assertEqual(response.status_code, 200, response.content)
        return [p["code"] for p in response.json()["products"]]

    def test_creation_order_until_arranged_then_the_arranged_order(self):
        self.assertEqual(self.codes(), ["A-ONE", "B-TWO", "D-FOUR"])
        services.reorder_products(
            self.owner.account, self.owner,
            [p.public_id for p in (self.d, self.c, self.b, self.a)],
        )
        self.assertEqual(self.codes(), ["D-FOUR", "B-TWO", "A-ONE"])  # the hidden one stays hidden

    def test_a_non_owner_site_cannot_arrange_through_the_service(self):
        with self.assertRaises(ApiError) as caught:
            services.reorder_products(
                self.site.account, self.site, [p.public_id for p in (self.d, self.c, self.b, self.a)]
            )
        self.assertEqual((caught.exception.status, caught.exception.code), (403, "catalog_read_only"))
        self.assertEqual(self.codes(), ["A-ONE", "B-TWO", "D-FOUR"])


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@switched_on
class ArrangeRaceTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.account = make_account()
        self.site = make_site(self.account, "race-arr", "Race Arrange")
        sante.set_catalog_owner(self.site, operator=operator("arrange-op"))
        self.products = [make_product(f"FX-{n}") for n in range(4)]

    def arrange(self, products):
        ids = [p.public_id for p in products]

        def call():
            return services.reorder_products(
                Account.objects.get(pk=self.account.pk), Site.objects.get(pk=self.site.pk), ids
            )
        return call

    def test_two_arranges_at_once_leave_one_whole_order(self):
        forward, backward = self.products, self.products[::-1]
        run_together(self.arrange(forward), self.arrange(backward))
        codes = list(
            CatalogProduct.objects.order_by("display_order", "pk").values_list("code", flat=True)
        )
        self.assertIn(codes, ([p.code for p in forward], [p.code for p in backward]))
        self.assertEqual(
            sorted(CatalogProduct.objects.values_list("display_order", flat=True)), [1, 2, 3, 4]
        )
