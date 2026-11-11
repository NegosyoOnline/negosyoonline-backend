"""The order-time snapshot. Restates TWCinc's test_snapshot for centavos and public ids."""
import dataclasses
from decimal import Decimal as D
from types import SimpleNamespace

from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.catalog import snapshot
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import MISSING_PRODUCT, make_product, retemplate, switched_on
from apps.sites.tests.base import make_account, make_site


@switched_on
class SnapshotGuardTests(SimpleTestCase):
    def test_it_refuses_to_run_outside_a_transaction(self):
        with self.assertRaises(RuntimeError):
            snapshot.take(SimpleNamespace(template="sante", pk=1), MISSING_PRODUCT, 1)


@switched_on
class SnapshotTests(TestCase):
    def setUp(self):
        self.site = make_site(make_account())
        self.product = make_product("FX-ALPHA", srp_centavos=9999, rate="0.3500")

    def test_it_locks_the_product_row_and_copies_its_values(self):
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            taken = snapshot.take(self.site, self.product.public_id, 3)
        self.assertEqual(taken, snapshot.LineSnapshot(
            product_id=self.product.public_id, code="FX-ALPHA", name="Fixture Alpha",
            revision=1, srp_centavos=9999, earnings_rate=D("0.3500"), quantity=3,
            line_srp_total_centavos=29997, line_earnings_centavos=10499,
            line_members_total_centavos=19498,
        ))
        if connection.features.has_select_for_update:  # true on PostgreSQL
            statements = [query["sql"] for query in queries.captured_queries]
            self.assertTrue(
                any('FOR UPDATE OF "catalog_catalogproduct"' in sql for sql in statements),
                statements,
            )

    def test_not_found_is_told_from_unavailable(self):
        inactive = make_product("FX-OFF", active=False)
        foreign = make_product("FX-FOREIGN", key="othercat")
        plain = retemplate(make_site(make_account("ben@example.test"), "plain", "Plain"))
        with transaction.atomic():
            with self.assertRaises(snapshot.ProductUnavailable):
                snapshot.take(self.site, inactive.public_id, 1)
            for site, product_id in (
                (self.site, foreign.public_id),  # in another catalog
                (self.site, MISSING_PRODUCT),
                (self.site, str(self.product.pk)),  # a row number is not an id
                (plain, self.product.public_id),  # a site that reads no catalog
            ):
                with self.subTest(product_id=product_id), self.assertRaises(snapshot.ProductNotFound):
                    snapshot.take(site, product_id, 1)

    def test_a_bad_quantity_is_refused(self):
        for quantity in (0, -1, True, "2"):
            with self.subTest(quantity=quantity), transaction.atomic(), self.assertRaises(ValueError):
                snapshot.take(self.site, self.product.public_id, quantity)

    def test_a_snapshot_is_unchanged_by_a_later_edit(self):
        with transaction.atomic():
            taken = snapshot.take(self.site, self.product.public_id, 1)
        CatalogProduct.objects.filter(pk=self.product.pk).update(srp_centavos=15000, revision=2)
        self.assertEqual(
            (taken.srp_centavos, taken.revision, taken.line_members_total_centavos),
            (9999, 1, 6499),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            taken.srp_centavos = 100

    def test_while_the_catalog_is_off_nothing_is_found_and_no_table_is_read(self):
        for off in ({"SANTE_CATALOG_ENABLED": False}, {"SANTE_SETTINGS_ENABLED": False}):
            with self.subTest(off=off), override_settings(**off), transaction.atomic():
                with self.assertNumQueries(0), self.assertRaises(snapshot.ProductNotFound):
                    snapshot.take(self.site, self.product.public_id, 1)
