"""The three tables and their rules in the database. Restates TWCinc's test_models for
centavos and public ids."""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.catalog.models import Catalog, CatalogProduct, CatalogProductRevision
from apps.catalog.tests.base import make_product
from apps.common.ids import is_public_id

COPIED = {
    "code": ("CharField", 64, False), "name": ("CharField", 120, False),
    "short_description": ("TextField", None, False),
    "long_description": ("TextField", None, False),
    "category": ("CharField", 20, False), "image_key": ("CharField", 200, False),
    **{f"image_key_{n}": ("CharField", 200, False) for n in (2, 3, 4, 5)},
    **{name: ("TextField", None, False)
       for name in ("features", "specifications", "advantages", "benefits")},
    "servings_per_pack": ("PositiveSmallIntegerField", None, True),
    "faq": ("JSONField", None, False),
    "ingredients": ("JSONField", None, False),
    "weight_kg": ("DecimalField", (10, 2), False),
    "srp_centavos": ("PositiveBigIntegerField", None, False),
    "earnings_rate": ("DecimalField", (5, 4), False), "is_active": ("BooleanField", None, False),
    "volume_slot": ("DecimalField", (6, 2), True),
    "shipping_qty": ("PositiveIntegerField", None, True),
    **{name: ("DecimalField", (6, 1), True) for name in ("length_cm", "width_cm", "height_cm")},
}
EXPECTED = {
    Catalog: {
        "id": ("BigAutoField", None, False), "template_key": ("CharField", 30, False),
        "created_at": ("DateTimeField", None, False),
    },
    CatalogProduct: {
        "id": ("BigAutoField", None, False), "public_id": ("CharField", 40, False),
        "catalog": ("ForeignKey", ("catalog.Catalog", "PROTECT"), False),
        **COPIED, "display_order": ("PositiveIntegerField", None, False),
        "revision": ("PositiveIntegerField", None, False),
        "created_at": ("DateTimeField", None, False), "updated_at": ("DateTimeField", None, False),
    },
    CatalogProductRevision: {
        "id": ("BigAutoField", None, False),
        "product": ("ForeignKey", ("catalog.CatalogProduct", "PROTECT"), False),
        "revision": ("PositiveIntegerField", None, False), **COPIED,
        "source": ("CharField", 8, False),
        "changed_by_account_id": ("PositiveBigIntegerField", None, True),
        "changed_by_site_id": ("PositiveBigIntegerField", None, True),
        "changed_by_operator_user_id": ("PositiveBigIntegerField", None, True),
        "changed_by_operator_username": ("CharField", 150, False),
        "seed_file_sha256": ("CharField", 64, False),
        "changed_at": ("DateTimeField", None, False),
    },
}
CONSTRAINTS = {
    CatalogProduct: {
        "catalog_product_code_uniq", "catalog_product_srp_positive",
        "catalog_product_rate_bounds", "catalog_product_weight_nonneg",
        "catalog_product_revision_min", "catalog_product_code_upper",
        "catalog_product_slot_positive", "catalog_product_shipping_qty_min",
        "catalog_product_dims_all_or_none_positive",
    },
    CatalogProductRevision: {"catalog_revision_uniq", "catalog_revision_writer"},
}


def shape(field):
    kind = field.get_internal_type()
    if kind == "CharField":
        detail = field.max_length
    elif kind == "DecimalField":
        detail = (field.max_digits, field.decimal_places)
    elif kind == "ForeignKey":
        detail = (field.related_model._meta.label, field.remote_field.on_delete.__name__)
    else:
        detail = None
    return kind, detail, field.null


class SchemaTests(TestCase):
    def test_the_tables_are_the_ones_approved(self):
        for model, fields in EXPECTED.items():
            with self.subTest(model=model.__name__):
                found = {f.name: shape(f) for f in model._meta.concrete_fields}
                self.assertEqual(found, fields)
                self.assertEqual(
                    {c.name for c in model._meta.constraints}, CONSTRAINTS.get(model, set())
                )
        self.assertTrue(Catalog._meta.get_field("template_key").unique)
        self.assertTrue(CatalogProduct._meta.get_field("public_id").unique)
        self.assertEqual(CatalogProduct._meta.get_field("weight_kg").default, Decimal("0"))
        self.assertEqual(
            (CatalogProduct._meta.get_field("is_active").default,
             CatalogProduct._meta.get_field("revision").default),
            (True, 1),
        )

    def test_a_product_gets_an_opaque_public_id(self):
        product = make_product()
        self.assertTrue(is_public_id(product.public_id, "prod"))
        self.assertNotEqual(product.public_id, make_product("FX-BETA").public_id)

    def test_the_product_rules(self):
        catalog = Catalog.objects.create(template_key="sante")
        good = dict(
            catalog=catalog, code="FX-OK", name="Ok", srp_centavos=100,
            earnings_rate=Decimal("0.1000"),
        )
        CatalogProduct.objects.create(**good)
        refused = (
            dict(good, code="FX-1", srp_centavos=0),
            dict(good, code="FX-2", earnings_rate=Decimal("0.0999")),
            dict(good, code="FX-3", earnings_rate=Decimal("0.5001")),
            dict(good, code="FX-4", weight_kg=Decimal("-0.01")),
            dict(good, code="FX-5", revision=0),
            dict(good, code="fx-6"),
            dict(good),  # the same code in the same catalog
            dict(good, code="FX-7", volume_slot=Decimal("-0.01")),
            dict(good, code="FX-8", shipping_qty=0),
            dict(good, code="FX-9", volume_slot=Decimal("0.00")),
            dict(good, code="FX-D1", length_cm=Decimal("1.0")),  # all three sizes or none
            dict(good, code="FX-D2", length_cm=Decimal("1.0"), width_cm=Decimal("1.0"),
                 height_cm=Decimal("0.0")),
        )
        for values in refused:
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                CatalogProduct.objects.create(**values)
        CatalogProduct.objects.create(**dict(good, code="FX-S1", volume_slot=Decimal("0.01")))
        with self.assertRaises(IntegrityError), transaction.atomic():
            CatalogProduct.objects.filter(code="FX-S1").update(volume_slot=Decimal("0.00"))
        # The same code may exist in another catalog.
        other = Catalog.objects.create(template_key="other")
        CatalogProduct.objects.create(**dict(good, catalog=other))

    def test_the_revision_rules(self):
        product = make_product("FX-OK")
        row = dict(
            product=product, revision=2, code=product.code, name=product.name,
            weight_kg=product.weight_kg, srp_centavos=product.srp_centavos,
            earnings_rate=product.earnings_rate, is_active=True,
        )
        refused = (
            dict(row, source="owner"),
            dict(row, source="owner", changed_by_account_id=1),
            dict(row, source="owner", changed_by_site_id=1),
            dict(row, source="seed", seed_file_sha256="a" * 64),
            dict(row, source="seed", changed_by_operator_username="op"),
            dict(row, source="other", changed_by_account_id=1, changed_by_site_id=1),
        )
        for values in refused:
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                CatalogProductRevision.objects.create(**values)
        writer = dict(source="owner", changed_by_account_id=1, changed_by_site_id=1)
        CatalogProductRevision.objects.create(**row, **writer)
        with self.assertRaises(IntegrityError), transaction.atomic():  # one row per revision
            CatalogProductRevision.objects.create(**row, **writer)

    def test_products_are_never_deleted_and_revisions_are_append_only(self):
        product = make_product()
        with self.assertRaises(CatalogProduct.DeleteRefused):
            product.delete()
        revision = product.revisions.get()
        revision.name = "Changed"
        with self.assertRaises(CatalogProductRevision.ImmutableRevisionError):
            revision.save()
        with self.assertRaises(CatalogProductRevision.ImmutableRevisionError):
            revision.delete()
        with self.assertRaises(ProtectedError):
            CatalogProduct.objects.filter(pk=product.pk).delete()
        with self.assertRaises(ProtectedError):
            Catalog.objects.all().delete()
        self.assertEqual(product.revisions.get().name, "Fixture Alpha")

    def test_no_table_points_at_a_site_or_an_account(self):
        for model in (Catalog, CatalogProduct, CatalogProductRevision):
            for field in model._meta.get_fields():
                if field.is_relation and not field.auto_created:
                    with self.subTest(model=model.__name__, field=field.name):
                        self.assertEqual(field.related_model._meta.app_label, "catalog")
