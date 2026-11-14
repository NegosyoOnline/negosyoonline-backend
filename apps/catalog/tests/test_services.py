"""The catalog rules below the API. Restates TWCinc's test_catalog and test_services for
centavos, public ids and this repository's errors."""
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from apps.catalog import services
from apps.catalog.models import Catalog, CatalogProduct, CatalogProductRevision
from apps.catalog.tests.base import (
    MISSING_PRODUCT, designate, make_product, retemplate, switched_on,
)
from apps.common.errors import ApiError
from apps.operators.models import OperatorAuditEntry
from apps.sites.tests.base import make_account, make_site

VALID = {
    "code": " fx-alpha ", "name": " Fixture Alpha ", "srp_centavos": 9999,
    "earnings_rate": "0.3500", "short_description": "", "long_description": "",
    "weight_kg": "0.00",
}
M = services.FIELD_MESSAGES


@switched_on
class ReadServiceTests(TestCase):
    def setUp(self):
        self.site = make_site(make_account())

    def test_products_are_scoped_by_template_and_by_activity(self):
        self.assertEqual(list(services.products_for_site(self.site)), [])
        self.assertFalse(Catalog.objects.exists())  # a read never creates the catalog
        alpha = make_product("FX-ALPHA")
        beta = make_product("FX-BETA", active=False)
        make_product("FX-OTHER", key="othercat")
        self.assertEqual(list(services.products_for_site(self.site)), [alpha])
        self.assertEqual(
            list(services.products_for_site(self.site, include_inactive=True)), [alpha, beta]
        )
        plain = retemplate(make_site(make_account("ben@example.test"), "plain", "Plain"))
        self.assertEqual(services.catalog_key_for(plain), "")
        self.assertEqual(list(services.products_for_site(plain)), [])
        self.assertEqual(services.catalog_key_for(SimpleNamespace(template="ghost")), "")

    def test_the_shape_of_a_product(self):
        product = make_product("FX-ALPHA", srp_centavos=9999, rate="0.3500")
        out = services.product_out(product)
        self.assertEqual(set(out), {
            "id", "code", "name", "short_description", "long_description", "weight_kg",
            "srp_centavos", "earnings_rate", "unit_members_price_centavos", "is_active",
            "revision", "updated_at", "volume_slot", "shipping_qty", "length_cm", "width_cm",
            "height_cm", "category", "image_url", "image_urls", "features", "specifications",
            "advantages", "benefits", "servings_per_pack", "faq",
        })
        self.assertEqual(
            (out["id"], out["srp_centavos"], out["earnings_rate"],
             out["unit_members_price_centavos"], out["weight_kg"], out["revision"],
             out["is_active"]),
            (product.public_id, 9999, "0.3500", 6499, "0.00", 1, True),
        )

    def test_a_revision_never_names_its_writer(self):
        out = services.revision_out(make_product().revisions.get())
        self.assertEqual(out["source"], "seed")
        self.assertFalse([name for name in out if name.startswith(("changed_by", "seed_"))])
        self.assertNotIn("id", out)

    def test_the_catalog_is_on_only_while_both_switches_are(self):
        self.assertTrue(services.enabled())
        make_product()
        for off in ({"SANTE_CATALOG_ENABLED": False}, {"SANTE_SETTINGS_ENABLED": False}):
            with self.subTest(off=off), override_settings(**off), self.assertNumQueries(0):
                self.assertFalse(services.enabled())
                self.assertEqual(services.catalog_key_for(self.site), "")
                self.assertEqual(list(services.products_for_site(self.site)), [])
                self.assertFalse(services.is_writer(self.site))


class FieldRuleTests(SimpleTestCase):
    def test_what_is_accepted(self):
        cleaned, errors = services.field_errors({
            "code": " fx-a.b_c ", "name": " Name ", "short_description": "x" * 500,
            "long_description": "x" * 20000, "weight_kg": "12345678.25", "srp_centavos": 1,
            "earnings_rate": "0.5", "volume_slot": "9999.99", "shipping_qty": 1,
            "length_cm": "99999.9", "is_active": False,
        })
        self.assertEqual(errors, {})
        self.assertEqual(cleaned, {
            "code": "FX-A.B_C", "name": "Name", "short_description": "x" * 500,
            "long_description": "x" * 20000, "weight_kg": Decimal("12345678.25"),
            "srp_centavos": 1, "earnings_rate": Decimal("0.5"), "volume_slot": Decimal("9999.99"),
            "shipping_qty": 1, "length_cm": Decimal("99999.9"), "is_active": False,
        })
        self.assertEqual(
            services.field_errors({"srp_centavos": services.SRP_MAX_CENTAVOS})[1], {}
        )

    def test_what_is_refused_with_its_own_message(self):
        cases = [
            ("code", "a b"), ("code", ""), ("code", "-X"), ("code", "X" * 65), ("code", None),
            ("name", ""), ("name", "   "), ("name", "x" * 121), ("name", None),
            ("short_description", "x" * 501), ("long_description", "x" * 20001),
            ("weight_kg", "-1"), ("weight_kg", "1.234"), ("weight_kg", "123456789"),
            ("weight_kg", 1), ("srp_centavos", 0), ("srp_centavos", -1),
            ("srp_centavos", services.SRP_MAX_CENTAVOS + 1), ("srp_centavos", "9999"),
            ("srp_centavos", 99.99), ("srp_centavos", True), ("srp_centavos", None),
            ("earnings_rate", "0.0999"), ("earnings_rate", "0.5001"),
            ("earnings_rate", "0.12345"), ("earnings_rate", "1"), ("earnings_rate", 0.35),
            ("volume_slot", "-1"), ("volume_slot", "1.234"), ("volume_slot", "0"),
            ("volume_slot", "0.00"), ("volume_slot", None), ("shipping_qty", 0),
            ("shipping_qty", -1), ("shipping_qty", True), ("shipping_qty", "2"),
            ("shipping_qty", None),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                self.assertEqual(services.field_errors({name: value}), ({}, {name: M[name]}))

    def test_a_size_needs_one_decimal_at_most_and_is_above_zero(self):
        for bad in ("0", "0.0", "-1.0", "1.25", "abc", "", None):
            with self.subTest(bad=bad):
                self.assertIn("length_cm", services.field_errors({"length_cm": bad})[1])
        self.assertEqual(
            services.field_errors({"length_cm": "12.5"})[0], {"length_cm": Decimal("12.5")}
        )

    def test_sizes_are_all_three_or_none(self):
        one = Decimal("1")
        self.assertEqual(
            services.dimension_errors({"length_cm": one}),
            {"width_cm": services.DIMS_ALL_MSG, "height_cm": services.DIMS_ALL_MSG},
        )
        self.assertEqual(
            services.dimension_errors({"length_cm": one, "width_cm": one, "height_cm": one}), {}
        )
        self.assertEqual(services.dimension_errors({}), {})
        existing = SimpleNamespace(length_cm=one, width_cm=Decimal("2"), height_cm=Decimal("3"))
        self.assertEqual(services.dimension_errors({"length_cm": Decimal("5")}, existing), {})

    def test_shipping_inputs_need_a_measured_weight(self):
        slot, message = {"volume_slot": Decimal("1.5")}, services.SHIPPING_WEIGHT_MSG
        self.assertEqual(services.shipping_weight_error(slot), message)
        self.assertEqual(services.shipping_weight_error({"shipping_qty": 1}), message)
        self.assertEqual(
            services.shipping_weight_error({**slot, "weight_kg": Decimal("0.00")}), message
        )
        self.assertIsNone(services.shipping_weight_error({**slot, "weight_kg": Decimal("0.18")}))
        self.assertIsNone(services.shipping_weight_error({}))
        on_file = dict(existing_weight=Decimal("0.18"), update=True)
        self.assertIsNone(services.shipping_weight_error(slot, **on_file))
        self.assertEqual(
            services.shipping_weight_error({"weight_kg": Decimal("0")}, **on_file), message
        )
        self.assertEqual(
            services.shipping_weight_error(slot, existing_weight=Decimal("0"), update=True),
            message,
        )


@switched_on
class WriteServiceTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.account = make_account()
        self.site = make_site(self.account)
        designate(self.site)

    def refused(self, call, status, code):
        with self.assertRaises(ApiError) as caught:
            call()
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
        return caught.exception

    def fields_of(self, call):
        return self.refused(call, 400, "invalid_request").fields

    def create(self, **changes):
        return services.create_product(self.account, self.site, {**VALID, **changes})

    def update(self, product, **data):
        return services.update_product(self.account, self.site, product.public_id, data)


class WriteServiceTests(WriteServiceTestCase):
    def test_a_create_appends_revision_one(self):
        product = self.create()
        self.assertEqual(
            (product.code, product.name, product.revision, product.is_active),
            ("FX-ALPHA", "Fixture Alpha", 1, True),
        )
        revision = product.revisions.get()
        self.assertEqual(
            (revision.revision, revision.source, revision.changed_by_account_id,
             revision.changed_by_site_id, revision.srp_centavos, revision.code),
            (1, "owner", self.account.pk, self.site.pk, 9999, "FX-ALPHA"),
        )
        self.assertEqual(
            (revision.changed_by_operator_user_id, revision.changed_by_operator_username,
             revision.seed_file_sha256),
            (None, "", ""),
        )
        self.assertEqual(list(Catalog.objects.values_list("template_key", flat=True)), ["sante"])

    def test_an_update_adds_one_revision_and_an_unchanged_one_writes_nothing(self):
        product = self.create()
        changed = self.update(product, revision=1, srp_centavos=10000)
        self.assertEqual((changed.revision, product.revisions.count()), (2, 2))
        same = self.update(product, revision=2, srp_centavos=10000, name="Fixture Alpha")
        self.assertEqual((same.revision, product.revisions.count()), (2, 2))
        self.refused(lambda: self.update(product, revision=1, name="Late"), 409, "stale_revision")
        self.assertEqual(CatalogProduct.objects.get(pk=product.pk).name, "Fixture Alpha")
        latest = product.revisions.get(revision=2)
        self.assertEqual((latest.srp_centavos, latest.name), (10000, "Fixture Alpha"))

    def test_a_taken_code_is_refused_whatever_its_case(self):
        self.create()
        error = self.refused(lambda: self.create(code="FX-alpha"), 409, "code_taken")
        self.assertEqual(error.fields, {"code": [services.CODE_TAKEN_MSG]})
        self.assertEqual(CatalogProduct.objects.count(), 1)

    def test_a_missing_product_or_one_of_another_catalog_is_not_found(self):
        foreign = make_product("FX-FOREIGN", key="othercat")
        for product_id in (MISSING_PRODUCT, foreign.public_id, str(foreign.pk)):
            with self.subTest(product_id=product_id):
                self.refused(
                    lambda: services.update_product(
                        self.account, self.site, product_id, {"revision": 1, "name": "X"}
                    ),
                    404, "not_found",
                )
        self.assertEqual(CatalogProduct.objects.get(pk=foreign.pk).name, "Fixture Alpha")

    def test_only_the_owner_site_writes(self):
        other_account = make_account("ben@example.test")
        other = make_site(other_account, "bens-store", "Ben's Store")
        product = make_product()
        self.refused(
            lambda: services.create_product(other_account, other, VALID), 403, "catalog_read_only"
        )
        self.refused(
            lambda: services.update_product(
                other_account, other, product.public_id, {"revision": 1, "name": "X"}
            ),
            403, "catalog_read_only",
        )
        self.refused(
            lambda: services.revisions_for(other, product.public_id), 403, "catalog_read_only"
        )
        # The owner site, called with an account that does not own it, is not found.
        self.refused(
            lambda: services.create_product(other_account, self.site, VALID), 404, "not_found"
        )
        self.assertEqual(CatalogProduct.objects.count(), 1)
        self.assertEqual(CatalogProductRevision.objects.count(), 1)

    def test_an_owner_write_is_not_an_operator_audit_entry(self):
        before = OperatorAuditEntry.objects.count()
        self.update(self.create(), revision=1, name="Renamed")
        self.assertEqual(OperatorAuditEntry.objects.count(), before)

    def test_writes_are_limited_per_account(self):
        product = self.create()
        cache.clear()
        with mock.patch.object(services, "WRITE_LIMIT", 2):
            self.update(product, revision=1, name="One")
            self.update(product, revision=2, name="Two")
            self.refused(lambda: self.update(product, revision=3, name="Three"), 429, "rate_limited")
        self.assertEqual(CatalogProduct.objects.get(pk=product.pk).revision, 3)


class DimensionWriteTests(WriteServiceTestCase):
    """Sizes are all three or none, each above 0, through create and update."""

    DIMS = {"length_cm": "18.6", "width_cm": "5.1", "height_cm": "6.2"}

    def test_a_create_without_sizes_is_an_unmeasured_draft(self):
        product = self.create()
        self.assertEqual(
            (product.length_cm, product.width_cm, product.height_cm), (None, None, None)
        )

    def test_a_create_with_all_three_stores_them_and_the_revision(self):
        product = self.create(**self.DIMS)
        revision = product.revisions.get()
        sizes = (Decimal("18.6"), Decimal("5.1"), Decimal("6.2"))
        self.assertEqual((product.length_cm, product.width_cm, product.height_cm), sizes)
        self.assertEqual((revision.length_cm, revision.width_cm, revision.height_cm), sizes)

    def test_a_create_with_only_some_is_refused_and_names_the_missing_ones(self):
        fields = self.fields_of(lambda: self.create(length_cm="5.0"))
        self.assertEqual(
            fields, {"width_cm": [services.DIMS_ALL_MSG], "height_cm": [services.DIMS_ALL_MSG]}
        )
        self.assertFalse(CatalogProduct.objects.exists())

    def test_zero_negative_and_two_decimals_are_refused(self):
        for bad in ("0", "0.0", "-1.0", "1.25"):
            with self.subTest(bad=bad):
                fields = self.fields_of(lambda: self.create(**{**self.DIMS, "height_cm": bad}))
                self.assertEqual(fields, {"height_cm": [M["height_cm"]]})

    def test_an_update_giving_one_with_none_on_file_is_refused(self):
        product = self.create()
        fields = self.fields_of(lambda: self.update(product, revision=1, width_cm="4.0"))
        self.assertEqual(set(fields), {"length_cm", "height_cm"})

    def test_an_update_giving_one_with_all_on_file_changes_just_that_one(self):
        product = self.create(**self.DIMS)
        changed = self.update(product, revision=1, width_cm="4.0")
        self.assertEqual(
            (changed.length_cm, changed.width_cm, changed.height_cm, changed.revision),
            (Decimal("18.6"), Decimal("4.0"), Decimal("6.2"), 2),
        )

    def test_an_update_cannot_clear_a_size(self):
        product = self.create(**self.DIMS)
        fields = self.fields_of(lambda: self.update(product, revision=1, length_cm=None))
        self.assertEqual(fields, {"length_cm": [M["length_cm"]]})


class DerivedSlotTests(WriteServiceTestCase):
    """The volume slot is derived from the sizes on create and update (CAT-1 r3, volume slot)."""

    EXACT = {"length_cm": "23", "width_cm": "23", "height_cm": "0.6"}  # 317.4 cm3: 1.0 slot
    OVER = {"length_cm": "23.1", "width_cm": "23", "height_cm": "0.6"}  # 318.78 cm3: rounds up

    def test_a_create_derives_the_slot_at_a_boundary_and_just_over_it(self):
        self.assertEqual(self.create(code="A", **self.EXACT).volume_slot, Decimal("1.00"))
        self.assertEqual(self.create(code="B", **self.OVER).volume_slot, Decimal("1.10"))
        double = {**self.EXACT, "height_cm": "1.2"}  # 634.8 cm3: exactly 2.0
        self.assertEqual(self.create(code="C", **double).volume_slot, Decimal("2.00"))
        self.assertEqual(self.create(code="D", **{**double, "length_cm": "23.1"}).volume_slot,
                         Decimal("2.10"))

    def test_a_create_without_sizes_has_no_slot_even_if_one_is_sent(self):
        product = self.create(volume_slot="3.5", weight_kg="0.50")
        self.assertIsNone(product.volume_slot)

    def test_a_slot_sent_with_sizes_is_overridden(self):
        product = self.create(volume_slot="9.9", weight_kg="0.50", **self.OVER)
        self.assertEqual(product.volume_slot, Decimal("1.10"))
        product = self.update(product, revision=1, volume_slot="5", name="Renamed")
        self.assertEqual(product.volume_slot, Decimal("1.10"))

    def test_an_update_of_the_sizes_rederives_and_the_revision_records_it(self):
        product = self.create(**self.EXACT)
        product = self.update(product, revision=1, height_cm="1.2")
        self.assertEqual(product.volume_slot, Decimal("2.00"))
        product = self.update(product, revision=2, length_cm="23.1")
        self.assertEqual(product.volume_slot, Decimal("2.10"))
        stored = CatalogProduct.objects.get(pk=product.pk)
        self.assertEqual(stored.volume_slot, Decimal("2.10"))
        self.assertEqual(
            list(product.revisions.order_by("revision").values_list("volume_slot", flat=True)),
            [Decimal("1.00"), Decimal("2.00"), Decimal("2.10")],
        )

    def test_a_product_gaining_sizes_on_update_gets_a_slot(self):
        product = self.create()
        product = self.update(product, revision=1, **self.OVER)
        self.assertEqual(product.volume_slot, Decimal("1.10"))

    def test_an_update_not_touching_sizes_keeps_the_stored_slot(self):
        product = self.create(weight_kg="0.50")
        CatalogProduct.objects.filter(pk=product.pk).update(volume_slot=Decimal("4.00"))
        product = self.update(product, revision=1, name="Renamed", volume_slot="7")
        self.assertEqual(product.volume_slot, Decimal("4.00"))

    def test_sizes_too_large_for_the_column_are_refused(self):
        big = {"length_cm": "99999", "width_cm": "99999", "height_cm": "99999"}
        self.assertEqual(set(self.fields_of(lambda: self.create(**big))), set(services.DIM_FIELDS))
        self.assertFalse(CatalogProduct.objects.exists())

    def test_the_seed_derives_from_sizes_and_keeps_a_slot_given_alone(self):
        row = {"code": "S1", "name": "Seed", "srp_centavos": 100, "earnings_rate": "0.3000",
               "weight_kg": "0.20"}
        self.assertNotIn("volume_slot", services.seed_clean(row)[1])
        self.assertEqual(services.seed_clean({**row, "volume_slot": "2.5"})[1]["volume_slot"],
                         Decimal("2.50"))
        cleaned = services.seed_clean({**row, "volume_slot": "2.5", **self.OVER})[1]
        self.assertEqual(cleaned["volume_slot"], Decimal("1.10"))

    def test_preview_and_save_give_the_same_slot(self):
        sizes = {name: Decimal(value) for name, value in self.OVER.items()}
        self.assertEqual(services.slot_from_sizes(*sizes.values()), self.create(**self.OVER).volume_slot)
        out = services.shipping_preview(self.site, {
            "weight_kg": "0.50", "shipping_qty": 1, "volume_slot": "9", **self.OVER,
        })
        self.assertIsNotNone(out["ncr_centavos"])  # priced on the derived slot, not the 9 sent
        derived = services.shipping_preview(self.site, {
            "weight_kg": "0.50", "shipping_qty": 1, **self.OVER,
        })
        self.assertEqual(out, derived)

    def test_a_saved_product_gets_a_checkout_shipping_quote_under_both_policies(self):
        from apps.shop import quote, shipping

        product = self.create(weight_kg="0.50", shipping_qty=1, **self.OVER)
        line = quote.line_of_product(product, 1)  # the lines checkout prices
        item = shipping.ShippingItem(line.weight_kg, line.volume_slot, line.shipping_qty, 1)
        for policy in ("zero", "charge"):
            with self.subTest(policy=policy), override_settings(SHOP_SHIPPING_POLICY=policy):
                result = shipping.quote([item], {"city": "Quezon City", "province": "Metro Manila"})
                self.assertIsNotNone(result.computed_centavos)
                self.assertEqual(result.charged_centavos == 0, policy == "zero")
