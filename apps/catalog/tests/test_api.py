"""The shared catalog over HTTP. Restates TWCinc's test_catalog_api for whole centavos, public
ids, this repository's error envelope and the 404 rule.

Two TWCinc groups have no counterpart here: "writes are data entry" and the launch-gate
exception tests. Both test TWCinc's launch gate, and no gate is built in this repository (each
surface has its own switch instead, tested in SwitchedOffTests).
"""
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.catalog import services
from apps.catalog.api import router
from apps.catalog.models import CatalogProduct, CatalogProductRevision
from apps.catalog.tests.base import (
    MISSING_PRODUCT, MISSING_SITE, VALID_CREATE, CatalogApiTestCase, catalog_rows, make_product,
    product_url, products_url, retemplate, revisions_url,
)
from apps.sites.models import Site
from apps.sites.tests.test_isolation import routes_under_a_site

M = services.FIELD_MESSAGES
ROUTES = [
    ("GET", "/sites/{site_id}/catalog/products/"),
    ("POST", "/sites/{site_id}/catalog/products/"),
    ("POST", "/sites/{site_id}/catalog/products/order/"),
    ("PATCH", "/sites/{site_id}/catalog/products/{product_id}/"),
    ("GET", "/sites/{site_id}/catalog/products/{product_id}/revisions/"),
    ("POST", "/sites/{site_id}/catalog/products/{product_id}/image/"),
    ("DELETE", "/sites/{site_id}/catalog/products/{product_id}/image/"),
    ("POST", "/sites/{site_id}/catalog/shipping-preview/"),
]


class CatalogApiTests(CatalogApiTestCase):
    def test_a_list_before_anything_is_written_is_empty(self):
        self.assertEqual(
            self.listed(self.ben_site, self.ben), {"can_edit": False, "products": []}
        )
        self.assertEqual(catalog_rows(), ([], [], []))  # a read never creates the catalog

    def test_a_create_answers_201_at_revision_one(self):
        self.designate()
        body = self.created()
        self.assertEqual(
            (body["code"], body["srp_centavos"], body["unit_members_price_centavos"],
             body["earnings_rate"], body["revision"], body["is_active"]),
            ("FX-ALPHA", 9999, 6499, "0.3500", 1, True),
        )
        product = CatalogProduct.objects.get()
        self.assertEqual(body["id"], product.public_id)
        self.assertTrue(body["id"].startswith("prod_"))
        revision = CatalogProductRevision.objects.get(product=product)
        self.assertEqual(
            (revision.source, revision.changed_by_account_id, revision.changed_by_site_id),
            ("owner", self.account.pk, self.site.pk),
        )

    def test_no_row_number_or_writer_is_in_a_response(self):
        self.designate()
        made = self.created()
        answers = [made, self.listed()["products"][0],
                   self.revisions(made["id"]).json()["revisions"][0]]
        for answer in answers:
            with self.subTest(answer=sorted(answer)):
                self.assertFalse({"pk", "catalog", "catalog_id", "product", "product_id"} & set(answer))
                self.assertFalse([name for name in answer if name.startswith("changed_by")])
                self.assertNotIn("seed_file_sha256", answer)

    def test_a_taken_code_is_409_whatever_its_case(self):
        self.designate()
        self.created()
        body = self.error(self.create(code="fx-alpha"), 409, "code_taken")
        self.assertEqual(body["fields"], {"code": [services.CODE_TAKEN_MSG]})
        self.assertEqual(CatalogProduct.objects.count(), 1)

    def test_a_stale_revision_is_409(self):
        self.designate()
        product = make_product()
        self.update(product.public_id, {"revision": 1, "name": "First"})
        self.error(
            self.update(product.public_id, {"revision": 1, "name": "Second"}),
            409, "stale_revision",
        )
        self.assertEqual(CatalogProduct.objects.get(pk=product.pk).name, "First")

    def test_with_no_valid_owner_nobody_writes(self):
        product = make_product()
        before = catalog_rows()
        change = {"revision": 1, "name": "X"}
        for site, client in ((self.site, None), (self.ben_site, self.ben)):
            with self.subTest(site=site.display_name):
                self.error(self.create(site=site, client=client), 403, "catalog_read_only")
                self.error(
                    self.update(product.public_id, change, site=site, client=client),
                    403, "catalog_read_only",
                )
        self.designate()
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.error(
            self.update(product.public_id, change, site=self.ben_site, client=self.ben),
            403, "catalog_read_only",
        )
        self.error(self.create(site=self.cora_site, client=self.cora), 403, "catalog_read_only")
        self.error(self.update(product.public_id, change), 404, "not_found")  # the removed site
        self.assertEqual(catalog_rows(), before)

    def test_an_update_adds_a_revision_and_an_unchanged_one_does_not(self):
        self.designate()
        product = make_product()
        response = self.update(
            product.public_id,
            {"revision": 1, "srp_centavos": 12000, "earnings_rate": "0.4000", "weight_kg": "0.25"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(
            (body["revision"], body["srp_centavos"], body["unit_members_price_centavos"]),
            (2, 12000, 7200),
        )
        revision = product.revisions.get(revision=2)
        self.assertEqual(
            (revision.srp_centavos, str(revision.earnings_rate), str(revision.weight_kg),
             revision.source, revision.changed_by_account_id, revision.changed_by_site_id),
            (12000, "0.4000", "0.25", "owner", self.account.pk, self.site.pk),
        )
        response = self.update(product.public_id, {"revision": 2, "srp_centavos": 12000})
        self.assertEqual(
            (response.status_code, response.json()["revision"], product.revisions.count()),
            (200, 2, 2),
        )

    def test_a_product_is_deactivated_never_deleted(self):
        self.designate()
        product = make_product()
        response = self.update(product.public_id, {"revision": 1, "is_active": False})
        self.assertEqual(
            (response.status_code, response.json()["is_active"], response.json()["revision"]),
            (200, False, 2),
        )
        self.assertEqual(self.listed(self.ben_site, self.ben)["products"], [])
        self.assertEqual(
            [(row["code"], row["is_active"]) for row in self.listed()["products"]],
            [("FX-ALPHA", False)],
        )
        response = self.update(product.public_id, {"revision": 2, "is_active": True})
        self.assertEqual(
            (response.status_code, response.json()["is_active"], product.revisions.count()),
            (200, True, 3),
        )
        # The only DELETE is the image's: no route deletes a product.
        self.assertEqual(
            [path for method, path in routes_under_a_site(router) if method == "DELETE"],
            ["/sites/{site_id}/catalog/products/{product_id}/image/"],
        )
        self.assertTrue(CatalogProduct.objects.filter(pk=product.pk).exists())

    def test_each_refused_value_gets_its_fields_message(self):
        self.designate()
        cases = [
            ("code", "a b"), ("code", ""), ("code", "-X"), ("code", "X" * 65),
            ("name", ""), ("name", "   "), ("name", "x" * 121),
            ("short_description", "x" * 501), ("long_description", "x" * 20001),
            ("weight_kg", "-1"), ("weight_kg", "1.234"), ("weight_kg", "123456789"),
            ("srp_centavos", 0), ("srp_centavos", -1), ("srp_centavos", 1_000_000_000_000),
            ("earnings_rate", "0.0999"), ("earnings_rate", "0.5001"),
            ("earnings_rate", "0.12345"), ("earnings_rate", "1"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                response = self.create(**{field: value})
                body = self.error(response, 400, "invalid_request")
                self.assertEqual(
                    (body["message"], body["fields"]), (services.INVALID_MSG, {field: [M[field]]})
                )
        self.assertFalse(CatalogProduct.objects.exists())

    def test_a_null_for_a_value_gets_that_fields_message(self):
        self.designate()
        product = make_product("FX-BASE")
        response = self.update(product.public_id, {"revision": 1, "name": None})
        self.assertEqual(self.fields(response), {"name": [M["name"]]})
        self.assertEqual(CatalogProduct.objects.get(pk=product.pk).revision, 1)

    def test_a_body_of_the_wrong_shape_is_refused(self):
        self.designate()
        product = make_product("FX-BASE")
        before = catalog_rows()
        creates = [
            {**VALID_CREATE, "srp_centavos": 99.99},  # an amount is never rounded
            {**VALID_CREATE, "srp_centavos": "9999"},
            {**VALID_CREATE, "srp": "99.99"},
            {**VALID_CREATE, "catalog_owner": True},
            {**VALID_CREATE, "changed_by_account_id": 1},
            {**VALID_CREATE, "is_active": False},
            {**VALID_CREATE, "revision": 5},
            {**VALID_CREATE, "name": None},
            {"code": "FX-ONLY"},
        ]
        updates = [
            {"revision": 1, "code": "FX-NEW"},  # the code is fixed after creation
            {"revision": 1, "catalog_owner": True},
            {"name": "No revision"},
            {"revision": True, "name": "Bool"},
            {"revision": 0, "name": "Zero"},
            {"revision": "1", "name": "Text"},
            {"revision": 1, "is_active": "yes"},
            {"revision": 1, "is_active": None},
            {"revision": 1, "shipping_qty": "2"},
        ]
        for body in creates:
            with self.subTest(method="POST", body=body):
                self.error(self.post(self.products, body), 400, "invalid_request")
        for body in updates:
            with self.subTest(method="PATCH", body=body):
                self.error(self.update(product.public_id, body), 400, "invalid_request")
        self.assertEqual(catalog_rows(), before)

    def test_a_code_is_stripped_and_upper_cased(self):
        self.designate()
        self.assertEqual(self.created(code=" fx-lower ")["code"], "FX-LOWER")

    def test_can_edit_is_true_for_the_owner_site_only(self):
        self.designate()
        self.assertTrue(self.listed()["can_edit"])
        self.assertFalse(self.listed(self.ben_site, self.ben)["can_edit"])

    def test_shipping_inputs_are_central_and_revisioned(self):
        self.designate()
        made = self.created(weight_kg="0.18", volume_slot="1.50", shipping_qty=2)
        response = self.update(made["id"], {"revision": 1, "volume_slot": "2.00"})
        self.assertEqual(response.status_code, 200, response.content)
        revisions = self.revisions(made["id"]).json()["revisions"]
        self.assertEqual(
            [(row["revision"], row["volume_slot"], row["shipping_qty"]) for row in revisions],
            [(2, "2.00", 2), (1, "1.50", 2)],
        )

    def test_another_subscriber_cannot_write_shipping_inputs(self):
        self.designate()
        made = self.created()
        response = self.update(
            made["id"], {"revision": 1, "shipping_qty": 3}, site=self.ben_site, client=self.ben
        )
        self.error(response, 403, "catalog_read_only")
        self.assertIsNone(CatalogProduct.objects.get().shipping_qty)

    def test_a_create_without_shipping_inputs_or_sizes_succeeds(self):
        self.designate()
        made = self.created()
        self.assertEqual(
            [made[name] for name in ("volume_slot", "shipping_qty", "length_cm", "width_cm",
                                     "height_cm", "weight_kg")],
            [None, None, None, None, None, "0.00"],
        )

    def test_invalid_shipping_inputs_are_refused(self):
        self.designate()
        for body in ({"volume_slot": "-1"}, {"volume_slot": "1.234"}, {"volume_slot": "0"},
                     {"volume_slot": "0.00"}, {"shipping_qty": 0}, {"shipping_qty": -1}):
            with self.subTest(body=body):
                response = self.create(code="FX-B", weight_kg="0.18", **body)
                self.assertEqual(set(self.fields(response)), set(body))
        self.assertFalse(CatalogProduct.objects.exists())

    def test_shipping_inputs_need_a_weight_above_zero(self):
        self.designate()
        for body in ({"volume_slot": "1.50"}, {"shipping_qty": 1},
                     {"weight_kg": "0.00", "volume_slot": "1.50"}):
            with self.subTest(method="POST", body=body):
                fields = self.fields(self.create(code="FX-W", **body))
                self.assertEqual(fields, {"weight_kg": [services.SHIPPING_WEIGHT_MSG]})
        self.assertFalse(CatalogProduct.objects.exists())

        made = self.created()
        for body in ({"weight_kg": "0.00"}, {"weight_kg": "0"}, {"volume_slot": "1.50"},
                     {"shipping_qty": 1}):
            with self.subTest(method="PATCH", body=body):
                fields = self.fields(self.update(made["id"], {"revision": 1, **body}))
                self.assertEqual(fields, {"weight_kg": [services.SHIPPING_WEIGHT_MSG]})
        self.assertEqual(CatalogProduct.objects.get().revision, 1)

        response = self.update(
            made["id"],
            {"revision": 1, "weight_kg": "0.18", "volume_slot": "1.90", "shipping_qty": 1},
        )
        self.assertEqual((response.status_code, response.json()["volume_slot"]), (200, "1.90"))
        response = self.update(made["id"], {"revision": 2, "volume_slot": "0.00"})
        self.assertEqual(set(self.fields(response)), {"volume_slot"})

    def test_an_update_cannot_clear_a_shipping_input_or_a_size_with_null(self):
        self.designate()
        made = self.created(
            weight_kg="0.18", volume_slot="1.50", shipping_qty=2,
            length_cm="10.0", width_cm="5.5", height_cm="2.0",
        )
        for body in ({"volume_slot": None}, {"shipping_qty": None}, {"length_cm": None}):
            with self.subTest(body=body):
                response = self.update(made["id"], {"revision": 1, **body})
                self.assertEqual(set(self.fields(response)), set(body))
        self.assertEqual(CatalogProduct.objects.get().revision, 1)

    def test_sizes_are_all_three_or_none(self):
        self.designate()
        fields = self.fields(self.create(length_cm="10.0"))
        self.assertEqual(
            fields, {"width_cm": [services.DIMS_ALL_MSG], "height_cm": [services.DIMS_ALL_MSG]}
        )
        made = self.created(length_cm="10.0", width_cm="5.5", height_cm="2.0")
        self.assertEqual(
            (made["length_cm"], made["width_cm"], made["height_cm"]), ("10.0", "5.5", "2.0")
        )

    def test_the_revisions_are_for_the_owner_site_only_newest_first(self):
        self.designate()
        product = make_product()
        self.update(product.public_id, {"revision": 1, "name": "Second"})
        response = self.revisions(product.public_id)
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["revisions"]
        self.assertEqual(
            [(row["revision"], row["name"], row["source"]) for row in rows],
            [(2, "Second", "owner"), (1, "Fixture Alpha", "seed")],
        )
        self.assertEqual(set(rows[0]) - set(self.listed()["products"][0]), {"source", "changed_at"})
        self.error(
            self.revisions(product.public_id, site=self.ben_site, client=self.ben),
            403, "catalog_read_only",
        )

    def test_a_product_of_another_catalog_or_a_missing_one_is_404(self):
        self.designate()
        foreign = make_product("FX-FOREIGN", key="othercat")
        mine = make_product()
        reference = self.get(products_url(MISSING_SITE)).json()
        for product_id in (foreign.public_id, MISSING_PRODUCT, str(mine.pk), str(foreign.pk)):
            with self.subTest(product_id=product_id):
                response = self.update(product_id, {"revision": 1, "name": "X"})
                self.assertEqual((response.status_code, response.json()), (404, reference))
                response = self.revisions(product_id)
                self.assertEqual((response.status_code, response.json()), (404, reference))
        self.assertEqual(
            list(CatalogProduct.objects.order_by("pk").values_list("name", "revision")),
            [("Fixture Alpha", 1), ("Fixture Alpha", 1)],
        )

    def test_writes_are_limited_per_account(self):
        self.designate()
        product = make_product()
        with mock.patch.object(services, "WRITE_LIMIT", 2):
            answers = [
                self.update(product.public_id, {"revision": n, "name": f"N{n}"}).status_code
                for n in (1, 2, 3)
            ]
            self.error(self.create(code="FX-NEXT"), 429, "rate_limited")
            # A read is not a write, and another account's count is its own.
            self.assertEqual(self.listed()["products"][0]["revision"], 3)
            self.error(self.create(site=self.ben_site, client=self.ben), 403, "catalog_read_only")
        self.assertEqual(answers, [200, 200, 429])
        self.assertEqual(CatalogProduct.objects.count(), 1)

    def test_a_signed_out_request_is_refused(self):
        self.designate()
        product = make_product()
        before = catalog_rows()
        self.client.cookies.clear()
        self.error(self.get(self.products), 401, "unauthenticated")
        self.error(self.create(), 401, "unauthenticated")
        self.error(self.update(product.public_id, {"revision": 1, "name": "X"}), 401, "unauthenticated")
        self.error(self.revisions(product.public_id), 401, "unauthenticated")
        self.assertEqual(catalog_rows(), before)


class IsolationTests(CatalogApiTestCase):
    """A foreign, missing, removed or non-Santé site answers the same 404 on all four routes,
    whatever the body, even when that site is the catalog owner."""

    def setUp(self):
        super().setUp()
        self.designate(self.ben_site)
        self.product = make_product(name="Bens Secret Name")

    def call(self, method, template, site_id, body, client=None):
        path = "/v1" + template.replace("{site_id}", site_id).replace(
            "{product_id}", self.product.public_id
        )
        if method == "GET":
            return self.get(path, client=client)
        return self.send(method.lower(), path, body, client=client)

    def assert_same_404(self, site_id, client=None):
        reference = self.get(products_url(MISSING_SITE)).json()
        before = catalog_rows()
        valid = {"POST": {**VALID_CREATE, "code": "FX-TAKEOVER"}, "PATCH": {"revision": 1, "name": "X"}}
        for method, template in ROUTES:
            # A body of the wrong shape is never looked at: the 404 comes first.
            for body in (valid.get(method), {"bad": 1}) if method != "GET" else (None,):
                with self.subTest(method=method, template=template, body=body):
                    response = self.call(method, template, site_id, body, client=client)
                    self.assertEqual(response.status_code, 404, response.content)
                    self.assertEqual(response.json(), reference)
                    self.assertNotIn("Bens Secret Name", response.content.decode())
        self.assertEqual(catalog_rows(), before)

    def test_every_route_under_a_site_is_one_this_test_knows(self):
        self.assertEqual(sorted(routes_under_a_site(router)), sorted(ROUTES))
        self.assertEqual(len(router.path_operations), 6)  # none outside a site

    def test_another_accounts_site(self):
        self.assert_same_404(self.ben_site.public_id)

    def test_a_missing_site(self):
        self.assert_same_404(MISSING_SITE)

    def test_a_removed_site(self):
        Site.objects.filter(pk=self.ben_site.pk).update(deleted_at=timezone.now())
        self.assert_same_404(self.ben_site.public_id, client=self.ben)

    def test_a_non_sante_site(self):
        retemplate(self.ben_site)
        self.assert_same_404(self.ben_site.public_id, client=self.ben)

    def test_a_row_number_is_not_a_site_id(self):
        self.assert_same_404(str(self.ben_site.pk), client=self.ben)

    def test_a_product_is_never_reached_through_its_row_number(self):
        reference = self.get(products_url(MISSING_SITE)).json()
        site_id, number = self.ben_site.public_id, str(self.product.pk)
        for response in (
            self.patch(product_url(site_id, number), {"revision": 1, "name": "X"}, client=self.ben),
            self.get(revisions_url(site_id, number), client=self.ben),
        ):
            self.assertEqual((response.status_code, response.json()), (404, reference))
        self.assertEqual(CatalogProduct.objects.get().name, "Bens Secret Name")


class SwitchedOffTests(CatalogApiTestCase):
    """Off, every route answers 404 signed in or not and no table is read: the code is released
    before migration catalog.0001 is applied. The catalog is on only while both switches are."""

    def test_every_route_answers_404_and_runs_no_query(self):
        self.designate()
        product = make_product()
        before = catalog_rows()
        calls = [
            lambda: self.get(self.products),
            lambda: self.create(),
            lambda: self.update(product.public_id, {"revision": 1, "name": "X"}),
            lambda: self.arrange([product.public_id]),
            lambda: self.revisions(product.public_id),
            lambda: self.post(
                f"{self.products}{product.public_id}/image/", {"revision": 1}
            ),
            lambda: self.client.delete(
                f"{self.products}{product.public_id}/image/?revision=1", secure=True
            ),
            lambda: self.post(self.preview_url, {"weight_kg": "1.00"}),
        ]
        self.assertEqual(len(calls), len(ROUTES))
        off = (
            {"SANTE_CATALOG_ENABLED": False},
            {"SANTE_SETTINGS_ENABLED": False},
            {"SANTE_CATALOG_ENABLED": False, "SANTE_SETTINGS_ENABLED": False},
        )
        for signed_in in (True, False):
            if not signed_in:
                self.client.cookies.clear()
            for switches in off:
                for index, call in enumerate(calls):
                    with self.subTest(signed_in=signed_in, off=sorted(switches), route=ROUTES[index]):
                        with override_settings(**switches), self.assertNumQueries(0):
                            self.error(call(), 404, "not_found")
        self.assertEqual(catalog_rows(), before)
