"""Order overage (OV-1 to OV-9): orders over the 500 a billing month includes are billed at PHP 5
each on the next bill, behind BILLING_ORDER_OVERAGE_ENABLED. The ledger is the storage one.

Counts are patched into `meters.orders_in` except where a test needs the real rule."""
from datetime import datetime, timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from apps.billing import clock, meters, notices, plan, services, usage
from apps.billing.models import (
    BillingCycle, BillingNotice, ChargeLine, Subscription, UsageEntry, UsageEvent,
)
from apps.billing.tests.base import (
    TRIAL_END, CYCLE_1_END, days, fresh, make_active, make_attention, make_operator,
    make_subscription, submit, transfers_on,
)
from apps.billing.tests.test_meters import shop_order
from apps.shop import constants as shop_constants
from apps.shop.models import ShopOrder

Status = UsageEntry.Status
GRACE = plan.LATE_ADMISSION
AFTER = CYCLE_1_END + GRACE  # the first instant the count of period 1 is taken
ON = dict(
    BILLING_ENABLED=True, BILLING_ORDER_OVERAGE_ENABLED=True,
    BILLING_ORDER_OVERAGE_FROM="2026-11-01", OWNER_NOTICES_ENABLED=True,
)
MONTHLY = 249_900
LABEL_20 = "Orders over 500: 20 × PHP 5"
LABEL_40 = "Orders over 500: 40 × PHP 5"


def counts(number):
    return mock.patch("apps.billing.meters.orders_in", return_value=number)


def cycle_1(subscription):
    return fresh(subscription).cycles.get(sequence=1)


def produce(now=AFTER):
    return dict(usage.produce_order_entries(now))


@override_settings(**ON)
class OrderOverageCase(TestCase):
    def setUp(self):
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.operator = make_operator()

    def confirm(self, payment, at, amount=None):
        return services.confirm_transfer(
            payment, self.operator, amount_received_centavos=amount or payment.amount_centavos,
            now=at,
        )

    def make_annual(self):
        """An annual term bought before the trial ends; returns (subscription, end of period 1)."""
        with override_settings(BILLING_ANNUAL_ENABLED=True):
            subscription = make_subscription()
            payment = submit(subscription, TRIAL_END - days(5), plan_code="annual")
            self.confirm(payment, TRIAL_END - days(4))
        subscription = fresh(subscription)
        cycle = cycle_1(subscription)
        end = clock.allowance_periods(cycle.start_at, subscription.anchor_day, 12, 12)[0][1]
        return subscription, end


class ConstantsAndSwitchTests(OrderOverageCase):
    def test_the_figures_and_the_effective_switch(self):
        self.assertEqual(
            (plan.ORDERS_INCLUDED_PER_MONTH, plan.ORDER_OVERAGE_CENTAVOS_PER_ORDER), (500, 500)
        )
        self.assertEqual(meters.ORDERS_INCLUDED_PER_MONTH, 500)
        self.assertTrue(plan.orders_enabled())
        with override_settings(BILLING_ENABLED=False):
            self.assertFalse(plan.orders_enabled())
        with override_settings(BILLING_ORDER_OVERAGE_ENABLED=False):
            self.assertFalse(plan.orders_enabled())

    def test_the_order_switch_is_independent_of_the_storage_switch(self):
        self.assertFalse(plan.usage_enabled())  # storage: switch off and not metered
        self.assertTrue(plan.any_usage_enabled())
        self.assertEqual(usage.enabled_kinds(), ["overage_orders"])

    def test_the_notice_date_must_be_an_iso_date_and_a_month_must_start_on_or_after_it(self):
        manila = clock.MANILA
        self.assertTrue(plan.orders_chargeable(datetime(2026, 11, 1, tzinfo=manila)))
        self.assertFalse(plan.orders_chargeable(datetime(2026, 10, 31, 23, 59, tzinfo=manila)))
        for empty in ("", "  ", "not-a-date", "2026-13-40"):
            with override_settings(BILLING_ORDER_OVERAGE_FROM=empty):
                self.assertIsNone(plan.charged_from())
                self.assertFalse(plan.orders_chargeable(datetime(2030, 1, 1, tzinfo=manila)))


class ProducerTests(OrderOverageCase):
    def test_a_month_over_500_is_counted_with_one_line(self):
        subscription = make_active()
        with counts(520):
            self.assertEqual(produce(), {"counted": 1})
        entry = UsageEntry.objects.get()
        cycle = cycle_1(subscription)
        self.assertEqual(
            (entry.kind, entry.quantity, entry.status, entry.reason, entry.period_index,
             entry.occurred_at, entry.received_at, entry.source_reference),
            ("orders", 520, "counted", "", 1, CYCLE_1_END, AFTER, f"orders:{cycle.pk}:1"),
        )
        line = ChargeLine.objects.get()
        self.assertEqual(
            (line.kind, line.quantity, line.amount_centavos, line.status, line.cycle_id),
            ("overage_orders", 20, 10_000, "open", cycle.pk),
        )
        self.assertEqual(usage.line_label(line.kind, line.quantity, line.amount_centavos), LABEL_20)
        self.assertEqual(UsageEvent.objects.get().code, "recorded_counted")

    def test_501_orders_is_one_order_over(self):
        make_active()
        with counts(501):
            produce()
        self.assertEqual(ChargeLine.objects.get().amount_centavos, 500)

    def test_at_or_below_500_writes_nothing(self):
        make_active()
        for number in (500, 0, 120):
            with counts(number):
                self.assertEqual(produce(), {"within_allowance": 1})
        self.assertFalse(UsageEntry.objects.exists())
        self.assertFalse(ChargeLine.objects.exists())

    def test_nothing_is_counted_before_72_hours_pass(self):
        make_active()
        with counts(900):
            self.assertEqual(produce(AFTER - timedelta(seconds=1)), {})
        self.assertFalse(UsageEntry.objects.exists())

    def test_one_entry_per_period_and_a_replay_creates_nothing_new(self):
        make_active()
        with counts(520):
            produce()
        with counts(900):  # recounted later: the first decision stands, nothing is recomputed
            self.assertEqual(produce(AFTER + days(1)), {})
            again = usage.record_order_count(
                BillingCycle.objects.get(sequence=1), 1, now=AFTER + days(1)
            )
        self.assertTrue(again.replayed)
        self.assertEqual((UsageEntry.objects.count(), ChargeLine.objects.count()), (1, 1))
        self.assertEqual(UsageEntry.objects.get().quantity, 520)
        self.assertEqual(UsageEvent.objects.count(), 1)

    def test_a_cancel_inside_the_72_hours_comes_off_the_count(self):
        subscription = make_active()
        period_start = cycle_1(subscription).start_at
        with mock.patch.object(plan, "ORDERS_INCLUDED_PER_MONTH", 2):
            orders = [shop_order(subscription.site, period_start + days(n)) for n in (1, 2, 3)]
            cancel = ShopOrder.objects.filter(pk=orders[0].pk)
            cancel.update(payment_status=shop_constants.CANCELLED, cancel_note="Out of stock")
            self.assertEqual(produce(), {"within_allowance": 1})  # two count: not over 2
            self.assertFalse(UsageEntry.objects.exists())
            cancel.update(payment_status=shop_constants.VERIFIED, cancel_note="")
            self.assertEqual(produce(), {"counted": 1})  # three count
        self.assertEqual(
            (UsageEntry.objects.get().quantity, ChargeLine.objects.get().amount_centavos), (3, 500)
        )

    def test_an_order_placed_after_the_period_is_not_in_it(self):
        subscription = make_active()
        cycle = cycle_1(subscription)
        with mock.patch.object(plan, "ORDERS_INCLUDED_PER_MONTH", 1):
            shop_order(subscription.site, cycle.end_at - days(1))
            shop_order(subscription.site, cycle.end_at)  # the next month's
            self.assertEqual(produce(), {"within_allowance": 1})

    def test_a_trial_site_has_no_paid_period_to_count(self):
        make_subscription()
        with counts(900):
            self.assertEqual(produce(TRIAL_END + days(30)), {})
        self.assertFalse(UsageEntry.objects.exists())

    def test_the_switch_off_reads_and_writes_nothing(self):
        make_active()
        for settings in (dict(BILLING_ORDER_OVERAGE_ENABLED=False), dict(BILLING_ENABLED=False)):
            with override_settings(**settings), counts(900) as patched:
                self.assertEqual(produce(), {})
                patched.assert_not_called()
        self.assertFalse(UsageEntry.objects.exists())

    def test_the_sweep_runs_the_producer_and_reports_by_status(self):
        make_active()
        with counts(520):
            result = services.sweep(now=AFTER)
        self.assertEqual(result.get("orders_counted"), 1)
        self.assertEqual(ChargeLine.objects.count(), 1)
        with override_settings(BILLING_ORDER_OVERAGE_ENABLED=False), counts(900):
            self.assertNotIn("orders_counted", services.sweep(now=AFTER + days(1)))

    def test_a_dry_run_sweep_writes_no_entry(self):
        make_active()
        with counts(520):
            services.sweep(now=AFTER, dry_run=True)
        self.assertFalse(UsageEntry.objects.exists())


class RoutingTests(OrderOverageCase):
    def one(self, subscription=None, now=AFTER, number=520):
        subscription = subscription or make_active()
        with counts(number):
            produce(now)
        return UsageEntry.objects.get()

    def test_counted(self):
        entry = self.one()
        self.assertEqual((entry.status, entry.reason), ("counted", ""))

    def test_a_trial_cycle_is_not_billable(self):
        subscription = make_subscription()
        trial = subscription.cycles.get(sequence=0)
        self.assertEqual(
            usage._route(subscription, trial, trial.end_at, trial.end_at + GRACE, kind="orders"),
            (Status.NOT_BILLABLE, "trial_cycle"),
        )

    def test_an_exempt_site_is_not_billable_whatever_the_count(self):
        subscription = make_active()
        Subscription.objects.filter(pk=subscription.pk).update(billing_exempt=True)
        entry = self.one(subscription, number=2000)
        self.assertEqual((entry.status, entry.reason), ("not_billable", "test_site"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_a_month_before_the_notice_date_is_not_billable(self):
        with override_settings(BILLING_ORDER_OVERAGE_FROM="2026-11-26"):  # period 1 starts on the 25th
            entry = self.one()
        self.assertEqual((entry.status, entry.reason), ("not_billable", "before_notice"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_a_month_starting_on_the_notice_date_is_billable(self):
        with override_settings(BILLING_ORDER_OVERAGE_FROM="2026-11-25"):
            self.assertEqual(self.one().status, "counted")

    def test_no_notice_date_means_no_month_is_charged(self):
        with override_settings(BILLING_ORDER_OVERAGE_FROM=""):
            entry = self.one()
        self.assertEqual((entry.status, entry.reason), ("not_billable", "before_notice"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_a_run_more_than_7_days_late_needs_review_and_an_operator_can_admit_it(self):
        entry = self.one(now=CYCLE_1_END + days(8))
        self.assertEqual((entry.status, entry.reason), ("needs_review", "late_producer"))
        self.assertFalse(ChargeLine.objects.exists())
        usage.admit(entry.source_reference, self.operator, "the sweep was down")
        entry.refresh_from_db()
        line = ChargeLine.objects.get()
        self.assertEqual(
            (entry.status, line.amount_centavos, line.labels), ("counted", 10_000, ["late_admitted"])
        )

    def test_exactly_7_days_is_still_on_time(self):
        self.assertEqual(self.one(now=CYCLE_1_END + days(7)).status, "counted")

    def test_the_storage_late_rule_does_not_apply_to_orders(self):
        # 4 days after the end is late for a storage snapshot (72 hours) and fine for the producer.
        self.assertEqual(self.one(now=CYCLE_1_END + days(4)).status, "counted")

    def test_a_pending_cycle_holds_and_payment_then_counts_it(self):
        subscription = make_attention()
        pending = subscription.cycles.order_by("-sequence").first()
        self.assertEqual(pending.settlement, BillingCycle.Settlement.PENDING)
        with counts(520), mock.patch.object(services, "catch_up"):
            result = usage.record_order_count(pending, 1, now=pending.end_at + GRACE)
        self.assertEqual((result.status, result.reason), ("held", "cycle_pending"))
        self.assertFalse(ChargeLine.objects.exists())
        usage.release_held(pending, True)
        self.assertEqual(ChargeLine.objects.get().amount_centavos, 10_000)
        usage.release_held(pending, True)  # twice is safe
        self.assertEqual(ChargeLine.objects.count(), 1)

    def test_a_held_row_is_written_off_when_the_cycle_lapses(self):
        subscription = make_attention()
        pending = subscription.cycles.order_by("-sequence").first()
        with counts(520), mock.patch.object(services, "catch_up"):
            usage.record_order_count(pending, 1, now=pending.end_at + GRACE)
        usage.release_held(pending, False)
        entry = UsageEntry.objects.get()
        self.assertEqual((entry.status, entry.reason), ("written_off", "cycle_lapsed"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_a_lapsed_cycle_writes_it_off_without_a_charge(self):
        subscription = make_attention()
        pending = subscription.cycles.order_by("-sequence").first()
        with counts(520):  # catch_up lapses the pending cycle: its month was never paid for
            result = usage.record_order_count(pending, 1, now=pending.end_at + GRACE)
        self.assertEqual((result.status, result.reason), ("written_off", "cycle_lapsed"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_after_the_account_closed_and_settled_it_is_written_off(self):
        make_active()
        with counts(520), mock.patch.object(usage, "closed_and_settled", return_value=True):
            produce()
        entry = UsageEntry.objects.get()
        self.assertEqual((entry.status, entry.reason), ("written_off", "after_final_settlement"))
        self.assertFalse(ChargeLine.objects.exists())


class CollectionTests(OrderOverageCase):
    def counted(self, number=520):
        subscription = make_active()
        with counts(number):
            produce()
        return subscription

    def test_the_monthly_renewal_carries_the_order_line_beside_the_base_fee(self):
        subscription = self.counted()
        at = AFTER + days(1)
        options = services.payment_options(fresh(subscription), at)
        self.assertEqual(options["usage"]["centavos"], 10_000)
        self.assertEqual(options["offers"][0]["amount_centavos"], MONTHLY + 10_000)
        self.assertEqual(options["offers"][0]["usage_centavos"], 10_000)
        item = options["usage"]["items"][0]
        self.assertEqual(
            (item["kind"], item["label"], item["quantity"], item["unit_centavos"],
             item["amount_centavos"], item["period_start_at"], item["period_end_at"],
             item["due_at"]),
            ("overage_orders", LABEL_20, 20, 500, 10_000, TRIAL_END, CYCLE_1_END, None),
        )
        payment = submit(fresh(subscription), at)
        self.assertEqual(
            (payment.amount_centavos, payment.usage_centavos), (MONTHLY + 10_000, 10_000)
        )
        confirmed = self.confirm(payment, at + days(1))
        line = ChargeLine.objects.get()
        self.assertEqual((line.status, line.settled_payment_id), ("settled", confirmed.pk))
        after = services.payment_options(fresh(subscription), at + days(2))
        self.assertEqual(after["usage"]["centavos"], 0)

    def test_the_overview_shows_the_outstanding_order_overage(self):
        subscription = self.counted()
        self.assertEqual(usage.outstanding(fresh(subscription), AFTER + days(1)), 10_000)

    def test_a_storage_line_is_not_collected_while_storage_is_off(self):
        subscription = self.counted()
        ChargeLine.objects.update(kind="overage_storage")  # the other kind, whose switch is off
        self.assertEqual(usage.due(subscription, AFTER + days(1)).centavos, 0)

    def test_with_the_order_switch_off_the_line_is_not_collected(self):
        subscription = self.counted()
        with override_settings(BILLING_ORDER_OVERAGE_ENABLED=False):
            self.assertEqual(usage.due(subscription, AFTER + days(1)).centavos, 0)
            payment = submit(fresh(subscription), AFTER + days(1))
            self.assertEqual((payment.amount_centavos, payment.usage_centavos), (MONTHLY, 0))

    def test_the_line_is_never_collected_twice(self):
        subscription = self.counted()
        at = AFTER + days(1)
        self.confirm(submit(fresh(subscription), at), at + days(1))
        again = submit(fresh(subscription), at + days(2), content=b"\x89PNG\r\n\x1a\n" + b"x" * 40)
        self.assertEqual(again.usage_centavos, 0)

    def test_an_annual_term_gets_a_usage_only_bill_due_in_7_days(self):
        subscription, end = self.make_annual()
        now = end + GRACE
        with counts(540):
            self.assertEqual(produce(now), {"counted": 1})
        options = services.payment_options(fresh(subscription), now + days(1))
        self.assertTrue(options["usage"]["payable_alone"])
        item = options["usage"]["items"][0]
        self.assertEqual((item["label"], item["due_at"]), (LABEL_40, now + days(7)))
        bill = submit(fresh(subscription), now + days(1), plan_code=plan.USAGE_ONLY)
        self.assertEqual(
            (bill.plan_code, bill.amount_centavos, bill.cycle_id), ("usage_only", 20_000, None)
        )
        self.confirm(bill, now + days(2))
        self.assertEqual(ChargeLine.objects.get().status, "settled")
        self.assertEqual(fresh(subscription).cycles.count(), 2)  # the trial and the term: no new one

    def test_the_owner_read_lists_the_lines_while_storage_is_off(self):
        subscription = self.counted()
        body = usage.owner_read(fresh(subscription), AFTER + days(1))
        self.assertEqual(body["due_centavos"], 10_000)
        self.assertEqual(body["items"][0]["label"], LABEL_20)
        self.assertFalse(body["storage"]["metered"])


class EmailTests(OrderOverageCase):
    NOW = TRIAL_END + days(10)

    def sent(self, number, now=None):
        mail.outbox.clear()
        with counts(number):
            result = dict(notices._order_meter_emails(now or self.NOW))
        return result, [m.subject for m in mail.outbox]

    def test_80_percent_once_then_100_percent_once(self):
        make_active()
        self.assertEqual(self.sent(399), ({}, []))
        self.assertEqual(
            self.sent(400),
            ({"orders_80": 1}, ["You've used 400 of your 500 orders this month"]),
        )
        self.assertEqual(self.sent(450), ({}, []))  # once per period
        self.assertEqual(
            self.sent(500), ({"orders_100": 1}, ["You've reached your plan's 500 orders"])
        )
        self.assertEqual(self.sent(700), ({}, []))
        self.assertEqual(BillingNotice.objects.count(), 2)

    def test_a_site_that_jumps_past_500_gets_only_the_100_percent_email(self):
        make_active()
        self.assertEqual(self.sent(520)[1], ["You've reached your plan's 500 orders"])

    def test_the_wording(self):
        subscription = make_active()
        self.sent(400)
        self.assertIn(
            "You have used 400 of the 500 orders included this month. Orders above 500 are "
            "billed at PHP 5 each on your next bill. Your store keeps taking orders as usual.",
            mail.outbox[0].body,
        )
        self.assertEqual(mail.outbox[0].to, [subscription.site.account.email])
        self.sent(500)
        self.assertIn(
            "You have reached your plan's 500 orders this month. Orders above 500 are billed "
            "at PHP 5 each on your next bill. Your store keeps taking orders as usual.",
            mail.outbox[0].body,
        )

    def test_a_date_outside_every_paid_month_sends_nothing(self):
        make_active()
        self.assertEqual(self.sent(600, CYCLE_1_END + days(10))[1], [])

    def test_none_with_a_switch_off_before_the_notice_date_or_for_exempt_and_lapsed_sites(self):
        subscription = make_active()
        for settings in (
            dict(BILLING_ORDER_OVERAGE_ENABLED=False), dict(OWNER_NOTICES_ENABLED=False),
            dict(BILLING_ENABLED=False), dict(BILLING_ORDER_OVERAGE_FROM="2026-11-26"),
            dict(BILLING_ORDER_OVERAGE_FROM=""),
        ):
            with override_settings(**settings):
                self.assertEqual(self.sent(600)[1], [], settings)
        Subscription.objects.filter(pk=subscription.pk).update(billing_exempt=True)
        self.assertEqual(self.sent(600)[1], [])
        self.assertFalse(BillingNotice.objects.exists())

    def test_a_trial_gets_none(self):
        make_subscription()
        self.assertEqual(self.sent(600, TRIAL_END - days(2))[1], [])

    def test_the_scheduled_notice_run_sends_them(self):
        make_active()
        with counts(400):
            sent = notices.run(self.NOW)
        self.assertEqual(sent.get("orders_80"), 1)

    def test_the_annual_bill_email_goes_once_with_its_due_date(self):
        subscription, end = self.make_annual()
        now = end + GRACE
        with counts(540):
            produce(now)
        mail.outbox.clear()
        self.assertEqual(dict(notices._orders_bill_emails(now + days(1))), {"orders_bill": 1})
        self.assertEqual(dict(notices._orders_bill_emails(now + days(1))), {})
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Your NegosyoOnline orders-over-500 bill")
        self.assertIn(f"{LABEL_40} = PHP 200.00", message.body)
        due = (now + days(7)).astimezone(clock.MANILA)
        self.assertIn(f"Please pay by {due.day} {due:%B %Y}", message.body)

    def test_a_monthly_plan_gets_no_separate_bill_email(self):
        make_active()
        with counts(540):
            produce()
        mail.outbox.clear()
        self.assertEqual(dict(notices._orders_bill_emails(AFTER + days(1))), {})
        self.assertEqual(mail.outbox, [])


class MetersFieldsTests(OrderOverageCase):
    def read(self, subscription, now):
        with counts(10):
            return meters.read(fresh(subscription), now)["orders"]

    def test_applies_in_a_chargeable_paid_billing_month(self):
        body = self.read(make_active(), TRIAL_END + days(3))
        self.assertEqual(
            (body["period_kind"], body["overage_applies"], body["overage_centavos_per_order"]),
            ("billing_month", True, 500),
        )

    def test_not_in_a_trial_for_an_exempt_site_before_the_date_or_with_the_switch_off(self):
        trial = make_subscription(email="t@example.test", name="trial-store")
        body = self.read(trial, trial.trial_start_at + days(2))
        self.assertEqual((body["period_kind"], body["overage_applies"]), ("trial", False))
        subscription = make_active()
        at = TRIAL_END + days(3)
        with override_settings(BILLING_ORDER_OVERAGE_FROM="2026-11-26"):
            self.assertFalse(self.read(subscription, at)["overage_applies"])
        with override_settings(BILLING_ORDER_OVERAGE_FROM=""):
            self.assertFalse(self.read(subscription, at)["overage_applies"])
        with override_settings(BILLING_ORDER_OVERAGE_ENABLED=False):
            body = self.read(subscription, at)
            self.assertEqual(
                (body["overage_applies"], body["overage_centavos_per_order"]), (False, 500)
            )
        Subscription.objects.filter(pk=subscription.pk).update(billing_exempt=True)
        self.assertFalse(self.read(subscription, at)["overage_applies"])

    def test_every_field_is_there(self):
        self.assertEqual(
            set(self.read(make_active(), TRIAL_END + days(1))),
            {"count", "limit", "period_start", "period_end", "period_kind", "overage_applies",
             "overage_centavos_per_order"},
        )
