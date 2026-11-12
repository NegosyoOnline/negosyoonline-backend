"""The Santé seed: the file, the catalog_seed_sante command and seed_products. Run against the
test database only; no test reaches the network."""
import hashlib
import json
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.catalog import media, pricing, services
from apps.catalog.management.commands.catalog_seed_sante import DEFAULT_FILE
from apps.catalog.models import CatalogProduct, CatalogProductRevision
from apps.catalog.tests.base import (
    JPEG, PNG, FakeMedia, catalog_rows, make_product, operator, switched_on,
)
from apps.sante import services as sante

FILE = Path(DEFAULT_FILE)
SHA = hashlib.sha256(FILE.read_bytes()).hexdigest()
DOC = json.loads(FILE.read_text(encoding="utf-8"))
ROWS = DOC["products"]

# (code, name, category, SRP in pesos, rate, members price in pesos, weight): the approved list.
APPROVED = [
    ("SPHB01001", "Fusion Coffee", "drinks", 450, "0.4000", 270, "0.18"),
    ("SPHB01002", "Boost Coffee", "drinks", 500, "0.4000", 300, "0.18"),
    ("SPHB03001", "Fit N' Trim", "drinks", 770, "0.4000", 462, "0.09"),
    ("SPHK06001", "Santé Beauty Collagen + Barley", "beauty", 1400, "0.4000", 840, "0.09"),
    ("SPHN01001", "Santé Barley Powder (10 sachets) with Stevia", "barley", 740, "0.4000", 444, "0.05"),
    ("SPHN01003", "Santé Barley Powder (30 sachets) with Stevia", "barley", 2100, "0.4000", 1260, "0.16"),
    ("SPHN01005", "Santé Barley Canister (200g) with Stevia", "barley", 2800, "0.4000", 1680, "0.26"),
    ("SPHN01006", "Santé Barley Canister (200g) No Added Sweetener", "barley", 2800, "0.4000", 1680, "0.26"),
    ("SPHN02001", "Santé Barley Capsules", "barley", 1050, "0.4000", 630, "0.08"),
    ("SPHN03003", "Daily-C (500mg x 100 capsules)", "essentials", 825, "0.4000", 495, "0.15"),
    ("SPHO01001", "Santé FibrEnergy (10 Sachet)", "drinks", 500, "0.2000", 400, "0.25"),
    ("SPHO02001", "Barliccino Coffee (10 Sachet)", "drinks", 320, "0.2000", 256, "0.15"),
    ("SPHO03001", "Calcium-Cee (500mg x 100 capsules)", "essentials", 825, "0.4000", 495, "0.11"),
    ("SPHO04001", "Santé Barley Max NZ (100g) with Stevia", "barley", 1545, "0.4000", 927, "0.13"),
    ("SPHO04003", "Santé Barley Max NZ (100g) No Added Sweetener", "barley", 1545, "0.4000", 927, "0.13"),
    ("SPHP01001", "Moments Sanitary Napkin (10 pads)", "essentials", 250, "0.4000", 150, "0.08"),
    ("SPHP03001", "Natureal Toothpaste 100ml", "essentials", 450, "0.4000", 270, "0.15"),
    ("SPHPO1002", "Moments Anion Night Pads (5 pads)", "essentials", 200, "0.4000", 120, "0.06"),
]


def run(*args, **options):
    out = StringIO()
    call_command("catalog_seed_sante", *args, stdout=out, **options)
    return out.getvalue()


class SeedFileTests(TestCase):
    def test_the_file_is_the_approved_list_in_order(self):
        self.assertEqual(
            [(r["code"], r["name"], r["category"], r["srp_centavos"], r["earnings_rate"],
              r["weight_kg"]) for r in ROWS],
            [(c, n, cat, peso * 100, rate, w) for c, n, cat, peso, rate, _, w in APPROVED],
        )

    def test_the_members_price_column_is_what_pricing_works_out(self):
        for row, (code, *_rest, members, _weight) in zip(ROWS, APPROVED, strict=True):
            with self.subTest(code=code):
                self.assertEqual(
                    pricing.unit_display(row["srp_centavos"], Decimal(row["earnings_rate"])),
                    members * 100,
                )

    def test_every_row_passes_the_apis_own_validation(self):
        for row in ROWS:
            code, cleaned = services.seed_clean(row)
            self.assertEqual(code, row["code"])
            self.assertTrue(cleaned["weight_kg"] > 0)

    def test_it_holds_no_promo_pack_and_no_membership_package(self):
        text = FILE.read_text(encoding="utf-8").lower()
        self.assertEqual(len(ROWS), 18)
        self.assertEqual(len({r["code"] for r in ROWS}), 18)
        self.assertFalse([r for r in ROWS if r["code"].startswith("PRM")])
        self.assertNotIn("membership", text.split('"products"')[1])
        self.assertEqual(DOC["signoff"]["approved_on"], "2026-11-13")


@switched_on
class SeedCommandTests(TestCase):
    def setUp(self):
        self.op = operator("seed-op")

    def apply(self, **extra):
        options = {"apply": True, "operator": "seed-op", "expect_sha256": SHA, **extra}
        return run(**options)

    def refused(self, message, **extra):
        before = catalog_rows()
        with self.assertRaises(CommandError) as raised:
            self.apply(**extra)
        self.assertIn(message, str(raised.exception))
        self.assertEqual(catalog_rows(), before)

    def test_the_default_is_a_dry_run_that_writes_nothing_and_prints_the_hash(self):
        before = catalog_rows()
        text = run()
        self.assertIn(SHA, text)
        self.assertEqual(text.count(": create"), 18)
        self.assertIn("Dry run, would create 18, skipped 0.", text)
        self.assertEqual(catalog_rows(), before)

    def test_a_dry_run_works_with_the_switch_off_and_says_it_did_not_check(self):
        with self.settings(SANTE_CATALOG_ENABLED=False):
            text = run()
        self.assertIn("existing products were not checked", text)
        self.assertFalse(CatalogProduct.objects.exists())

    def test_apply_is_refused_without_each_piece(self):
        self.refused("needs --operator", operator="")
        self.refused("needs --expect-sha256", expect_sha256="")
        self.refused("does not match", expect_sha256="0" * 64)
        self.refused("not an active staff user", operator="nobody")
        get_user_model().objects.create_user("plain-user", password="pw")
        self.refused("not an active staff user", operator="plain-user")
        with self.settings(SANTE_CATALOG_ENABLED=False):
            self.refused("switched off")
        with self.settings(SANTE_SETTINGS_ENABLED=False):
            self.refused("switched off")

    def test_apply_creates_18_products_and_18_seed_revisions(self):
        text = self.apply()
        self.assertIn("Created 18, skipped 0.", text)
        products = list(CatalogProduct.objects.order_by("pk"))
        self.assertEqual([p.code for p in products], [r["code"] for r in ROWS])
        for product, (code, name, category, peso, rate, members, weight) in zip(
            products, APPROVED, strict=True
        ):
            self.assertEqual(
                (product.name, product.category, product.srp_centavos, product.earnings_rate,
                 product.weight_kg, product.is_active, product.revision, product.image_key),
                (name, category, peso * 100, Decimal(rate), Decimal(weight), True, 1, ""),
            )
        revisions = CatalogProductRevision.objects.all()
        self.assertEqual(revisions.count(), 18)
        for revision in revisions:
            self.assertEqual(
                (revision.source, revision.changed_by_operator_username,
                 revision.changed_by_operator_user_id, revision.seed_file_sha256,
                 revision.changed_by_account_id, revision.changed_by_site_id),
                ("seed", "seed-op", self.op.pk, SHA, None, None),
            )

    def test_the_second_run_changes_nothing(self):
        self.apply()
        before = catalog_rows()
        text = self.apply()
        self.assertIn("Created 0, skipped 18.", text)
        self.assertEqual(text.count("skip (same)"), 18)
        self.assertEqual(catalog_rows(), before)

    def test_an_owner_edited_product_is_reported_as_differs_and_left_alone(self):
        self.apply()
        CatalogProduct.objects.filter(code="SPHB01001").update(name="Renamed", srp_centavos=1)
        before = catalog_rows()
        self.assertIn("SPHB01001: skip (differs: name, srp_centavos)", run())
        text = self.apply()
        self.assertIn("SPHB01001: skip (differs: name, srp_centavos)", text)
        self.assertEqual(catalog_rows(), before)

    def test_a_bad_row_rolls_everything_back(self):
        rows = [*ROWS[:3], {**ROWS[3], "earnings_rate": "0.9000"}]
        before = catalog_rows()
        with self.assertRaises(Exception):
            services.seed_products(rows, operator=self.op, file_sha256=SHA)
        self.assertEqual(catalog_rows(), before)

    def test_a_row_with_an_unknown_field_is_refused(self):
        with self.assertRaises(Exception):
            services.seed_clean({**ROWS[0], "is_active": False})
        with self.assertRaises(Exception):
            services.seed_clean({**ROWS[0], "image_key": "x"})

    def test_a_file_that_is_not_a_product_list_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "bad.json")
            path.write_text('{"products": "no"}', encoding="utf-8")
            with self.assertRaises(CommandError):
                run(file=str(path))

    def test_a_changed_file_needs_its_own_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "copy.json")
            path.write_text(FILE.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.refused("does not match", file=str(path))

    def test_images_dir_stores_files_named_by_code_and_ignores_the_rest(self):
        with tempfile.TemporaryDirectory() as folder, FakeMedia() as store:
            Path(folder, "SPHB01001.png").write_bytes(PNG)
            Path(folder, "sphn01001.jpg").write_bytes(JPEG)  # a code in any letter case
            Path(folder, "NOT-A-CODE.png").write_bytes(PNG)
            Path(folder, "notes.txt").write_text("x")
            text = self.apply(images_dir=folder)
            self.assertEqual(text.count("+image"), 2)
            keyed = dict(CatalogProduct.objects.exclude(image_key="").values_list("code", "image_key"))
            self.assertEqual(set(keyed), {"SPHB01001", "SPHN01001"})
            self.assertEqual(set(store.files), set(keyed.values()))

    def test_images_dir_needs_the_media_store_and_a_real_image(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "SPHB01001.png").write_bytes(PNG)
            self.refused("not configured", images_dir=folder)
            Path(folder, "SPHB01001.png").write_bytes(b"not an image at all")
            with FakeMedia():
                self.refused("not a JPEG, PNG or WebP", images_dir=folder)

    def test_a_failed_image_store_writes_nothing_and_removes_what_it_stored(self):
        with tempfile.TemporaryDirectory() as folder, FakeMedia() as store:
            Path(folder, "SPHB01001.png").write_bytes(PNG)
            Path(folder, "SPHB01002.png").write_bytes(PNG)
            first = store._save
            calls = []

            def flaky(content, content_type):
                calls.append(1)
                if len(calls) == 2:
                    raise media.StoreError("no")
                return first(content, content_type)

            before = catalog_rows()
            with mock.patch("apps.catalog.media.save", side_effect=flaky):
                with self.assertRaises(CommandError):
                    self.apply(images_dir=folder)
            self.assertEqual(catalog_rows(), before)
            self.assertEqual(store.files, {})

    def test_existing_products_get_no_image_from_a_later_run(self):
        self.apply()
        with tempfile.TemporaryDirectory() as folder, FakeMedia() as store:
            Path(folder, "SPHB01001.png").write_bytes(PNG)
            self.apply(images_dir=folder)
            self.assertEqual(store.files, {})
        self.assertFalse(CatalogProduct.objects.exclude(image_key="").exists())

    def test_seeding_needs_no_catalog_owner(self):
        self.assertIsNone(sante.catalog_owner_site())
        self.apply()
        self.assertEqual(CatalogProduct.objects.count(), 18)

    def test_an_existing_fixture_product_is_never_overwritten(self):
        make_product("SPHB01001", "Fixture", 100, "0.2000")
        text = self.apply()
        self.assertIn("Created 17, skipped 1.", text)
        self.assertEqual(CatalogProduct.objects.get(code="SPHB01001").name, "Fixture")
