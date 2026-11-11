"""Simultaneous storage snapshots. These need real row locks, so they run on PostgreSQL only."""
from unittest import mock, skipUnless

from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.billing import plan, usage
from apps.billing.models import ChargeLine, UsageEntry
from apps.billing.tests.base import CYCLE_1_END, SECOND, make_active
from apps.sites.tests.test_concurrency import run_together

GB = plan.GB_BYTES


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True)
class UsageConcurrencyTests(TransactionTestCase):
    def setUp(self):
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _call(self, subscription, ref):
        return lambda: usage.record_storage_snapshot(
            subscription.site, source_reference=ref, period_end_at=CYCLE_1_END,
            measured_bytes=7 * GB, measured_at=CYCLE_1_END, now=CYCLE_1_END + SECOND,
        )

    def test_the_same_reference_twice_at_once_charges_once(self):
        subscription = make_active()
        results = run_together(*[self._call(subscription, "same") for _ in range(2)])
        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual(UsageEntry.objects.count(), 1)
        self.assertEqual(ChargeLine.objects.count(), 1)

    def test_two_references_for_one_period_at_once_charge_once(self):
        subscription = make_active()
        results = run_together(self._call(subscription, "one"), self._call(subscription, "two"))
        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual(UsageEntry.objects.count(), 1)
        self.assertEqual(ChargeLine.objects.count(), 1)
