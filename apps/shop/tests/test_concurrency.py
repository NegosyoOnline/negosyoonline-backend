"""Simultaneous placements and confirmations. These need real row locks and threads that share a
database, so they run on PostgreSQL only (CI's "PostgreSQL and Redis" job)."""
from decimal import Decimal
from unittest import skipUnless

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.accounts.models import Account
from apps.billing.tests.base import make_live_subscription
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import make_product
from apps.common.errors import ApiError
from apps.members.tests.test_concurrency import run_together
from apps.payments.tests.base import PNG, make_payment_account, use_local_private_store
from apps.shop import placement, quote, review
from apps.shop.models import ShopOrder
from apps.shop.tests.owner_base import make_order
from apps.sites.models import Site


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(
    SHOP_ENABLED=True, SANTE_SETTINGS_ENABLED=True, SANTE_CATALOG_ENABLED=True,
    PAYMENT_METHODS_ENABLED=True, MEMBERS_ENABLED=True, BILLING_ENABLED=True,
)
class ShopConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        use_local_private_store(self)
        self.site = make_live_subscription().site
        self.account = make_payment_account(self.site)
        self.product = make_product("FX-A", "FX-A", 10000, "0.3500")
        CatalogProduct.objects.filter(pk=self.product.pk).update(
            weight_kg=Decimal("0.25"), volume_slot=Decimal("1"), shipping_qty=1
        )
        self.lines = [{"id": self.product.public_id, "qty": 1}]
        self.fingerprint = quote.quote(self.site, self.lines)["fingerprint"]

    def place(self, key):
        return placement.place(
            Site.objects.get(pk=self.site.pk), lines=self.lines, fingerprint=self.fingerprint,
            submit_key=key, address={"line": "1 St", "barangay": "Bagumbayan", "city": "Quezon City",
                                     "province": "Metro Manila", "zip": "", "landmark": ""},
            receiver={"receiver_name": "Maria Santos", "receiver_mobile": "09171234567"},
            data={"account_id": self.account.public_id, "reference_number": "R1", "amount_sent": "100"},
            upload=SimpleUploadedFile("p.png", PNG, content_type="image/png"),
        )

    def test_one_submit_key_posted_three_times_makes_one_order(self):
        results = run_together(*[lambda: self.place("k")] * 3)
        placed = [r for r in results if isinstance(r, placement.Placed)]
        self.assertEqual(len(placed), 3, results)
        self.assertEqual(sorted(p.replay for p in placed), [False, True, True])
        self.assertEqual(ShopOrder.objects.count(), 1)

    def test_two_buyers_at_once_get_two_different_numbers(self):
        results = run_together(lambda: self.place("a"), lambda: self.place("b"))
        self.assertTrue(all(isinstance(r, placement.Placed) for r in results), results)
        self.assertEqual(sorted(ShopOrder.objects.values_list("order_number", flat=True)), ["SO00001", "SO00002"])

    def test_two_confirmations_at_once_confirm_once(self):
        order, _ = make_order(self.site)
        owner = Account.objects.get(pk=self.site.account_id)
        results = run_together(
            lambda: review.confirm_receipt(owner, self.site, order.pk, state_version=1),
            lambda: review.confirm_receipt(owner, self.site, order.pk, state_version=1),
        )
        errors = [r for r in results if isinstance(r, ApiError)]
        self.assertEqual(len(errors), 1, results)
        self.assertIn(errors[0].code, ("stale", "wrong_status"))
        self.assertEqual(ShopOrder.objects.get().state_version, 2)

    def test_confirm_and_cancel_at_once_leave_one_outcome(self):
        order, _ = make_order(self.site)
        owner = Account.objects.get(pk=self.site.account_id)
        results = run_together(
            lambda: review.confirm_receipt(owner, self.site, order.pk, state_version=1),
            lambda: review.cancel(owner, self.site, order.pk, state_version=1, note="no"),
        )
        self.assertEqual(sum(isinstance(r, ApiError) for r in results), 1, results)
        self.assertIn(ShopOrder.objects.get().payment_status, ("verified", "cancelled"))
