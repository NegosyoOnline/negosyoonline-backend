"""Category and product image: validation, revisions and the owner image routes. No test
reaches the network: the media store is FakeMedia."""
from unittest import mock

from django.core.cache import cache

from apps.accounts.tests.base import ORIGIN
from apps.catalog import media
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import (
    JPEG, PNG, WEBP, CatalogApiTestCase, FakeMedia, catalog_rows, image_file, make_product,
    product_url,
)


class CategoryTests(CatalogApiTestCase):
    def test_the_four_categories_and_blank_are_accepted(self):
        self.designate()
        for number, category in enumerate(("barley", "drinks", "essentials", "beauty", "")):
            body = self.created(code=f"FX-C{number}", category=category)
            self.assertEqual(body["category"], category)

    def test_an_unknown_category_is_refused_on_create_and_update(self):
        self.designate()
        self.assertIn("category", self.fields(self.create(category="gadgets")))
        product = self.created()
        response = self.update(product["id"], {"revision": 1, "category": "Barley"})
        self.assertIn("category", self.fields(response))
        self.assertEqual(CatalogProduct.objects.get().category, "")

    def test_a_category_change_writes_a_revision_and_the_same_value_none(self):
        self.designate()
        product = self.created()
        changed = self.update(product["id"], {"revision": 1, "category": "drinks"}).json()
        self.assertEqual((changed["category"], changed["revision"]), ("drinks", 2))
        same = self.update(product["id"], {"revision": 2, "category": "drinks"}).json()
        self.assertEqual(same["revision"], 2)
        rows = self.revisions(product["id"]).json()["revisions"]
        self.assertEqual([(r["revision"], r["category"]) for r in rows], [(2, "drinks"), (1, "")])

    def test_existing_rows_read_with_blank_category_and_no_image(self):
        make_product()
        self.designate()
        row = self.listed()["products"][0]
        self.assertEqual((row["category"], row["image_url"]), ("", None))


class ImageTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()
        self.product = self.created()
        self.url = f"{self.products}{self.product['id']}/image/"
        cache.clear()

    def upload(self, file=None, revision=None, client=None, url=None):
        data = {"file": file or image_file(), "revision": revision or self.current()}
        return (client or self.client).post(url or self.url, data=data, secure=True, HTTP_ORIGIN=ORIGIN)

    def current(self):
        return CatalogProduct.objects.get().revision

    def remove(self, revision=None, client=None, url=None):
        return (client or self.client).delete(
            f"{url or self.url}?revision={revision or self.current()}", secure=True,
            HTTP_ORIGIN=ORIGIN,
        )

    def test_upload_stores_the_file_sets_the_url_and_writes_a_revision(self):
        with FakeMedia() as store:
            response = self.upload()
            self.assertEqual(response.status_code, 200, response.content)
            body = response.json()
            row = CatalogProduct.objects.get()
            self.assertEqual(body["revision"], 2)
            self.assertEqual(body["image_url"], f"https://media.example.test/{row.image_key}")
            self.assertEqual(list(store.files), [row.image_key])
        self.assertEqual(row.revisions.count(), 2)
        self.assertEqual(row.revisions.get(revision=2).image_key, row.image_key)
        self.assertEqual(row.revisions.get(revision=2).source, "owner")

    def test_replace_removes_the_old_file_after_commit(self):
        with FakeMedia() as store:
            self.upload()
            first = CatalogProduct.objects.get().image_key
            with self.captureOnCommitCallbacks(execute=True):
                self.upload(image_file(JPEG, "image/jpeg", "p.jpg"))
            second = CatalogProduct.objects.get().image_key
            self.assertNotEqual(first, second)
            self.assertEqual(store.deleted, [first])
            self.assertEqual(list(store.files), [second])
            self.assertEqual(CatalogProduct.objects.get().revision, 3)

    def test_webp_is_accepted(self):
        with FakeMedia():
            response = self.upload(image_file(WEBP, "image/webp", "p.webp"))
        self.assertEqual(response.status_code, 200, response.content)

    def test_delete_blanks_the_key_writes_a_revision_and_removes_the_file(self):
        with FakeMedia() as store:
            self.upload()
            key = CatalogProduct.objects.get().image_key
            with self.captureOnCommitCallbacks(execute=True):
                response = self.remove()
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["image_url"], None)
            self.assertEqual(response.json()["revision"], 3)
            self.assertEqual(store.deleted, [key])
        self.assertEqual(CatalogProduct.objects.get().revisions.count(), 3)

    def test_delete_with_no_image_changes_nothing(self):
        before = catalog_rows()
        response = self.remove()
        self.assertEqual((response.status_code, response.json()["revision"]), (200, 1))
        self.assertEqual(catalog_rows(), before)

    def test_a_stale_revision_is_refused_and_stores_no_lasting_file(self):
        with FakeMedia() as store:
            response = self.upload(revision=5)
            self.error(response, 409, "stale_revision")
            self.assertEqual(store.files, {})
            self.assertEqual(len(store.deleted), 1)
        self.error(self.remove(revision=5), 409, "stale_revision")

    def test_missing_or_bad_revision_is_refused(self):
        with FakeMedia():
            for revision in ("", "x", "0", "-1"):
                response = self.client.post(
                    self.url, data={"file": image_file(), "revision": revision}, secure=True,
                    HTTP_ORIGIN=ORIGIN,
                )
                self.assertIn("revision", self.fields(response))

    def test_wrong_type_and_oversize_are_refused_with_the_field_message(self):
        before = catalog_rows()
        with FakeMedia() as store:
            text = image_file(b"hello world, not an image", "image/png", "p.png")
            self.assertIn("file", self.fields(self.upload(text)))
            declared = image_file(PNG, "text/plain", "p.png")
            self.assertIn("file", self.fields(self.upload(declared)))
            big = image_file(PNG + b"0" * media.MAX_BYTES, "image/png", "p.png")
            self.assertIn("file", self.fields(self.upload(big)))
            nothing = self.client.post(
                self.url, data={"revision": 1}, secure=True, HTTP_ORIGIN=ORIGIN
            )
            self.assertIn("file", self.fields(nothing))
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)

    def test_not_configured_is_a_clear_error_and_writes_nothing(self):
        before = catalog_rows()
        response = self.upload()  # no token and no media host: the settings are unset
        body = self.error(response, 503, "image_store_not_configured")
        self.assertIn("not configured", body["message"])
        self.assertEqual(catalog_rows(), before)

    def test_only_the_owner_may_change_an_image(self):
        make_ben = self.ben_site.public_id
        url = f"{product_url(make_ben, self.product['id'])}image/"
        before = catalog_rows()
        with FakeMedia() as store:
            self.error(self.upload(client=self.ben, url=url), 403, "catalog_read_only")
            self.error(self.remove(client=self.ben, url=url), 403, "catalog_read_only")
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)

    def test_another_accounts_site_is_the_same_404(self):
        url = f"{product_url(self.ben_site.public_id, self.product['id'])}image/"
        with FakeMedia():
            self.error(self.upload(url=url), 404, "not_found")
        self.error(self.remove(url=url), 404, "not_found")

    def test_an_unknown_product_is_404(self):
        url = f"{self.products}prod_00000000000000000000/image/"
        with FakeMedia():
            self.error(self.upload(url=url, revision=1), 404, "not_found")

    def test_a_failed_change_removes_the_file_it_stored(self):
        with FakeMedia() as store:
            self.upload(url=f"{self.products}prod_00000000000000000000/image/", revision=1)
            self.assertEqual(store.files, {})
            self.assertEqual(len(store.deleted), 1)

    def test_a_store_failure_answers_502_and_writes_nothing(self):
        before = catalog_rows()
        with FakeMedia(), mock.patch("apps.catalog.media.save", side_effect=media.StoreError("x")):
            self.error(self.upload(), 502, "image_not_saved")
        self.assertEqual(catalog_rows(), before)

    def test_the_writes_are_throttled(self):
        with FakeMedia():
            statuses = [self.upload().status_code for _ in range(125)]
        self.assertEqual(statuses[0], 200)
        self.assertEqual(statuses[-1], 429)
