"""Collecting the storage overage (Billing Build B4, PR 2): the amount frozen with a proof,
its settlement, the usage-only payment and the write-offs. Lines come only from the intake."""
from unittest import mock

from django.test import TestCase, override_settings

from apps.billing import clock, plan, services, usage
from apps.billing.models import BankTransferPayment, ChargeLine, Subscription, UsageEntry
from apps.billing.tests.base import (
    CYCLE_1_END, JPEG, PNG, SECOND, days, fresh, make_active, make_operator, make_suspended,
    submit, transfers_on,
)
from apps.common.errors import ApiError

GB = plan.GB_BYTES
MONTHLY = 249_900
LINE = 6_000  # 7 GB: two started GB at PHP 30
State = Subscription.State


def snapshot(subscription, ref="s1", size=7 * GB, at=CYCLE_1_END, now=None):
    return usage.record_storage_snapshot(
        subscription.site, source_reference=ref, period_end_at=at, measured_bytes=size,
        measured_at=at, now=now or at + SECOND,
    )


@override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True)
class CollectionCase(TestCase):
    def setUp(self):
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.operator = make_operator()

    def refused(self, code, call, *args, **kwargs):
        with self.assertRaises(ApiError) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def confirm(self, payment, at, amount=None):
        return services.confirm_transfer(
            payment, self.operator, amount_received_centavos=amount or payment.amount_centavos,
            now=at,
        )


class RenewalTests(CollectionCase):
    def test_the_frozen_amount_is_the_price_plus_the_overage_of_ended_periods(self):
        subscription = make_active()
        snapshot(subscription)
        payment = submit(subscription, CYCLE_1_END + days(1))
        self.assertEqual(
            (payment.amount_centavos, payment.usage_centavos), (MONTHLY + LINE, LINE)
        )
        self.assertEqual(payment.usage_line_max_id, ChargeLine.objects.get().pk)

    def test_nothing_changes_without_overage_or_with_the_switch_off(self):
        subscription = make_active()
        self.assertEqual(submit(subscription, CYCLE_1_END - days(2)).amount_centavos, MONTHLY)
        snapshot(subscription)
        with override_settings(BILLING_USAGE_ENABLED=False):
            payment = submit(subscription, CYCLE_1_END + days(1), content=PNG)
        self.assertEqual((payment.amount_centavos, payment.usage_centavos), (MONTHLY, 0))

    def test_payment_options_show_the_overage_apart(self):
        subscription = make_active()
        snapshot(subscription)
        options = services.payment_options(subscription, CYCLE_1_END + days(1))
        usage_block = options["usage"]
        self.assertEqual(
            (usage_block["centavos"], usage_block["lines"], usage_block["payable_alone"]),
            (LINE, 1, False),
        )
        self.assertEqual(
            [(i["kind"], i["amount_centavos"]) for i in usage_block["items"]],
            [("overage_storage", LINE)],
        )
        offer = options["offers"][0]
        self.assertEqual((offer["amount_centavos"], offer["usage_centavos"]), (MONTHLY + LINE, LINE))

    def test_confirming_settles_the_cycle_and_exactly_those_lines(self):
        subscription = make_active()
        snapshot(subscription)
        payment = submit(subscription, CYCLE_1_END + days(1))
        confirmed = self.confirm(payment, CYCLE_1_END + days(2))
        line = ChargeLine.objects.get()
        self.assertEqual((line.status, line.settled_payment_id), ("settled", confirmed.pk))
        self.assertIsNotNone(confirmed.cycle)
        self.assertEqual(usage.due(fresh(subscription), CYCLE_1_END + days(3)).centavos, 0)
        event = confirmed.events.get(code="confirmed")
        self.assertEqual(event.detail["usage_lines"], 1)

    def test_a_line_made_after_the_proof_waits_for_the_next_collection(self):
        subscription = make_active()
        snapshot(subscription)
        payment = submit(subscription, CYCLE_1_END + days(1))
        late = UsageEntry.objects.create(  # a later admission would make this row (PR 3)
            subscription=subscription, cycle=subscription.cycles.get(sequence=1), period_index=9,
            source_reference="late", quantity=7 * GB, occurred_at=CYCLE_1_END,
            received_at=CYCLE_1_END, status="counted",
        )
        usage.make_charge(late, labels=["late_admitted"])
        self.confirm(payment, CYCLE_1_END + days(2))
        self.assertEqual(
            list(ChargeLine.objects.order_by("pk").values_list("status", flat=True)),
            ["settled", "open"],
        )

    def test_a_change_between_the_proof_and_the_confirmation_settles_nothing(self):
        subscription = make_active()
        snapshot(subscription)
        payment = submit(subscription, CYCLE_1_END + days(1))
        usage.write_off_line(ChargeLine.objects.get(), "reviewed_not_billable", CYCLE_1_END)
        self.refused("usage_changed", self.confirm, payment, CYCLE_1_END + days(2))
        self.assertEqual(fresh(subscription).cycles.count(), 2)  # trial and cycle 1 only
        self.assertEqual(BankTransferPayment.objects.get().status, "submitted")

    def test_a_replacing_proof_recomputes_the_amount(self):
        subscription = make_active()
        first = submit(subscription, CYCLE_1_END - days(2))
        self.assertEqual(first.usage_centavos, 0)
        snapshot(subscription)
        second = submit(subscription, CYCLE_1_END + days(1), content=PNG)
        self.assertEqual((second.amount_centavos, second.usage_centavos), (MONTHLY + LINE, LINE))
        first.refresh_from_db()
        self.assertEqual(first.status, "replaced")

    def test_a_rejected_proof_attaches_nothing_and_the_next_proof_recomputes(self):
        subscription = make_active()
        snapshot(subscription)
        first = submit(subscription, CYCLE_1_END + days(1))
        services.reject_transfer(first, self.operator, reason="unreadable", now=CYCLE_1_END + days(2))
        line = ChargeLine.objects.get()
        self.assertEqual((line.status, line.settled_payment_id), ("open", None))
        second = submit(subscription, CYCLE_1_END + days(3), content=PNG)
        self.assertEqual((second.amount_centavos, second.usage_centavos), (MONTHLY + LINE, LINE))
        self.assertEqual(second.usage_line_max_id, line.pk)

    def test_the_overview_reports_what_is_outstanding(self):
        subscription = make_active()
        self.assertEqual(services.overview(subscription.site, CYCLE_1_END)["outstanding_centavos"], 0)
        snapshot(subscription)
        self.assertEqual(
            services.overview(subscription.site, CYCLE_1_END + days(1))["outstanding_centavos"],
            LINE,
        )


class UsageOnlyTests(CollectionCase):
    def cancelled_with_a_line(self):
        subscription = make_active()
        services.cancel(subscription, now=CYCLE_1_END - days(2))
        snapshot(subscription)
        return fresh(subscription)

    def test_a_cancelled_site_pays_its_overage_with_no_base_fee_and_no_new_cycle(self):
        subscription = self.cancelled_with_a_line()
        at = CYCLE_1_END + days(1)
        options = services.payment_options(subscription, at)
        self.assertTrue(options["usage"]["payable_alone"])
        payment = submit(subscription, at, plan_code=plan.USAGE_ONLY)
        self.assertEqual(
            (payment.plan_code, payment.amount_centavos, payment.usage_centavos),
            ("usage_only", LINE, LINE),
        )
        self.refused("amount_mismatch", self.confirm, payment, at, amount=MONTHLY)
        confirmed = self.confirm(payment, at + days(1))
        self.assertEqual((confirmed.status, confirmed.cycle), ("confirmed", None))
        self.assertEqual(ChargeLine.objects.get().status, "settled")
        after = fresh(subscription)
        self.assertEqual(after.state, State.CANCELLED)
        self.assertEqual(after.cycles.count(), 2)
        self.assertTrue(usage.closed_and_settled(after, at + days(2)))

    def test_a_charge_for_a_closed_settled_account_is_written_off_at_once(self):
        subscription = make_active()
        cycle = subscription.cycles.get(sequence=1)
        bought = plan.LAUNCH_PROMO  # six monthly periods, so a last one can follow the payment
        cycle.end_at = clock.cycle_end(cycle.start_at, subscription.anchor_day, bought.months)
        for name, value in plan.snapshot(bought).items():
            setattr(cycle, name, value)
        cycle.save()
        periods = clock.allowance_periods(
            cycle.start_at, subscription.anchor_day, bought.months, bought.allowance_periods
        )
        services.cancel(subscription, now=periods[4][1] + days(1))
        snapshot(subscription, "p5", at=periods[4][1])
        final = periods[5][1]
        after = fresh(subscription)
        self.assertEqual(after.service_ended_at, final)
        paid_at = final + SECOND * 3600
        payment = submit(after, paid_at, plan_code=plan.USAGE_ONLY)
        self.confirm(payment, paid_at + SECOND)
        self.assertTrue(usage.closed_and_settled(fresh(subscription), final + SECOND * 7200))
        result = snapshot(subscription, "p6", at=final, now=final + SECOND * 7200)
        self.assertEqual((result.status, result.reason), ("written_off", "after_final_settlement"))
        self.assertEqual(ChargeLine.objects.count(), 1)  # only period 5's line, now settled
        self.assertEqual(ChargeLine.objects.get().status, "settled")

    def test_it_is_refused_when_nothing_is_owed_or_the_base_payment_covers_it(self):
        subscription = make_active()
        self.refused("invalid_request", submit, subscription, CYCLE_1_END - days(2),
                     plan_code=plan.USAGE_ONLY)
        snapshot(subscription)  # active monthly: the renewal collects it
        self.refused("invalid_request", submit, subscription, CYCLE_1_END + days(1),
                     plan_code=plan.USAGE_ONLY)

    def test_it_is_not_known_while_the_switch_is_off(self):
        subscription = self.cancelled_with_a_line()
        with override_settings(BILLING_USAGE_ENABLED=False):
            self.refused("invalid_request", submit, subscription, CYCLE_1_END + days(1),
                         plan_code=plan.USAGE_ONLY)

    def test_it_is_refused_at_the_deletion_date(self):
        subscription = self.cancelled_with_a_line()
        self.refused(
            "past_deletion_date", submit, subscription,
            fresh(subscription).deletion_due_at or CYCLE_1_END + days(400),
            plan_code=plan.USAGE_ONLY,
        )


class WriteOffTests(CollectionCase):
    def test_a_suspended_site_that_never_returns_owes_nothing_at_the_deletion_date(self):
        subscription = make_suspended()
        snapshot(subscription)
        line = ChargeLine.objects.get()
        deletion = fresh(subscription).deletion_due_at
        self.assertIsNotNone(deletion)
        services.sweep(now=deletion - SECOND)
        self.assertEqual(ChargeLine.objects.get().status, "open")
        services.sweep(now=deletion)
        line.refresh_from_db()
        self.assertEqual((line.status, line.closed_reason), ("written_off", "suspension_unreturned"))
        self.assertEqual(usage.write_off_unreturned(deletion), 0)  # twice is safe

    def test_a_closed_line_cannot_be_reopened_or_changed(self):
        subscription = make_active()
        snapshot(subscription)
        line = ChargeLine.objects.get()
        usage.write_off_line(line, "reviewed_not_billable", CYCLE_1_END)
        line.status = "open"
        with self.assertRaises(RuntimeError):
            line.save()
