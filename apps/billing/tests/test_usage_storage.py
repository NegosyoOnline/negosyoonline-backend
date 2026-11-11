"""The storage usage intake (Billing Build B4, PR 1), always through `record_storage_snapshot`."""
from unittest import mock

from django.test import TestCase, override_settings

from apps.billing import clock, plan, services, usage
from apps.billing.models import BillingCycle, ChargeLine, UsageEntry, UsageEvent
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, TRIAL_END, days, fresh, make_active, make_attention, make_subscription,
)

GB = plan.GB_BYTES
Status = UsageEntry.Status


def snap(subscription, size, *, at=CYCLE_1_END, now=None, ref="snap-1", measured_at=None):
    return usage.record_storage_snapshot(
        subscription.site, source_reference=ref, period_end_at=at, measured_bytes=size,
        measured_at=measured_at or at, now=now or at + SECOND,
    )


@override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True)
class StorageIntakeTests(TestCase):
    def setUp(self):
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_started_gb_thresholds(self):
        self.assertEqual(usage.started_gb(5 * GB), 0)
        self.assertEqual(usage.started_gb(5 * GB + 1), 1)
        self.assertEqual(usage.started_gb(6 * GB), 1)
        self.assertEqual(usage.started_gb(6 * GB + 1), 2)
        self.assertEqual(usage.started_gb(0), 0)

    def test_at_or_below_the_allowance_writes_nothing(self):
        subscription = make_active()
        result = snap(subscription, 5 * GB)
        self.assertEqual(result.status, "within_allowance")
        self.assertFalse(UsageEntry.objects.exists())

    def test_one_byte_over_charges_one_started_gb(self):
        subscription = make_active()
        result = snap(subscription, 5 * GB + 1)
        self.assertEqual((result.status, result.charge_centavos), ("counted", 3000))
        line = ChargeLine.objects.get()
        self.assertEqual((line.quantity, line.amount_centavos), (1, 3000))

    def test_six_gb_and_a_byte_charge_two_gb(self):
        subscription = make_active()
        self.assertEqual(snap(subscription, 6 * GB + 1).charge_centavos, 6000)
        other = make_active(email="b@example.test", name="b-store")
        self.assertEqual(snap(other, 6 * GB, ref="other").charge_centavos, 3000)

    def test_replay_returns_the_same_result_and_never_charges_twice(self):
        subscription = make_active()
        first = snap(subscription, 7 * GB)
        again = snap(subscription, 9 * GB)
        self.assertTrue(again.replayed)
        self.assertEqual(again.charge_centavos, first.charge_centavos)
        self.assertEqual(ChargeLine.objects.count(), 1)
        self.assertEqual(UsageEntry.objects.get().quantity, 7 * GB)

    def test_a_second_reference_for_the_same_period_is_a_replay_of_the_first(self):
        subscription = make_active()
        snap(subscription, 7 * GB, ref="a")
        second = snap(subscription, 20 * GB, ref="b")
        self.assertTrue(second.replayed)
        self.assertEqual(UsageEntry.objects.count(), 1)
        self.assertEqual(ChargeLine.objects.count(), 1)

    def test_a_reference_used_by_another_subscription_is_not_counted(self):
        one = make_active()
        two = make_active(email="b@example.test", name="b-store")
        snap(one, 7 * GB, ref="shared")
        result = snap(two, 7 * GB, ref="shared")
        self.assertEqual((result.status, result.reason), ("not_counted", "reference_reused"))

    def test_not_at_the_period_end_is_not_counted(self):
        subscription = make_active()
        result = snap(subscription, 7 * GB, measured_at=CYCLE_1_END - days(1))
        self.assertEqual((result.status, result.reason), ("not_counted", "not_at_period_end"))
        result = snap(subscription, 7 * GB, at=CYCLE_1_END - days(1), ref="x")
        self.assertEqual(result.reason, "not_at_period_end")
        self.assertFalse(UsageEntry.objects.exists())

    def test_a_snapshot_in_the_future_is_not_counted(self):
        subscription = make_active()
        result = snap(subscription, 7 * GB, now=CYCLE_1_END - SECOND)
        self.assertEqual((result.status, result.reason), ("not_counted", "in_the_future"))

    def test_malformed_calls_raise(self):
        subscription = make_active()
        with self.assertRaises(ValueError):
            snap(subscription, -1)
        with self.assertRaises(ValueError):
            snap(subscription, 7 * GB, ref="")
        with self.assertRaises(ValueError):
            snap(subscription, True)

    def test_the_switches_and_the_metering_gate(self):
        subscription = make_active()
        with mock.patch.object(plan, "STORAGE_METERED", False):
            self.assertEqual(snap(subscription, 7 * GB).status, "not_metered")
        with override_settings(BILLING_USAGE_ENABLED=False):
            self.assertEqual(snap(subscription, 7 * GB).status, "disabled")
        with override_settings(BILLING_ENABLED=False):
            self.assertEqual(snap(subscription, 7 * GB).status, "disabled")
        self.assertFalse(UsageEntry.objects.exists())

    def test_a_test_site_is_skipped_and_auditable(self):
        subscription = make_active()
        type(subscription).objects.filter(pk=subscription.pk).update(billing_exempt=True)
        result = snap(subscription, 7 * GB)
        self.assertEqual((result.status, result.reason), ("not_billable", "test_site"))
        self.assertFalse(ChargeLine.objects.exists())
        self.assertEqual(UsageEvent.objects.count(), 1)

    def test_the_trial_is_not_billable(self):
        subscription = make_subscription()
        result = snap(subscription, 7 * GB, at=TRIAL_END, now=TRIAL_END + SECOND)
        self.assertEqual((result.status, result.reason), ("not_billable", "trial_cycle"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_past_72_hours_needs_review_and_charges_nothing(self):
        subscription = make_active()
        on_time = snap(subscription, 7 * GB, now=CYCLE_1_END + days(3))
        self.assertEqual(on_time.status, "counted")
        late = snap(
            make_active(email="b@example.test", name="b-store"), 7 * GB,
            now=CYCLE_1_END + days(3) + SECOND, ref="late",
        )
        self.assertEqual((late.status, late.reason), ("needs_review", "late_over_72h"))
        self.assertEqual(ChargeLine.objects.count(), 1)

    def test_an_unmeasured_period_writes_nothing(self):
        make_active()
        self.assertFalse(UsageEntry.objects.exists())
        self.assertFalse(UsageEvent.objects.exists())

    def test_every_decision_leaves_an_event(self):
        subscription = make_active()
        snap(subscription, 7 * GB)
        event = UsageEvent.objects.get()
        self.assertEqual(event.code, "recorded_counted")
        self.assertEqual(event.entry, UsageEntry.objects.get())

    def test_a_usage_entry_cannot_be_deleted(self):
        subscription = make_active()
        snap(subscription, 7 * GB)
        with self.assertRaises(RuntimeError):
            UsageEntry.objects.get().delete()

    def _pending(self):
        """A pending cycle whose period end has passed while it is still pending. The lifecycle
        lapses a pending cycle after 7 days, so the lapse is held off to reach the route."""
        subscription = make_attention()
        cycle = subscription.cycles.order_by("-sequence").first()
        self.assertEqual(cycle.settlement, BillingCycle.Settlement.PENDING)
        return subscription, cycle

    def test_pending_cycle_holds_then_release_counts_it(self):
        subscription, cycle = self._pending()
        end = cycle.end_at
        with mock.patch.object(services, "catch_up"):
            result = snap(subscription, 7 * GB, at=end, now=end + SECOND)
        self.assertEqual((result.status, result.reason), ("held", "cycle_pending"))
        self.assertFalse(ChargeLine.objects.exists())
        usage.release_held(cycle, True)
        entry = UsageEntry.objects.get()
        self.assertEqual(entry.status, "counted")
        self.assertEqual(ChargeLine.objects.get().amount_centavos, 6000)
        usage.release_held(cycle, True)  # twice is safe
        self.assertEqual(ChargeLine.objects.count(), 1)

    def test_pending_cycle_holds_then_lapse_writes_it_off(self):
        subscription, cycle = self._pending()
        end = cycle.end_at
        with mock.patch.object(services, "catch_up"):
            snap(subscription, 7 * GB, at=end, now=end + SECOND)
        usage.release_held(cycle, False)
        entry = UsageEntry.objects.get()
        self.assertEqual((entry.status, entry.reason), ("written_off", "cycle_lapsed"))
        self.assertFalse(ChargeLine.objects.exists())
        self.assertEqual(
            sorted(UsageEvent.objects.values_list("code", flat=True)),
            ["recorded_held", "written_off_lapsed"],
        )

    def test_a_lapsed_cycle_writes_the_snapshot_off_without_a_charge(self):
        subscription, cycle = self._pending()
        end = cycle.end_at
        result = snap(subscription, 7 * GB, at=end, now=end + SECOND)
        self.assertEqual((result.status, result.reason), ("written_off", "cycle_lapsed"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_the_lifecycle_releases_held_snapshots(self):
        """`_lapse_pending` and `recovered` call `release_held` (hooks in services.py)."""
        subscription, cycle = self._pending()
        end = cycle.end_at
        with mock.patch.object(services, "catch_up"):
            snap(subscription, 7 * GB, at=end, now=end + SECOND)
        services.catch_up(fresh(subscription), end + SECOND)
        self.assertEqual(UsageEntry.objects.get().status, "written_off")


@override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True)
class StoragePeriodTests(TestCase):
    def setUp(self):
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _term(self, bought):
        """Cycle 1 of an active subscription, made a term of `bought` (six months or a year)."""
        subscription = make_active()
        cycle = subscription.cycles.get(sequence=1)
        cycle.end_at = clock.cycle_end(cycle.start_at, subscription.anchor_day, bought.months)
        for name, value in plan.snapshot(bought).items():
            setattr(cycle, name, value)
        cycle.save()
        periods = clock.allowance_periods(
            cycle.start_at, subscription.anchor_day, bought.months, bought.allowance_periods
        )
        return subscription, cycle, periods

    def test_each_of_the_six_promo_months_has_its_own_snapshot(self):
        subscription, cycle, periods = self._term(plan.LAUNCH_PROMO)
        self.assertEqual(len(periods), 6)
        for index, (_, end) in enumerate(periods, start=1):
            result = snap(subscription, 7 * GB, at=end, now=end + SECOND, ref=f"p{index}")
            self.assertEqual(result.status, "counted", index)
        self.assertEqual(
            list(UsageEntry.objects.order_by("period_index").values_list("period_index", flat=True)),
            [1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(ChargeLine.objects.count(), 6)

    def test_an_annual_term_has_twelve_periods_and_one_per_period(self):
        subscription, cycle, periods = self._term(plan.ANNUAL)
        self.assertEqual(len(periods), 12)
        end = periods[2][1]
        snap(subscription, 7 * GB, at=end, now=end + SECOND, ref="a")
        again = snap(subscription, 9 * GB, at=end, now=end + SECOND, ref="b")
        self.assertTrue(again.replayed)
        self.assertEqual(UsageEntry.objects.get().period_index, 3)

    def test_an_instant_inside_a_period_is_not_a_period_end(self):
        subscription, cycle, periods = self._term(plan.LAUNCH_PROMO)
        middle = periods[1][0] + days(3)
        result = snap(subscription, 7 * GB, at=middle, now=middle + SECOND)
        self.assertEqual(result.reason, "not_at_period_end")

    def test_no_cycle_needs_review(self):
        subscription = make_active()
        before = TRIAL_END - days(30)
        result = snap(subscription, 7 * GB, at=before, now=CYCLE_1_END)
        self.assertEqual((result.status, result.reason), ("needs_review", "no_cycle"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_after_service_end_needs_review(self):
        subscription, cycle, periods = self._term(plan.LAUNCH_PROMO)
        end = periods[3][1]
        type(subscription).objects.filter(pk=subscription.pk).update(
            service_ended_at=periods[1][1]
        )
        result = snap(subscription, 7 * GB, at=end, now=end + SECOND)
        self.assertEqual((result.status, result.reason), ("needs_review", "after_service_end"))
        self.assertFalse(ChargeLine.objects.exists())
