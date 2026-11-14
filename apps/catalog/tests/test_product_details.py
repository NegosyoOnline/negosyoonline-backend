"""CAT-1 round 3: the four detail texts, the five photo slots and the shipping preview."""
from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings

from apps.accounts.tests.base import ORIGIN
from apps.billing.tests.base import make_subscription
from apps.catalog import services
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import (
    JPEG, MEDIA, CatalogApiTestCase, FakeMedia, catalog_rows, image_file, make_product,
    preview_url, switched_on,
)
from apps.shop import zones
from apps.storefront.tests.base import SurfaceTestCase

TEXTS = ("features", "specifications", "advantages", "benefits")


class DetailTextTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()

    def test_a_create_without_the_texts_has_four_blanks(self):
        body = self.created()
        self.assertEqual([body[name] for name in TEXTS], ["", "", "", ""])

    def test_the_texts_are_stored_on_create_and_in_the_first_revision(self):
        values = {name: f"{name} text" for name in TEXTS}
        body = self.created(**values)
        for name in TEXTS:
            self.assertEqual(body[name], values[name])
        row = self.revisions(body["id"]).json()["revisions"][0]
        for name in TEXTS:
            self.assertEqual(row[name], values[name])

    def test_an_update_changes_each_text_and_writes_a_revision(self):
        product = self.created()
        for number, name in enumerate(TEXTS, start=1):
            response = self.update(product["id"], {"revision": number, name: f"new {name}"})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual((response.json()[name], response.json()["revision"]), (f"new {name}", number + 1))
        rows = self.revisions(product["id"]).json()["revisions"]
        self.assertEqual(len(rows), 5)
        self.assertEqual([row["features"] for row in rows], ["new features"] * 4 + [""])
        self.assertEqual([row["benefits"] for row in rows], ["new benefits", "", "", "", ""])

    def test_the_same_text_writes_no_revision_and_a_text_can_be_blanked(self):
        product = self.created(features="Fast")
        same = self.update(product["id"], {"revision": 1, "features": "Fast"}).json()
        self.assertEqual(same["revision"], 1)
        blanked = self.update(product["id"], {"revision": 1, "features": ""}).json()
        self.assertEqual((blanked["features"], blanked["revision"]), ("", 2))

    def test_a_text_of_5000_characters_is_accepted_and_5001_is_a_field_error(self):
        for name in TEXTS:
            with self.subTest(name=name):
                ok = self.create(code=f"FX-{name[:3].upper()}", **{name: "x" * 5000})
                self.assertEqual(ok.status_code, 201, ok.content)
                too_long = self.create(code=f"FX-L{name[:3].upper()}", **{name: "x" * 5001})
                self.assertEqual(self.fields(too_long), {name: [services.FIELD_MESSAGES[name]]})
        product = self.created(code="FX-UPD")
        response = self.update(product["id"], {"revision": 1, "benefits": "y" * 5001})
        self.assertIn("benefits", self.fields(response))
        response = self.update(product["id"], {"revision": 1, "benefits": None})
        self.assertIn("benefits", self.fields(response))

    def test_only_the_owner_site_writes_the_texts(self):
        product = self.created()
        before = catalog_rows()
        response = self.update(
            product["id"], {"revision": 1, "features": "mine"}, site=self.ben_site, client=self.ben
        )
        self.error(response, 403, "catalog_read_only")
        self.assertEqual(catalog_rows(), before)


class PhotoSlotTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()
        self.product = self.created()
        self.url = f"{self.products}{self.product['id']}/image/"
        cache.clear()

    def row(self):
        return CatalogProduct.objects.get()

    def upload(self, slot=None, client=None, url=None, file=None):
        suffix = "" if slot is None else f"?slot={slot}"
        data = {"file": file or image_file(), "revision": self.row().revision}
        return (client or self.client).post(
            (url or self.url) + suffix, data=data, secure=True, HTTP_ORIGIN=ORIGIN
        )

    def remove(self, slot=None, revision=None, client=None, url=None):
        query = f"revision={revision or self.row().revision}" + ("" if slot is None else f"&slot={slot}")
        return (client or self.client).delete(
            f"{url or self.url}?{query}", secure=True, HTTP_ORIGIN=ORIGIN
        )

    def test_a_new_product_has_five_empty_slots(self):
        self.assertEqual(self.product["image_urls"], [None] * 5)
        self.assertIsNone(self.product["image_url"])

    def test_each_slot_stores_only_itself_and_writes_one_revision(self):
        with FakeMedia() as store:
            for slot in range(1, 6):
                response = self.upload(slot)
                self.assertEqual(response.status_code, 200, response.content)
                body = response.json()
                self.assertEqual(body["revision"], slot + 1)
                keys = [self.row().image_key] + [getattr(self.row(), f"image_key_{n}") for n in (2, 3, 4, 5)]
                self.assertEqual([bool(key) for key in keys], [n <= slot for n in range(1, 6)])
                self.assertEqual(
                    [url is not None for url in body["image_urls"]], [n <= slot for n in range(1, 6)]
                )
                self.assertEqual(body["image_url"], body["image_urls"][0])
            self.assertEqual(len(store.files), 5)
        revisions = list(self.row().revisions.order_by("revision"))
        self.assertEqual(len(revisions), 6)
        self.assertEqual(revisions[2].image_key_2, self.row().image_key_2)
        self.assertEqual(revisions[2].image_key_3, "")
        self.assertEqual(revisions[5].image_key_5, self.row().image_key_5)
        self.assertTrue(all(revision.source == "owner" for revision in revisions[1:]))

    def test_the_default_slot_is_slot_one(self):
        with FakeMedia():
            body = self.upload().json()
        self.assertIsNotNone(body["image_urls"][0])
        self.assertEqual(body["image_urls"][1:], [None] * 4)
        self.assertEqual(body["image_url"], body["image_urls"][0])

    def test_replacing_a_slot_removes_only_its_old_file(self):
        with FakeMedia() as store:
            self.upload(1)
            self.upload(3)
            first_three = self.row().image_key_3
            kept = self.row().image_key
            with self.captureOnCommitCallbacks(execute=True):
                self.upload(3, file=image_file(JPEG, "image/jpeg", "p.jpg"))
            self.assertEqual(store.deleted, [first_three])
            self.assertEqual(self.row().image_key, kept)
            self.assertNotEqual(self.row().image_key_3, first_three)

    def test_clearing_a_slot_clears_only_that_slot(self):
        with FakeMedia() as store:
            for slot in (1, 2, 4):
                self.upload(slot)
            row = self.row()
            keys = {1: row.image_key, 2: row.image_key_2, 4: row.image_key_4}
            before = row.revision
            with self.captureOnCommitCallbacks(execute=True):
                response = self.remove(2)
            self.assertEqual(response.status_code, 200, response.content)
            body = response.json()
            self.assertEqual(body["revision"], before + 1)
            self.assertEqual(
                [url is None for url in body["image_urls"]], [False, True, True, False, True]
            )
            self.assertEqual(store.deleted, [keys[2]])
        row = self.row()
        self.assertEqual((row.image_key, row.image_key_2, row.image_key_4), (keys[1], "", keys[4]))
        latest = row.revisions.get(revision=before + 1)
        self.assertEqual((latest.image_key, latest.image_key_2, latest.image_key_4), (keys[1], "", keys[4]))

    def test_clearing_an_empty_slot_changes_nothing(self):
        with FakeMedia():
            self.upload(1)
        before = catalog_rows()
        response = self.remove(5)
        self.assertEqual((response.status_code, response.json()["revision"]), (200, 2))
        self.assertEqual(catalog_rows(), before)

    def test_slot_one_clear_keeps_the_other_slots(self):
        with FakeMedia():
            self.upload(1)
            self.upload(2)
            body = self.remove(1).json()
        self.assertEqual([url is None for url in body["image_urls"]], [True, False, True, True, True])
        self.assertIsNone(body["image_url"])

    def test_a_slot_outside_1_to_5_is_a_field_error_and_stores_nothing(self):
        before = catalog_rows()
        with FakeMedia() as store:
            for slot in ("0", "6", "-1", "x", "1.5", ""):
                with self.subTest(slot=slot):
                    self.assertIn("slot", self.fields(self.upload(slot)))
                    self.assertIn("slot", self.fields(self.remove(slot)))
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)

    def test_a_stale_revision_is_refused_for_any_slot(self):
        with FakeMedia():
            response = self.client.post(
                f"{self.url}?slot=3", data={"file": image_file(), "revision": 9}, secure=True,
                HTTP_ORIGIN=ORIGIN,
            )
            self.error(response, 409, "stale_revision")
        self.error(self.remove(3, revision=9), 409, "stale_revision")

    def test_only_the_owner_may_change_any_slot(self):
        url = f"{self.ben_site.public_id.join(('/v1/sites/', '/catalog/products/'))}{self.product['id']}/image/"
        before = catalog_rows()
        with FakeMedia() as store:
            for slot in range(1, 6):
                self.error(self.upload(slot, client=self.ben, url=url), 403, "catalog_read_only")
                self.error(self.remove(slot, client=self.ben, url=url), 403, "catalog_read_only")
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)

    def test_another_accounts_site_is_the_same_404_on_every_slot(self):
        url = f"/v1/sites/{self.ben_site.public_id}/catalog/products/{self.product['id']}/image/"
        # Ben's site is not the catalog owner, but the 404 is for a site that is not his: Cora
        # naming Ben's site gets 404 before any slot is looked at.
        with FakeMedia():
            for slot in (1, 3, 5):
                self.error(self.upload(slot, url=url), 404, "not_found")
                self.error(self.remove(slot, url=url), 404, "not_found")
            # A slot that is invalid is still a 404 first on a site that is not the caller's.
            self.error(self.upload(9, url=url), 404, "not_found")
            self.error(self.remove(9, url=url), 404, "not_found")


class PreviewTests(CatalogApiTestCase):
    BODY = {"weight_kg": "0.50", "length_cm": "10", "width_cm": "10", "height_cm": "10", "shipping_qty": 1}

    def setUp(self):
        super().setUp()
        self.designate()

    def preview(self, body=None, site=None, client=None):
        return self.post(preview_url((site or self.site).public_id), body, client=client)

    def test_it_prices_one_unit_with_the_shop_fee_code(self):
        from apps.shop import shipping

        body = self.preview(self.BODY).json()
        item = shipping.ShippingItem(Decimal("0.50"), Decimal("3.2"), 1, 1)  # 1000 cm3 / 317.4
        self.assertEqual(body["ncr_centavos"], shipping.fee_centavos([item], "MANILA"))
        self.assertEqual(body["mindanao_centavos"], shipping.fee_centavos([item], "MINDANAO"))
        self.assertEqual(body["billable_kg"], "0.50")
        self.assertGreater(body["mindanao_centavos"], body["ncr_centavos"])
        self.assertIsInstance(body["reason"], str)

    def test_the_two_zones_are_chosen_by_the_shop_zone_code(self):
        self.assertEqual(zones.dest_zone(services.PREVIEW_NCR), "MANILA")
        self.assertEqual(zones.dest_zone(services.PREVIEW_MINDANAO), "MINDANAO")

    def test_a_box_bills_its_minimum_weight(self):
        body = self.preview({**self.BODY, "length_cm": "40", "width_cm": "30", "height_cm": "15"}).json()
        self.assertEqual(body["billable_kg"], "6.00")  # 18000 cm3 is 56.7 slots: a medium box, 6 kg floor
        self.assertIsNotNone(body["ncr_centavos"])

    def test_units_per_box_scale_weight_and_volume(self):
        one = self.preview(self.BODY).json()
        many = self.preview({**self.BODY, "shipping_qty": 6}).json()
        self.assertGreater(many["ncr_centavos"], one["ncr_centavos"])

    def test_a_given_volume_slot_without_sizes_is_priced_as_it_is(self):
        from apps.shop import shipping

        only = {"weight_kg": "0.50", "shipping_qty": 1, "volume_slot": "60.00"}
        body = self.preview(only).json()
        item = shipping.ShippingItem(Decimal("0.50"), Decimal("60.00"), 1, 1)
        self.assertEqual(body["ncr_centavos"], shipping.fee_centavos([item], "MANILA"))
        self.assertEqual(body["mindanao_centavos"], shipping.fee_centavos([item], "MINDANAO"))
        self.assertEqual(body["billable_kg"], "6.00")  # a medium box, not the derived pouch
        self.assertNotEqual(body["ncr_centavos"], self.preview(self.BODY).json()["ncr_centavos"])

    def test_with_sizes_a_given_volume_slot_is_ignored_as_a_save_ignores_it(self):
        self.assertEqual(
            self.preview({**self.BODY, "volume_slot": "60.00"}).json(), self.preview(self.BODY).json()
        )

    def test_a_given_volume_slot_needs_no_sizes_and_overrides_them(self):
        only = self.preview({"weight_kg": "0.50", "shipping_qty": 1, "volume_slot": "3.20"}).json()
        self.assertEqual(only, {**self.preview(self.BODY).json()})
        self.assertIsNotNone(only["ncr_centavos"])

    def test_a_bad_volume_slot_is_a_field_error(self):
        for value in ("0", "abc", "1.234"):
            with self.subTest(value=value):
                response = self.preview({**self.BODY, "volume_slot": value})
                self.assertEqual(
                    self.fields(response), {"volume_slot": [services.FIELD_MESSAGES["volume_slot"]]}
                )

    def test_shipping_policy_charge_gives_no_reason(self):
        with override_settings(SHOP_SHIPPING_POLICY="charge"):
            self.assertEqual(self.preview(self.BODY).json()["reason"], "")
        with override_settings(SHOP_SHIPPING_POLICY="zero"):
            self.assertIn("not charged", self.preview(self.BODY).json()["reason"])

    def test_what_cannot_be_priced_gives_nulls_and_a_reason(self):
        cases = {
            "weight": {**self.BODY, "weight_kg": "0"},
            "dims": {**self.BODY, "height_cm": None},
            "no dims": {"weight_kg": "1.00", "shipping_qty": 1},
            "qty": {**self.BODY, "shipping_qty": None},
        }
        for label, body in cases.items():
            with self.subTest(label):
                response = self.preview(body)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(
                    response.json(),
                    {"ncr_centavos": None, "mindanao_centavos": None, "billable_kg": None,
                     "reason": response.json()["reason"]},
                )
                self.assertTrue(response.json()["reason"])

    def test_a_load_above_the_rate_table_gives_a_reason(self):
        body = self.preview(
            {**self.BODY, "length_cm": "40", "width_cm": "30", "height_cm": "15", "weight_kg": "99.00"}
        ).json()
        self.assertEqual((body["ncr_centavos"], body["billable_kg"]), (None, None))
        self.assertEqual(body["reason"], services.PREVIEW_REASONS["table"])

    def test_invalid_numbers_are_field_errors_with_the_product_messages(self):
        for field, value in (
            ("weight_kg", "1.234"), ("weight_kg", "abc"), ("length_cm", "0"),
            ("width_cm", "1.25"), ("height_cm", "-1"),
        ):
            with self.subTest(field=field, value=value):
                response = self.preview({**self.BODY, field: value})
                self.assertEqual(self.fields(response), {field: [services.FIELD_MESSAGES[field]]})
        self.assertIn("shipping_qty", self.fields(self.preview({**self.BODY, "shipping_qty": 0})))
        self.assertIn("weight_kg", self.fields(self.preview({"length_cm": "1"})))
        self.assertIn("price", self.fields(self.preview({**self.BODY, "price": 1})))

    def test_it_stores_nothing(self):
        make_product()
        before = catalog_rows()
        self.preview(self.BODY)
        self.preview({**self.BODY, "weight_kg": "x"})
        self.assertEqual(catalog_rows(), before)

    def test_a_non_writer_is_refused(self):
        self.error(self.preview(self.BODY, site=self.ben_site, client=self.ben), 403, "catalog_read_only")

    def test_another_accounts_site_is_the_same_404(self):
        self.error(self.preview(self.BODY, site=self.ben_site), 404, "not_found")
        self.error(self.preview({"bad": 1}, site=self.ben_site), 404, "not_found")

    def test_signed_out_is_refused(self):
        self.client.cookies.clear()
        self.error(self.preview(self.BODY), 401, "unauthenticated")


@switched_on
class StorefrontOutputTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.site = make_subscription().site

    def test_the_four_texts_and_five_image_urls_are_shown(self):
        product = make_product()
        CatalogProduct.objects.filter(pk=product.pk).update(
            features="F", specifications="S", advantages="A", benefits="B",
            image_key="catalog/products/a.png", image_key_3="catalog/products/c.png",
        )
        with override_settings(**MEDIA):
            listed = self.call(path="/storefront/v1/catalog/products/").json()["products"][0]
            detail = self.call(path=f"/storefront/v1/catalog/products/{product.public_id}/").json()
        for body in (listed, detail):
            self.assertEqual([body[name] for name in TEXTS], ["F", "S", "A", "B"])
            self.assertEqual(
                body["image_urls"],
                ["https://media.example.test/catalog/products/a.png", None,
                 "https://media.example.test/catalog/products/c.png", None, None],
            )
            self.assertEqual(body["image_url"], body["image_urls"][0])
            self.assertNotIn("image_key", " ".join(body))

    def test_an_empty_product_has_blank_texts_and_five_nulls(self):
        make_product()
        body = self.call(path="/storefront/v1/catalog/products/").json()["products"][0]
        self.assertEqual([body[name] for name in TEXTS], ["", "", "", ""])
        self.assertEqual((body["image_url"], body["image_urls"]), (None, [None] * 5))


class ServingsAndFaqTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()

    def test_a_create_without_them_has_no_servings_and_an_empty_faq(self):
        body = self.created()
        self.assertEqual((body["servings_per_pack"], body["faq"]), (None, []))

    def test_both_round_trip_through_create_update_revisions_and_the_list(self):
        faq = [{"q": "  How?  ", "a": " Like this. "}]
        body = self.created(servings_per_pack=30, faq=faq)
        self.assertEqual((body["servings_per_pack"], body["faq"]), (30, [{"q": "How?", "a": "Like this."}]))
        again = self.update(body["id"], {"revision": 1, "servings_per_pack": 60}).json()
        self.assertEqual((again["servings_per_pack"], again["revision"]), (60, 2))
        newer = self.update(body["id"], {"revision": 2, "faq": [{"q": "A", "a": "B"}]}).json()
        self.assertEqual((newer["faq"], newer["revision"]), ([{"q": "A", "a": "B"}], 3))
        rows = self.revisions(body["id"]).json()["revisions"]
        self.assertEqual([r["servings_per_pack"] for r in rows], [60, 60, 30])
        self.assertEqual([r["faq"] for r in rows],
                         [[{"q": "A", "a": "B"}], [{"q": "How?", "a": "Like this."}], [{"q": "How?", "a": "Like this."}]])
        listed = self.listed()["products"][0]
        self.assertEqual((listed["servings_per_pack"], listed["faq"]), (60, [{"q": "A", "a": "B"}]))

    def test_the_same_values_write_no_revision_and_null_and_empty_clear(self):
        body = self.created(servings_per_pack=5, faq=[{"q": "A", "a": "B"}])
        same = self.update(body["id"], {"revision": 1, "servings_per_pack": 5, "faq": [{"q": " A", "a": "B "}]})
        self.assertEqual(same.json()["revision"], 1)
        cleared = self.update(body["id"], {"revision": 1, "servings_per_pack": None, "faq": []}).json()
        self.assertEqual((cleared["servings_per_pack"], cleared["faq"], cleared["revision"]), (None, [], 2))

    def test_servings_must_be_a_whole_number_from_1_to_999(self):
        for good in (1, 999):
            ok = self.create(code=f"SV-{good}", servings_per_pack=good)
            self.assertEqual(ok.status_code, 201, ok.content)
        for bad in (0, 1000, -1, 1.5, "7", True):
            with self.subTest(bad=bad):
                response = self.create(code="SV-BAD", servings_per_pack=bad)
                self.assertIn("servings_per_pack", self.fields(response))
        product = self.created(code="SV-UPD")
        for bad in (0, 1000):
            response = self.update(product["id"], {"revision": 1, "servings_per_pack": bad})
            self.assertEqual(self.fields(response), {"servings_per_pack": [services.FIELD_MESSAGES["servings_per_pack"]]})

    def test_faq_limits_and_shape(self):
        pair = {"q": "Q", "a": "A"}
        ok = self.create(code="FQ-OK", faq=[pair] * 10)
        self.assertEqual(ok.status_code, 201, ok.content)
        ok = self.create(code="FQ-MAX", faq=[{"q": "q" * 150, "a": "a" * 1000}])
        self.assertEqual(ok.status_code, 201, ok.content)
        bad = {
            "eleven": [pair] * 11,
            "long q": [{"q": "q" * 151, "a": "A"}],
            "long a": [{"q": "Q", "a": "a" * 1001}],
            "empty q": [{"q": "  ", "a": "A"}],
            "empty a": [{"q": "Q", "a": ""}],
            "missing key": [{"q": "Q"}],
            "extra key": [{"q": "Q", "a": "A", "x": "y"}],
            "number": [{"q": 1, "a": "A"}],
            "not a dict": ["Q"],
        }
        for label, value in bad.items():
            with self.subTest(label):
                response = self.create(code="FQ-BAD", faq=value)
                self.assertEqual(self.fields(response), {"faq": [services.FIELD_MESSAGES["faq"]]})
        product = self.created(code="FQ-UPD")
        for value in ([pair] * 11, [{"q": "Q"}], None):
            response = self.update(product["id"], {"revision": 1, "faq": value})
            self.assertIn("faq", self.fields(response))

    def test_a_non_list_faq_is_refused(self):
        response = self.create(code="FQ-STR", faq="Q and A")
        self.assertEqual(response.status_code, 400, response.content)

    def test_only_the_owner_site_writes_them_and_others_see_the_same_values(self):
        product = self.created(servings_per_pack=10, faq=[{"q": "A", "a": "B"}])
        before = catalog_rows()
        response = self.update(
            product["id"], {"revision": 1, "servings_per_pack": 2, "faq": []},
            site=self.ben_site, client=self.ben,
        )
        self.error(response, 403, "catalog_read_only")
        self.assertEqual(catalog_rows(), before)
        seen = self.listed(site=self.ben_site, client=self.ben)["products"][0]
        self.assertEqual((seen["servings_per_pack"], seen["faq"]), (10, [{"q": "A", "a": "B"}]))
        stranger = self.update(product["id"], {"revision": 1, "faq": []}, site=self.site, client=self.ben)
        self.assertIn(stranger.status_code, (403, 404))
        self.assertEqual(catalog_rows(), before)


class IngredientTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()

    def item(self, name="Barley", line="Young barley grass.", slot=None):
        return {"name": name, "line": line, "photo_slot": slot}

    def test_a_create_without_them_has_none(self):
        self.assertEqual(self.created()["ingredients"], [])

    def test_they_round_trip_through_create_update_revisions_and_the_list(self):
        body = self.created(ingredients=[self.item("  Barley  ", " Green. ", 2)])
        self.assertEqual(
            body["ingredients"], [{"name": "Barley", "line": "Green.", "photo_slot": 2, "photo_url": None}]
        )
        newer = self.update(body["id"], {"revision": 1, "ingredients": [self.item("Kale", "", None)]}).json()
        self.assertEqual((newer["ingredients"][0]["name"], newer["revision"]), ("Kale", 2))
        rows = self.revisions(body["id"]).json()["revisions"]
        self.assertEqual([r["ingredients"][0]["name"] for r in rows], ["Kale", "Barley"])
        self.assertEqual(self.listed()["products"][0]["ingredients"][0]["name"], "Kale")

    def test_the_same_list_writes_no_revision_and_an_empty_list_clears(self):
        body = self.created(ingredients=[self.item()])
        same = self.update(body["id"], {"revision": 1, "ingredients": [self.item(" Barley ")]})
        self.assertEqual(same.json()["revision"], 1)
        cleared = self.update(body["id"], {"revision": 1, "ingredients": []}).json()
        self.assertEqual((cleared["ingredients"], cleared["revision"]), ([], 2))

    def test_limits_and_shape(self):
        for label, good in {
            "eight": [self.item()] * 8,
            "max name": [self.item("n" * 60)],
            "max line": [self.item(line="l" * 200)],
            "slot 5": [self.item(slot=5)],
        }.items():
            with self.subTest(label):
                ok = self.create(code=f"IG-{label}".upper().replace(" ", "-"), ingredients=good)
                self.assertEqual(ok.status_code, 201, ok.content)
        bad = {
            "nine": [self.item()] * 9,
            "long name": [self.item("n" * 61)],
            "empty name": [self.item("  ")],
            "long line": [self.item(line="l" * 201)],
            "slot 0": [self.item(slot=0)],
            "slot 6": [self.item(slot=6)],
            "slot text": [self.item(slot="1")],
            "slot bool": [self.item(slot=True)],
            "missing key": [{"name": "A", "line": "B"}],
            "extra key": [{**self.item(), "x": "y"}],
            "number name": [{"name": 1, "line": "", "photo_slot": None}],
            "not a dict": ["Barley"],
        }
        for label, value in bad.items():
            with self.subTest(label):
                response = self.create(code="IG-BAD", ingredients=value)
                self.assertEqual(self.fields(response), {"ingredients": [services.FIELD_MESSAGES["ingredients"]]})
        product = self.created(code="IG-UPD")
        for value in ([self.item()] * 9, None):
            self.assertIn("ingredients", self.fields(self.update(product["id"], {"revision": 1, "ingredients": value})))

    def test_a_slot_with_no_photo_is_saved_and_shows_no_photo_url(self):
        body = self.created(ingredients=[self.item(slot=3)])
        self.assertEqual(body["ingredients"][0]["photo_slot"], 3)
        self.assertIsNone(body["ingredients"][0]["photo_url"])

    def test_only_the_owner_site_writes_them(self):
        product = self.created(ingredients=[self.item()])
        before = catalog_rows()
        response = self.update(
            product["id"], {"revision": 1, "ingredients": []}, site=self.ben_site, client=self.ben
        )
        self.error(response, 403, "catalog_read_only")
        self.assertEqual(catalog_rows(), before)
