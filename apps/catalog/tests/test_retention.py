"""Account disposal and export beside the shared catalog. The catalog is shared, not an
account's: a disposal leaves every product and revision as it was, and an export holds no
catalog row. A revision names its writer by row number only, which is no personal value."""
import json

from django.test import override_settings

from apps.accounts import retention as rules
from apps.catalog import services
from apps.catalog.tests.base import catalog_rows, switched_on
from apps.sante.tests.test_retention import SanteDisposalTestCase
from apps.sites.models import Site

CREATE = {
    "code": "FX-KEPT", "name": "Kept Product", "srp_centavos": 9999, "earnings_rate": "0.3500",
    "short_description": "", "long_description": "", "weight_kg": "0.00",
}


@switched_on
@override_settings(RETENTION_ENABLED=True)
class CatalogRetentionTests(SanteDisposalTestCase):
    def setUp(self):
        super().setUp()
        self.own(self.site)
        self.product = services.create_product(self.account, self.site, dict(CREATE))
        services.update_product(
            self.account, self.site, self.product.public_id, {"revision": 1, "name": "Renamed"}
        )

    def test_the_owner_account_is_refused_and_the_catalog_is_unchanged(self):
        before = catalog_rows()
        with self.assertRaises(rules.NotDisposable):
            self.dispose()
        self.assertEqual(catalog_rows(), before)

    def test_once_another_owner_is_named_a_disposal_leaves_products_and_revisions_unchanged(self):
        self.own(self.their_site)
        before = catalog_rows()
        counts = self.dispose()
        self.assertIsNotNone(Site.objects.get(pk=self.site.pk).deleted_at)
        self.assertEqual(catalog_rows(), before)
        self.assertFalse([name for name in counts if "catalog" in name and name != "catalog_owner"])
        # The new owner site still reads and writes the same products.
        self.assertEqual(
            [row["name"] for row in services.list_for(self.their_site)["products"]], ["Renamed"]
        )
        _, _, revisions = before
        self.assertEqual(
            [(row["revision"], row["changed_by_account_id"], row["changed_by_site_id"])
             for row in revisions],
            [(1, self.account.pk, self.site.pk), (2, self.account.pk, self.site.pk)],
        )

    def test_a_revision_holds_no_personal_value_of_its_writer(self):
        text = json.dumps(catalog_rows(), default=str)
        self.assert_no_personal_value(text)
        self.assertNotIn(self.account.public_id, text)

    def test_the_export_holds_no_catalog_row(self):
        before = catalog_rows()
        data = rules.export_account_data(self.account, actor=self.operator, request_ref="DPO-3")
        self.assertFalse([name for name in data if "catalog" in name or "product" in name])
        text = json.dumps(data, default=str)
        self.assertNotIn("FX-KEPT", text)
        self.assertNotIn("Renamed", text)
        self.assertEqual(catalog_rows(), before)
