"""Simultaneous catalog writes. These need real row locks, so they run on PostgreSQL only
(CI's "PostgreSQL and Redis" job). Restates TWCinc's test_concurrency through the service
calls, the way this repository's other lock tests run."""
import threading
from unittest import mock, skipUnless

from django.core.cache import cache
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import Account
from apps.catalog import services
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import make_product, operator, switched_on
from apps.common.errors import ApiError
from apps.sante import services as sante
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site
from apps.sites.tests.test_concurrency import run_together

CREATE = {
    "code": "FX-SAME", "name": "Same", "srp_centavos": 100, "earnings_rate": "0.1000",
    "short_description": "", "long_description": "", "weight_kg": "0.00",
}


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@switched_on
class CatalogWriteRaceTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.op = operator("race-op")
        self.account = make_account()
        self.site = make_site(self.account, "race-one", "Race One")
        self.other = make_site(self.account, "race-two", "Race Two", first=False)
        sante.set_catalog_owner(self.site, operator=self.op)
        self.product = make_product("FX-RACE")

    def update(self, name, revision=1, site=None):
        """An update as the API makes it: the site was read before the write began."""
        site_pk = (site or self.site).pk

        def call():
            return services.update_product(
                Account.objects.get(pk=self.account.pk), Site.objects.get(pk=site_pk),
                self.product.public_id, {"revision": revision, "name": name},
            )
        return call

    def create(self):
        return services.create_product(
            Account.objects.get(pk=self.account.pk), Site.objects.get(pk=self.site.pk),
            dict(CREATE),
        )

    def in_thread(self, call, results, key):
        """Start `call` on its own connection; what it returned or raised lands in results."""
        def worker():
            try:
                results[key] = call()
            except Exception as exc:  # the test inspects what the call raised
                results[key] = exc
            finally:
                connection.close()

        thread = threading.Thread(target=worker)
        thread.start()
        return thread

    def refusals(self, results):
        return [(r.status, r.code) for r in results if isinstance(r, ApiError)]

    def test_two_owner_updates_of_one_revision_write_one(self):
        results = run_together(self.update("One"), self.update("Two"))
        self.assertEqual(self.refusals(results), [(409, "stale_revision")], results)
        self.assertEqual(len([r for r in results if isinstance(r, CatalogProduct)]), 1, results)
        self.product.refresh_from_db()
        self.assertEqual((self.product.revision, self.product.revisions.count()), (2, 2))
        self.assertIn(self.product.name, ("One", "Two"))

    def test_two_owner_creates_of_one_code_make_one_product(self):
        results = run_together(self.create, self.create)
        self.assertEqual(self.refusals(results), [(409, "code_taken")], results)
        product = CatalogProduct.objects.get(code="FX-SAME")
        self.assertEqual(product.revisions.count(), 1)

    def test_a_write_waits_for_a_change_of_owner_and_is_then_refused(self):
        results = {}
        with transaction.atomic():
            sante.set_catalog_owner(self.other, operator=self.op)  # holds the owner row
            thread = self.in_thread(self.update("Changed"), results, "write")
            thread.join(1.0)
            self.assertTrue(thread.is_alive(), "the write must wait on the catalog owner row")
        thread.join(20)
        self.assertEqual(self.refusals([results["write"]]), [(403, "catalog_read_only")], results)
        self.assertEqual(self.product.revisions.count(), 1)

    def test_a_write_waits_for_its_site_being_removed_and_then_answers_404(self):
        results = {}
        with transaction.atomic():
            row = Site.objects.select_for_update().get(pk=self.site.pk)
            row.deleted_at = timezone.now()
            row.save(update_fields=["deleted_at"])
            thread = self.in_thread(self.update("Changed"), results, "write")
            thread.join(1.0)
            self.assertTrue(thread.is_alive(), "the write must wait on its site row")
        thread.join(20)
        self.assertEqual(self.refusals([results["write"]]), [(404, "not_found")], results)
        self.assertEqual(self.product.revisions.count(), 1)

    def test_naming_the_writing_site_the_owner_never_deadlocks(self):
        """The owner row is locked before the Site row on both paths, so in either order the
        result is a committed write by the new owner or a clean refusal, never a deadlock."""
        results = {}

        # Order 1: the change of owner is held first; the write queues on the owner row, then
        # commits as the new owner.
        with transaction.atomic():
            sante.set_catalog_owner(self.other, operator=self.op)
            thread = self.in_thread(self.update("first", 1, self.other), results, "first")
            thread.join(1.0)
            self.assertTrue(thread.is_alive(), "the write must queue on the catalog owner row")
        thread.join(20)
        self.assertIsInstance(results["first"], CatalogProduct, results)
        self.assertEqual(self.product.revisions.count(), 2)

        # Order 2: the write is in flight first, paused just before its owner check (after
        # its unlocked site read); the change of owner runs while it is paused.
        sante.set_catalog_owner(self.site, operator=self.op)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real = sante.is_catalog_writer

        def paused(site, *, lock=False):
            if lock:
                entered.set()
                release.wait(10)
            return real(site, lock=lock)

        def reassign():
            return sante.set_catalog_owner(Site.objects.get(pk=self.other.pk), operator=self.op)

        with mock.patch.object(sante, "is_catalog_writer", paused):
            write = self.in_thread(self.update("second", 2, self.other), results, "second")
            self.assertTrue(entered.wait(5), "the write must reach its owner check")
            change = self.in_thread(reassign, results, "reassign")
            change.join(5)
            self.assertFalse(
                change.is_alive(), "naming the owner must not wait on a write holding no lock yet"
            )
            release.set()
            write.join(20)
        self.assertNotIsInstance(results["reassign"], Exception, results)
        self.assertIsInstance(results["second"], CatalogProduct, results)  # the owner when checked
        self.assertEqual(self.product.revisions.count(), 3)
