"""The Santé lists in the hq admin: the catalog owner is the one operator action, and both
lists are closed while the switch is off. Restates the admin half of TWCinc's
test_catalog_owner."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.base import HqAdminMixin, on_hq
from apps.sante import services
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sante.tests.base import operator, retemplate, switched_on
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site

OWNER = "admin:sante_santecatalogowner"
SETTINGS = "admin:sante_santesitesettings"


@on_hq
@switched_on
class SanteAdminTestCase(HqAdminMixin, TestCase):
    def setUp(self):
        self.op = operator()
        self.open_hq_session(self.op)
        self.a = make_site(make_account(), "site-a", "Site A")
        self.b = make_site(make_account("ben@example.test"), "site-b", "Site B")


class CatalogOwnerAdminTests(SanteAdminTestCase):
    def add(self, site):
        return self.hq_post(self.admin_url(f"{OWNER}_add"), {"site": site.pk if site else ""})

    def change(self, site):
        url = self.admin_url(f"{OWNER}_change", 1)
        return self.hq_post(url, {"site": site.pk if site else ""})

    def test_the_operator_sets_the_owner_and_it_is_audited(self):
        self.assertEqual(self.add(self.a).status_code, 302)
        self.assertEqual(services.catalog_owner_site(), self.a)
        entry = OperatorAuditEntry.objects.get(action="sante_catalog_owner_set")
        self.assertEqual(entry.operator, self.op)
        self.assertEqual(entry.detail, {"old_site": None, "new_site": self.a.public_id})

    def test_the_operator_swaps_and_clears_the_owner_on_the_one_row(self):
        self.add(self.a)
        self.assertEqual(self.change(self.b).status_code, 302)
        self.assertEqual(services.catalog_owner_site(), self.b)
        self.assertEqual(self.change(None).status_code, 302)
        self.assertIsNone(services.catalog_owner_site())
        self.assertEqual(SanteCatalogOwner.objects.count(), 1)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="sante_catalog_owner_set").count(), 3)

    def test_a_second_row_cannot_be_added(self):
        self.add(self.a)
        self.assertEqual(self.hq_get(self.admin_url(f"{OWNER}_add")).status_code, 403)
        self.assertEqual(self.add(self.b).status_code, 403)
        self.assertEqual(services.catalog_owner_site(), self.a)

    def test_only_live_sante_sites_are_offered_or_accepted(self):
        removed = make_site(make_account("cora@example.test"), "site-c", "Site C")
        Site.objects.filter(pk=removed.pk).update(deleted_at=timezone.now())
        other = retemplate(make_site(make_account("dan@example.test"), "site-d", "Site D"))
        page = self.hq_get(self.admin_url(f"{OWNER}_add"))
        self.assertContains(page, f"Site A ({self.a.public_id})")
        self.assertContains(page, "Site B")
        self.assertNotContains(page, "Site C")
        self.assertNotContains(page, "Site D")
        for site in (removed, other):
            with self.subTest(site=site.display_name):
                self.assertEqual(self.add(site).status_code, 200)  # the form again, with an error
        self.assertFalse(SanteCatalogOwner.objects.exists())
        self.assertFalse(OperatorAuditEntry.objects.filter(action="sante_catalog_owner_set").exists())

    def test_the_row_cannot_be_deleted(self):
        self.add(self.a)
        response = self.hq_post(self.admin_url(f"{OWNER}_delete", 1), {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(services.catalog_owner_site(), self.a)

    def test_a_staff_user_without_the_permission_cannot_reach_it(self):
        staff = get_user_model().objects.create_user("staff", password="pw", is_staff=True)
        self.client.cookies.clear()
        self.open_hq_session(staff)
        self.assertEqual(self.hq_get(self.admin_url(f"{OWNER}_changelist")).status_code, 403)
        self.assertEqual(self.add(self.a).status_code, 403)
        self.assertFalse(SanteCatalogOwner.objects.exists())

    def test_without_an_hq_session_nothing_is_set(self):
        self.client.cookies.clear()
        self.assertNotEqual(self.add(self.a).status_code, 200)
        self.assertFalse(SanteCatalogOwner.objects.exists())


class SiteSettingsAdminTests(SanteAdminTestCase):
    def test_the_list_opens_and_nothing_can_be_added_changed_or_deleted(self):
        row = SanteSiteSettings.objects.create(site=self.a, sponsor_id="SB-1")
        self.assertContains(self.hq_get(self.admin_url(f"{SETTINGS}_changelist")), "SB-1")
        self.assertEqual(self.hq_get(self.admin_url(f"{SETTINGS}_add")).status_code, 403)
        change = self.hq_post(self.admin_url(f"{SETTINGS}_change", row.pk), {"sponsor_id": "X"})
        self.assertEqual(change.status_code, 403)
        delete = self.hq_post(self.admin_url(f"{SETTINGS}_delete", row.pk), {"post": "yes"})
        self.assertEqual(delete.status_code, 403)
        row.refresh_from_db()
        self.assertEqual(row.sponsor_id, "SB-1")


@override_settings(SANTE_SETTINGS_ENABLED=False)
class SwitchedOffAdminTests(SanteAdminTestCase):
    def test_both_lists_are_closed_and_read_no_sante_table(self):
        index = self.hq_get(self.admin_url("admin:index"))
        self.assertEqual(index.status_code, 200)
        self.assertNotContains(index, "Santé catalog owner")
        self.assertNotContains(index, "Santé site settings")
        for name in (f"{OWNER}_changelist", f"{OWNER}_add", f"{SETTINGS}_changelist"):
            with self.subTest(name):
                self.assertEqual(self.hq_get(self.admin_url(name)).status_code, 403)
        self.assertEqual(self.hq_post(self.admin_url(f"{OWNER}_add"), {"site": self.a.pk}).status_code, 403)
        self.assertFalse(SanteCatalogOwner.objects.exists())
