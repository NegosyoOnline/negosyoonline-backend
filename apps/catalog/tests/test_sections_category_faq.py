"""CAT-1 r5c: one optional image per page section, and the general FAQs of each category."""
from django.core.cache import cache
from django.test import override_settings

from apps.accounts.tests.base import ORIGIN
from apps.billing.tests.base import make_subscription
from apps.catalog import services
from apps.catalog.models import Catalog, CatalogCategoryFaq, CatalogProduct
from apps.catalog.tests.base import (
    MEDIA, CatalogApiTestCase, FakeMedia, catalog_rows, image_file, make_product, switched_on,
)
from apps.storefront.tests.base import HOST, SurfaceTestCase

SECTIONS = ("features", "advantages", "benefits")
LIST = "/storefront/v1/catalog/products/"
FAQ = [{"q": "Is it safe?", "a": "Yes."}, {"q": "How often?", "a": "Daily."}]


class SectionImageTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()
        self.product = self.created()
        self.url = f"{self.products}{self.product['id']}/image/"
        cache.clear()

    def row(self):
        return CatalogProduct.objects.get()

    def upload(self, section, client=None, url=None, revision=None):
        data = {"file": image_file(), "revision": revision or self.row().revision}
        return (client or self.client).post(
            f"{url or self.url}?section={section}", data=data, secure=True, HTTP_ORIGIN=ORIGIN
        )

    def remove(self, section, client=None, url=None, revision=None):
        return (client or self.client).delete(
            f"{url or self.url}?revision={revision or self.row().revision}&section={section}",
            secure=True, HTTP_ORIGIN=ORIGIN,
        )

    def test_a_new_product_has_no_section_images(self):
        for section in SECTIONS:
            self.assertIsNone(self.product[f"{section}_image_url"])

    def test_each_section_stores_only_itself_and_writes_one_revision(self):
        with FakeMedia() as store:
            for number, section in enumerate(SECTIONS, start=1):
                response = self.upload(section)
                self.assertEqual(response.status_code, 200, response.content)
                body = response.json()
                self.assertEqual(body["revision"], number + 1)
                self.assertEqual(
                    [body[f"{s}_image_url"] is not None for s in SECTIONS],
                    [n <= number for n in range(1, 4)],
                )
                # a section image never touches a product photo slot
                self.assertEqual(body["image_urls"], [None] * 5)
                self.assertIsNone(body["image_url"])
            self.assertEqual(len(store.files), 3)
        row = self.row()
        revisions = list(row.revisions.order_by("revision"))
        self.assertEqual(len(revisions), 4)
        self.assertEqual(revisions[1].features_image_key, row.features_image_key)
        self.assertEqual(revisions[1].advantages_image_key, "")
        self.assertEqual(revisions[3].benefits_image_key, row.benefits_image_key)
        self.assertTrue(all(r.source == "owner" for r in revisions[1:]))
        with override_settings(**MEDIA):
            history = self.revisions(self.product["id"]).json()["revisions"]
        self.assertIsNotNone(history[0]["benefits_image_url"])
        self.assertIsNone(history[-1]["features_image_url"])

    def test_replacing_a_section_image_removes_only_its_old_file(self):
        with FakeMedia() as store:
            self.upload("features")
            self.upload("benefits")
            old, kept = self.row().features_image_key, self.row().benefits_image_key
            with self.captureOnCommitCallbacks(execute=True):
                self.upload("features")
            self.assertEqual(store.deleted, [old])
            self.assertNotEqual(self.row().features_image_key, old)
            self.assertEqual(self.row().benefits_image_key, kept)

    def test_clearing_one_section_leaves_the_others_and_the_photos(self):
        with FakeMedia() as store:
            for section in SECTIONS:
                self.upload(section)
            self.client.post(
                f"{self.url}?slot=2", data={"file": image_file(), "revision": self.row().revision},
                secure=True, HTTP_ORIGIN=ORIGIN,
            )
            before = self.row()
            keys = (before.features_image_key, before.advantages_image_key, before.image_key_2)
            with self.captureOnCommitCallbacks(execute=True):
                response = self.remove("advantages")
            self.assertEqual(response.status_code, 200, response.content)
            body = response.json()
            self.assertEqual(body["revision"], before.revision + 1)
            self.assertIsNone(body["advantages_image_url"])
            self.assertIsNotNone(body["features_image_url"])
            self.assertIsNotNone(body["benefits_image_url"])
            self.assertIsNotNone(body["image_urls"][1])
            self.assertEqual(store.deleted, [keys[1]])
        row = self.row()
        self.assertEqual(
            (row.features_image_key, row.advantages_image_key, row.image_key_2),
            (keys[0], "", keys[2]),
        )
        self.assertEqual(row.revisions.get(revision=row.revision).advantages_image_key, "")

    def test_clearing_an_empty_section_changes_nothing(self):
        before = catalog_rows()
        response = self.remove("benefits")
        self.assertEqual((response.status_code, response.json()["revision"]), (200, 1))
        self.assertEqual(catalog_rows(), before)

    def test_an_unknown_section_or_a_section_with_a_slot_is_a_field_error(self):
        before = catalog_rows()
        with FakeMedia() as store:
            self.assertIn("section", self.fields(self.upload("specifications")))
            self.assertIn("section", self.fields(self.remove("")))
            both = self.client.post(
                f"{self.url}?section=features&slot=2",
                data={"file": image_file(), "revision": 1}, secure=True, HTTP_ORIGIN=ORIGIN,
            )
            self.assertIn("section", self.fields(both))
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)

    def test_a_stale_revision_is_refused(self):
        with FakeMedia():
            self.error(self.upload("features", revision=9), 409, "stale_revision")
        self.error(self.remove("features", revision=9), 409, "stale_revision")

    def test_a_bad_file_stores_nothing(self):
        before = catalog_rows()
        with FakeMedia() as store:
            bad = self.client.post(
                f"{self.url}?section=features",
                data={"file": image_file(b"not an image", "text/plain", "x.txt"), "revision": 1},
                secure=True, HTTP_ORIGIN=ORIGIN,
            )
            self.assertIn("file", self.fields(bad))
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)

    def test_only_the_owner_site_may_change_a_section_image(self):
        url = f"/v1/sites/{self.ben_site.public_id}/catalog/products/{self.product['id']}/image/"
        before = catalog_rows()
        with FakeMedia() as store:
            for section in SECTIONS:
                self.error(self.upload(section, client=self.ben, url=url), 403, "catalog_read_only")
                self.error(self.remove(section, client=self.ben, url=url), 403, "catalog_read_only")
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)

    def test_another_accounts_site_is_the_same_404(self):
        url = f"/v1/sites/{self.ben_site.public_id}/catalog/products/{self.product['id']}/image/"
        before = catalog_rows()
        with FakeMedia() as store:  # Cora names Ben's site
            for section in SECTIONS:
                self.error(self.upload(section, client=self.cora, url=url), 404, "not_found")
                self.error(self.remove(section, client=self.cora, url=url), 404, "not_found")
            self.assertEqual(store.files, {})
        self.assertEqual(catalog_rows(), before)

    def test_the_owner_list_carries_the_urls(self):
        with FakeMedia():
            self.upload("features")
            listed = self.listed()["products"][0]
        self.assertIsNotNone(listed["features_image_url"])
        self.assertIsNone(listed["advantages_image_url"])


class CategoryFaqApiTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()

    def url(self, category="drinks", site=None):
        return f"/v1/sites/{(site or self.site).public_id}/catalog/categories/{category}/faq/"

    def put(self, faq, category="drinks", site=None, client=None):
        return self.send("put", self.url(category, site), {"faq": faq}, client=client)

    def test_an_unset_category_reads_as_an_empty_list_and_creates_nothing(self):
        before = catalog_rows()
        response = self.get(self.url("barley"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"category": "barley", "faq": []})
        self.assertEqual(catalog_rows(), before)
        self.assertFalse(CatalogCategoryFaq.objects.exists())

    def test_put_then_get_round_trips_and_categories_are_separate(self):
        response = self.put(FAQ, "drinks")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"category": "drinks", "faq": FAQ})
        self.assertEqual(self.get(self.url("drinks")).json()["faq"], FAQ)
        self.assertEqual(self.get(self.url("beauty")).json()["faq"], [])
        self.put([{"q": "Q?", "a": "A."}], "beauty")
        self.assertEqual(self.get(self.url("drinks")).json()["faq"], FAQ)
        self.assertEqual(CatalogCategoryFaq.objects.count(), 2)

    def test_put_replaces_and_strips_and_an_empty_list_clears(self):
        self.put(FAQ)
        response = self.put([{"q": "  New?  ", "a": " Yes. "}])
        self.assertEqual(response.json()["faq"], [{"q": "New?", "a": "Yes."}])
        self.assertEqual(self.put([]).json()["faq"], [])
        row = CatalogCategoryFaq.objects.get()
        self.assertEqual(row.faq, [])
        self.assertEqual(
            (row.changed_by_account_id, row.changed_by_site_id),
            (self.site.account_id, self.site.pk),
        )

    def test_the_same_validation_as_the_product_faq(self):
        bad = [
            [{"q": "", "a": "x"}], [{"q": "x", "a": ""}], [{"q": "q" * 151, "a": "x"}],
            [{"q": "x", "a": "a" * 1001}], [{"q": "x"}], [{"q": "x", "a": "y", "extra": 1}],
            ["text"], [{"q": 1, "a": "y"}], [{"q": "q", "a": "a"}] * 11,
        ]
        for faq in bad:
            with self.subTest(faq=str(faq)[:40]):
                self.assertEqual(
                    self.fields(self.put(faq)), {"faq": [services.FIELD_MESSAGES["faq"]]}
                )
        self.assertFalse(CatalogCategoryFaq.objects.exists())
        ok = self.put([{"q": "q" * 150, "a": "a" * 1000}] * 10)
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_an_unknown_category_is_a_404(self):
        self.error(self.get(self.url("snacks")), 404, "not_found")
        self.error(self.put(FAQ, "snacks"), 404, "not_found")
        self.assertFalse(CatalogCategoryFaq.objects.exists())

    def test_only_the_owner_site_reads_or_writes(self):
        self.put(FAQ)
        before = catalog_rows(), list(CatalogCategoryFaq.objects.values())
        self.error(self.get(self.url(site=self.ben_site), client=self.ben), 403, "catalog_read_only")
        self.error(self.put([], site=self.ben_site, client=self.ben), 403, "catalog_read_only")
        self.assertEqual((catalog_rows(), list(CatalogCategoryFaq.objects.values())), before)

    def test_another_accounts_site_is_the_same_404(self):
        self.put(FAQ)
        before = list(CatalogCategoryFaq.objects.values())
        # Cora names Ben's site, and Ben names the owner's (Ana's) site.
        self.error(self.get(self.url(site=self.ben_site), client=self.cora), 404, "not_found")
        self.error(self.put([], site=self.ben_site, client=self.cora), 404, "not_found")
        self.error(self.get(self.url(), client=self.ben), 404, "not_found")
        self.error(self.put([], client=self.ben), 404, "not_found")
        self.assertEqual(list(CatalogCategoryFaq.objects.values()), before)


@switched_on
class StorefrontSectionAndCategoryFaqTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.site = make_subscription().site

    def body(self, product, host=HOST):
        response = self.call(host, path=f"{LIST}{product.public_id}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def set_faq(self, category, faq):
        CatalogCategoryFaq.objects.update_or_create(
            catalog=Catalog.objects.get(template_key="sante"), category=category,
            defaults={"faq": faq},
        )

    def test_section_images_are_null_when_unset_and_never_a_product_photo(self):
        product = make_product()
        CatalogProduct.objects.filter(pk=product.pk).update(
            image_key="catalog/products/a.png", image_key_2="catalog/products/b.png"
        )
        with override_settings(**MEDIA):
            body = self.body(product)
            listed = self.call(path=LIST).json()["products"][0]
        for payload in (body, listed):
            for section in SECTIONS:
                self.assertIsNone(payload[f"{section}_image_url"])
            self.assertIsNotNone(payload["image_url"])

    def test_section_images_are_returned_by_name(self):
        product = make_product()
        CatalogProduct.objects.filter(pk=product.pk).update(
            features_image_key="catalog/products/f.png", benefits_image_key="catalog/products/b.png"
        )
        with override_settings(**MEDIA):
            body = self.body(product)
        self.assertEqual(
            body["features_image_url"], "https://media.example.test/catalog/products/f.png"
        )
        self.assertIsNone(body["advantages_image_url"])
        self.assertEqual(
            body["benefits_image_url"], "https://media.example.test/catalog/products/b.png"
        )
        self.assertIsNone(body["image_url"])

    def test_the_category_faq_is_a_separate_key_after_the_product_faq(self):
        product = make_product()
        CatalogProduct.objects.filter(pk=product.pk).update(
            category="drinks", faq=[{"q": "Own?", "a": "Yes."}]
        )
        self.set_faq("drinks", FAQ)
        self.set_faq("beauty", [{"q": "Other?", "a": "No."}])
        body = self.body(product)
        self.assertEqual(body["faq"], [{"q": "Own?", "a": "Yes."}])
        self.assertEqual(body["category_faq"], FAQ)
        listed = self.call(path=LIST).json()["products"][0]
        self.assertEqual((listed["faq"], listed["category_faq"]), (body["faq"], FAQ))

    def test_no_category_or_no_category_faq_gives_an_empty_list(self):
        plain = make_product("P-1", "Plain")
        drinks = make_product("P-2", "Drinks")
        CatalogProduct.objects.filter(pk=drinks.pk).update(category="drinks")
        self.set_faq("beauty", FAQ)
        self.assertEqual(self.body(plain)["category_faq"], [])
        self.assertEqual(self.body(drinks)["category_faq"], [])

    def test_every_site_reads_the_same_category_faq(self):
        product = make_product()
        CatalogProduct.objects.filter(pk=product.pk).update(category="barley")
        self.set_faq("barley", FAQ)
        make_subscription("ben@example.test", "bens-store")
        self.assertEqual(self.body(product, "bens-store.negosyotest.com")["category_faq"], FAQ)
