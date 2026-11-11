"""The catalog in the hq admin: two read-only lists, closed while the catalog is off.
Restates TWCinc's test_admin."""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.catalog.models import Catalog, CatalogProduct, CatalogProductRevision
from apps.catalog.tests.base import catalog_rows, make_product, operator, switched_on
from apps.operators.tests.base import HqAdminMixin, on_hq

PRODUCTS = "admin:catalog_catalogproduct"
REVISIONS = "admin:catalog_catalogproductrevision"


@on_hq
@switched_on
class CatalogAdminTestCase(HqAdminMixin, TestCase):
    def setUp(self):
        self.open_hq_session(operator())
        self.product = make_product()
        self.revision = self.product.revisions.get()


class CatalogAdminTests(CatalogAdminTestCase):
    def test_the_operator_reads_both_lists(self):
        index = self.hq_get(self.admin_url("admin:index"))
        self.assertContains(index, "Catalog products")
        self.assertContains(index, "Catalog product revisions")
        self.assertContains(self.hq_get(self.admin_url(f"{PRODUCTS}_changelist")), "FX-ALPHA")
        self.assertContains(self.hq_get(self.admin_url(f"{REVISIONS}_changelist")), "fixture")

    def test_nothing_can_be_added_changed_or_deleted(self):
        before = catalog_rows()
        for name, row in ((PRODUCTS, self.product), (REVISIONS, self.revision)):
            with self.subTest(name):
                page = self.hq_get(self.admin_url(f"{name}_changelist"))
                self.assertFalse(page.context["has_add_permission"])
                self.assertEqual(self.hq_get(self.admin_url(f"{name}_add")).status_code, 403)
                self.assertEqual(self.hq_post(self.admin_url(f"{name}_add"), {}).status_code, 403)
                view = self.hq_get(self.admin_url(f"{name}_change", row.pk))
                self.assertEqual(view.status_code, 200)  # the read-only page
                self.assertFalse(view.context["has_change_permission"])
                self.assertFalse(view.context["has_delete_permission"])
                change = self.hq_post(self.admin_url(f"{name}_change", row.pk), {"name": "X"})
                self.assertEqual(change.status_code, 403)
                delete = self.hq_post(self.admin_url(f"{name}_delete", row.pk), {"post": "yes"})
                self.assertEqual(delete.status_code, 403)
        self.assertEqual(catalog_rows(), before)

    def test_the_catalog_row_itself_has_no_list(self):
        self.assertFalse(admin.site.is_registered(Catalog))
        self.assertTrue(admin.site.is_registered(CatalogProduct))
        self.assertTrue(admin.site.is_registered(CatalogProductRevision))

    def test_a_staff_user_without_the_permission_cannot_reach_them(self):
        staff = get_user_model().objects.create_user("staff", password="pw", is_staff=True)
        self.client.cookies.clear()
        self.open_hq_session(staff)
        for name in (PRODUCTS, REVISIONS):
            with self.subTest(name):
                self.assertEqual(
                    self.hq_get(self.admin_url(f"{name}_changelist")).status_code, 403
                )

    def test_without_an_hq_session_nothing_is_shown(self):
        self.client.cookies.clear()
        response = self.hq_get(self.admin_url(f"{PRODUCTS}_changelist"))
        self.assertNotEqual(response.status_code, 200)


class SwitchedOffAdminTests(CatalogAdminTestCase):
    def test_both_lists_are_closed_while_either_switch_is_off(self):
        for off in ({"SANTE_CATALOG_ENABLED": False}, {"SANTE_SETTINGS_ENABLED": False}):
            with self.subTest(off=off), override_settings(**off):
                index = self.hq_get(self.admin_url("admin:index"))
                self.assertEqual(index.status_code, 200)
                self.assertNotContains(index, "Catalog products")
                self.assertNotContains(index, "Catalog product revisions")
                for name, row in ((PRODUCTS, self.product), (REVISIONS, self.revision)):
                    for url in (
                        self.admin_url(f"{name}_changelist"),
                        self.admin_url(f"{name}_change", row.pk),
                        self.admin_url(f"{name}_add"),
                    ):
                        self.assertEqual(self.hq_get(url).status_code, 403, url)
