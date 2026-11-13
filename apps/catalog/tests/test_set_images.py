"""The catalog_set_images command: images for products that already exist. No test reaches the
network; the media store is a dict (FakeMedia)."""
import hashlib
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.catalog import media
from apps.catalog.models import CatalogProduct, CatalogProductRevision
from apps.catalog.tests.base import (
    JPEG, PNG, WEBP, FakeMedia, catalog_rows, make_product, switched_on,
)


def run(folder, *args, **options):
    out = StringIO()
    call_command("catalog_set_images", "--images-dir", folder, *args, stdout=out, **options)
    return out.getvalue()


@switched_on
class SetImagesTests(TestCase):
    def setUp(self):
        self.op = get_user_model().objects.create_user(
            "image-op", password="pw", is_staff=True
        )
        self.alpha = make_product("FX-ALPHA")
        self.beta = make_product("FX-BETA")
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)

    def apply(self, *args, **options):
        return run(self.folder.name, "--apply", "--operator", "image-op", *args, **options)

    def refused(self, text, *args, **options):
        before = catalog_rows()
        with self.assertRaisesMessage(CommandError, text):
            self.apply(*args, **options)
        self.assertEqual(catalog_rows(), before)

    def test_a_dry_run_reports_each_file_and_writes_nothing(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        (self.path / "fx-beta.JPG").write_bytes(JPEG)
        (self.path / "FX-GONE.webp").write_bytes(WEBP)
        (self.path / "notes.txt").write_text("x")
        CatalogProduct.objects.filter(pk=self.beta.pk).update(image_key="catalog/products/old.png")
        before = catalog_rows()
        with FakeMedia() as store:
            text = run(self.folder.name)
            self.assertEqual(store.files, {})
        self.assertIn("FX-ALPHA: would set", text)
        self.assertIn("FX-BETA: skip (has image)", text)
        self.assertIn("FX-GONE: skip (no such product)", text)
        self.assertIn("Dry run, would set 1, skipped 2.", text)
        self.assertNotIn("notes", text)
        self.assertEqual(catalog_rows(), before)

    def test_a_dry_run_needs_neither_the_store_nor_an_operator(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        self.assertIn("FX-ALPHA: would set", run(self.folder.name))

    def test_apply_sets_the_images_and_writes_a_revision_each(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        (self.path / "FX-BETA.jpeg").write_bytes(JPEG)
        with FakeMedia() as store:
            text = self.apply()
            self.assertEqual(len(store.files), 2)
        self.assertIn("FX-ALPHA: set", text)
        self.assertIn("Set 2, skipped 0.", text)
        for product, content in ((self.alpha, PNG), (self.beta, JPEG)):
            product.refresh_from_db()
            self.assertTrue(product.image_key)
            self.assertEqual(product.revision, 2)
            revision = CatalogProductRevision.objects.get(product=product, revision=2)
            self.assertEqual(
                (revision.source, revision.changed_by_operator_user_id,
                 revision.changed_by_operator_username, revision.seed_file_sha256,
                 revision.image_key),
                ("seed", self.op.pk, "image-op", hashlib.sha256(content).hexdigest(),
                 product.image_key),
            )

    def test_output_never_holds_a_key_or_a_token(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        with FakeMedia():
            text = self.apply()
        self.assertNotIn("catalog/products", text)
        self.assertNotIn("test-token", text)

    def test_a_product_with_an_image_is_skipped_unless_replace(self):
        CatalogProduct.objects.filter(pk=self.alpha.pk).update(image_key="catalog/products/old.png")
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        with FakeMedia() as store:
            store.files["catalog/products/old.png"] = b"old"
            text = self.apply()
            self.assertIn("FX-ALPHA: skip (has image)", text)
            self.assertEqual(list(store.files), ["catalog/products/old.png"])
            self.alpha.refresh_from_db()
            self.assertEqual((self.alpha.image_key, self.alpha.revision), ("catalog/products/old.png", 1))
            with self.captureOnCommitCallbacks(execute=True):
                text = self.apply("--replace")
            self.assertIn("FX-ALPHA: set", text)
            self.alpha.refresh_from_db()
            self.assertNotEqual(self.alpha.image_key, "catalog/products/old.png")
            self.assertEqual(self.alpha.revision, 2)
            self.assertEqual(list(store.files), [self.alpha.image_key])  # the old file is removed

    def test_an_unknown_code_is_skipped_and_writes_nothing(self):
        (self.path / "FX-NOPE.png").write_bytes(PNG)
        before = catalog_rows()
        with FakeMedia() as store:
            text = self.apply()
            self.assertEqual(store.files, {})
        self.assertIn("FX-NOPE: skip (no such product)", text)
        self.assertEqual(catalog_rows(), before)

    def test_a_bad_file_refuses_the_whole_run_even_a_dry_one(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        (self.path / "FX-BETA.png").write_bytes(b"not an image at all")
        with FakeMedia() as store:
            with self.assertRaisesMessage(CommandError, "not a JPEG, PNG or WebP"):
                run(self.folder.name)
            self.refused("not a JPEG, PNG or WebP")
            self.assertEqual(store.files, {})

    def test_two_files_for_one_code_refuse_the_run(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        (self.path / "FX-ALPHA.jpg").write_bytes(JPEG)
        with FakeMedia():
            self.refused("More than one image file")

    def test_a_missing_folder_is_refused(self):
        with self.assertRaisesMessage(CommandError, "not a folder"):
            run(str(self.path / "nope"))

    def test_apply_refuses_when_the_store_is_not_configured(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        self.refused("not configured")

    def test_apply_refuses_without_an_active_staff_operator_or_with_the_catalog_off(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        before = catalog_rows()
        with FakeMedia():
            with self.assertRaisesMessage(CommandError, "needs --operator"):
                run(self.folder.name, "--apply")
            with self.assertRaisesMessage(CommandError, "not an active staff user"):
                run(self.folder.name, "--apply", "--operator", "nobody")
            get_user_model().objects.create_user("plain-user", password="pw")
            with self.assertRaisesMessage(CommandError, "not an active staff user"):
                run(self.folder.name, "--apply", "--operator", "plain-user")
            with self.settings(SANTE_CATALOG_ENABLED=False):
                self.refused("switched off")
            with self.settings(SANTE_SETTINGS_ENABLED=False):
                self.refused("switched off")
        self.assertEqual(catalog_rows(), before)

    def test_a_dry_run_with_the_catalog_off_says_so_and_checks_nothing(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        with self.settings(SANTE_CATALOG_ENABLED=False):
            text = run(self.folder.name)
        self.assertIn("Catalog switch is off", text)
        self.assertIn("FX-ALPHA: not checked", text)

    def test_a_failed_store_write_changes_nothing_and_removes_what_it_stored(self):
        (self.path / "FX-ALPHA.png").write_bytes(PNG)
        (self.path / "FX-BETA.png").write_bytes(PNG)
        before = catalog_rows()
        with FakeMedia() as store:
            first = store._save
            calls = []

            def flaky(content, content_type):
                calls.append(1)
                if len(calls) == 2:
                    raise media.StoreError("no")
                return first(content, content_type)

            with mock.patch("apps.catalog.media.save", side_effect=flaky):
                with self.assertRaisesMessage(CommandError, "nothing was written"):
                    self.apply()
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)
