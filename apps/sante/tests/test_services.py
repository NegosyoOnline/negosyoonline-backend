"""The Santé rules below the API. Restates the hook half of TWCinc's test_template_behavior
and the service half of test_catalog_owner."""
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.common.errors import ApiError
from apps.operators.models import OperatorAuditEntry
from apps.sante import services
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sante.tests.base import operator, retemplate, switched_on
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site


@switched_on
class SanteServiceTestCase(TestCase):
    def setUp(self):
        self.account = make_account()
        self.a = make_site(self.account, "site-a", "A")
        self.b = make_site(make_account("ben@example.test"), "site-b", "B")
        self.op = operator()


class SponsorRuleTests(SanteServiceTestCase):
    def test_the_format(self):
        self.assertEqual(services.clean_settings({"sponsor_id": " AB-12 "}), ({"sponsor_id": "AB-12"}, {}))
        self.assertEqual(services.clean_settings({"sponsor_id": ""}), ({"sponsor_id": ""}, {}))
        self.assertEqual(services.clean_settings({"sponsor_id": None}), ({"sponsor_id": ""}, {}))
        self.assertEqual(services.clean_settings({}), ({}, {}))
        for bad in ("bad id!", "x" * 33):
            with self.subTest(bad):
                cleaned, errors = services.clean_settings({"sponsor_id": bad})
                self.assertEqual((cleaned, errors), ({}, {"sponsor_id": services.SPONSOR_MSG}))

    def test_the_settings_fields_are_the_sponsor_id_only(self):
        self.assertEqual(services.SETTINGS_FIELDS, ("sponsor_id",))

    def test_a_new_site_reads_blank_and_has_no_row(self):
        self.assertEqual(
            services.settings_out(self.a),
            {"sponsor_id": "", "registration_ready": False, "catalog_owner": False},
        )
        self.assertFalse(SanteSiteSettings.objects.exists())
        self.assertFalse(services.registration_ready(self.a))

    def test_update_writes_one_row_per_site(self):
        services.update_settings(self.account, self.a, {"sponsor_id": "A-1"})
        services.update_settings(self.account, self.a, {"sponsor_id": "A-2"})
        self.assertEqual(list(SanteSiteSettings.objects.values_list("site_id", "sponsor_id")), [(self.a.pk, "A-2")])
        self.assertTrue(services.registration_ready(self.a))
        self.assertFalse(services.registration_ready(self.b))

    def test_update_refuses_a_site_the_account_cannot_reach(self):
        removed = make_site(make_account("cora@example.test"), "site-c", "C")
        Site.objects.filter(pk=removed.pk).update(deleted_at=timezone.now())
        other = retemplate(make_site(make_account("dan@example.test"), "site-d", "D"))
        for account, site in ((self.account, self.b), (removed.account, removed), (other.account, other)):
            with self.subTest(site=site.display_name), self.assertRaises(ApiError) as refused:
                services.update_settings(account, site, {"sponsor_id": "X-1"})
            self.assertEqual(refused.exception.status, 404)
        self.assertFalse(SanteSiteSettings.objects.exists())


class CatalogOwnerTests(SanteServiceTestCase):
    def entries(self):
        return list(OperatorAuditEntry.objects.filter(action="sante_catalog_owner_set").order_by("pk"))

    def test_set_and_swap_keep_one_owner_and_are_audited(self):
        services.set_catalog_owner(self.a, operator=self.op)
        services.set_catalog_owner(self.b, operator=self.op)
        self.assertEqual(SanteCatalogOwner.objects.count(), 1)
        self.assertEqual(services.catalog_owner_site(), self.b)
        first, second = self.entries()
        self.assertEqual(first.detail, {"old_site": None, "new_site": self.a.public_id})
        self.assertEqual(second.detail, {"old_site": self.a.public_id, "new_site": self.b.public_id})
        self.assertEqual(
            (second.operator, second.target_type, second.target_id, second.personal),
            (self.op, "sante_catalog_owner", "1", {}),
        )

    def test_it_can_be_cleared(self):
        services.set_catalog_owner(self.a, operator=self.op)
        services.set_catalog_owner(None, operator=self.op)
        self.assertIsNone(services.catalog_owner_site())
        self.assertEqual(SanteCatalogOwner.objects.count(), 1)
        self.assertEqual(self.entries()[-1].detail, {"old_site": self.a.public_id, "new_site": None})

    def test_no_owner_before_one_is_named(self):
        self.assertIsNone(services.catalog_owner_site())

    def test_the_target_must_be_a_live_sante_site(self):
        Site.objects.filter(pk=self.a.pk).update(deleted_at=timezone.now())
        retemplate(self.b)
        for site in (self.a, self.b):
            with self.subTest(site=site.display_name), self.assertRaises(ValueError):
                services.set_catalog_owner(site, operator=self.op)
        self.assertIsNone(services.catalog_owner_site())
        self.assertEqual(self.entries(), [])

    def test_a_stale_copy_of_a_removed_site_is_refused(self):
        Site.objects.filter(pk=self.a.pk).update(deleted_at=timezone.now())
        with self.assertRaises(ValueError):  # self.a still says it is live
            services.set_catalog_owner(self.a, operator=self.op)

    def test_is_catalog_writer_only_for_the_live_designated_sante_site(self):
        self.assertFalse(services.is_catalog_writer(self.a))  # no owner designated
        services.set_catalog_owner(self.a, operator=self.op)
        self.assertTrue(services.is_catalog_writer(self.a))
        self.assertFalse(services.is_catalog_writer(self.b))
        with transaction.atomic():
            self.assertTrue(services.is_catalog_writer(self.a, lock=True))
        self.assertTrue(services.settings_out(self.a)["catalog_owner"])

        Site.objects.filter(pk=self.a.pk).update(deleted_at=timezone.now())
        self.a.refresh_from_db()
        self.assertFalse(services.is_catalog_writer(self.a))  # fails closed on a removed owner
        self.assertFalse(services.settings_out(self.a)["catalog_owner"])

        services.set_catalog_owner(self.b, operator=self.op)
        retemplate(self.b)
        self.assertFalse(services.is_catalog_writer(self.b))  # and on a retemplated owner

    def test_is_catalog_writer_reads_only_the_owner_row(self):
        services.set_catalog_owner(self.a, operator=self.op)
        with self.assertNumQueries(1):
            services.is_catalog_writer(self.b)
