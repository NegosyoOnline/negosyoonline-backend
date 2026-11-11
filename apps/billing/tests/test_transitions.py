"""The table in section 5.4 of the plan: every transition, written once, with its effective time."""
from django.test import TestCase, override_settings

from apps.billing import services
from apps.billing.models import BillingCycle, Subscription, SubscriptionTransition
from apps.billing.tests.base import (
    CYCLE_1_END, CYCLE_2_END, SECOND, T0, TRIAL_END, collection_is_live, days, fresh, make_active,
    make_attention, make_subscription, make_suspended, manila, method_on_file,
)
from apps.common.errors import ApiError
from apps.sites.tests.base import make_account, make_site

State = Subscription.State
Settlement = BillingCycle.Settlement
RUN_LATE = days(1)  # the code runs a day after the rule's time


def snapshot():
    """Every billing row, to show that a call changed nothing."""
    return (
        list(Subscription.objects.order_by("pk").values()),
        list(SubscriptionTransition.objects.order_by("pk").values()),
        list(BillingCycle.objects.order_by("pk").values()),
    )


def codes(subscription):
    return list(subscription.transitions.order_by("pk").values_list("code", flat=True))


def cycles(subscription):
    return list(
        subscription.cycles.order_by("sequence").values_list("sequence", "kind", "settlement")
    )


@override_settings(BILLING_ENABLED=True)
class BillingTestCase(TestCase):
    def assertRefused(self, code, call, *args, **kwargs):
        before = snapshot()
        with self.assertRaises(ApiError) as caught:
            call(*args, **kwargs)
        self.assertEqual((caught.exception.code, caught.exception.status), (code, 409))
        self.assertEqual(snapshot(), before)


class TrialStartTests(BillingTestCase):
    def test_site_creation_starts_the_trial_from_the_accounts_creation(self):
        subscription = make_subscription(now=T0 + days(2))

        self.assertEqual(subscription.state, State.TRIAL_ACTIVE)
        self.assertEqual((subscription.trial_start_at, subscription.trial_end_at), (T0, TRIAL_END))
        self.assertEqual(cycles(subscription), [(0, "trial", Settlement.NOT_BILLABLE)])
        cycle = subscription.cycles.get()
        self.assertEqual((cycle.start_at, cycle.end_at), (T0, TRIAL_END))
        transition = subscription.transitions.get()
        self.assertEqual(
            (transition.code, transition.from_state, transition.to_state, transition.actor),
            ("trial_started", "", State.TRIAL_ACTIVE, "site_creation"),
        )
        self.assertEqual(transition.effective_at, T0)
        self.assertEqual(transition.idempotency_key, f"trial_started:{subscription.pk}")

    def test_starting_twice_keeps_the_one_subscription(self):
        subscription = make_subscription()
        before = snapshot()

        again = services.start_subscription(subscription.site, subscription.site.account, now=T0)

        self.assertEqual(again.pk, subscription.pk)
        self.assertEqual(snapshot(), before)

    def test_with_the_switch_off_nothing_is_written(self):
        site = make_site(make_account())

        with override_settings(BILLING_ENABLED=False):
            self.assertIsNone(services.start_subscription(site, site.account, now=T0))
        self.assertFalse(Subscription.objects.exists())

    def test_a_site_created_after_the_trial_ended_joins_ended_at_day_14(self):
        subscription = make_subscription(now=T0 + days(50))

        self.assertEqual(subscription.state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(codes(subscription), ["trial_started", "trial_ended_no_method"])
        self.assertEqual(subscription.service_ended_at, TRIAL_END)  # not the creation time
        ended = subscription.transitions.get(code="trial_ended_no_method")
        self.assertEqual((ended.effective_at, ended.actor), (TRIAL_END, "site_creation"))
        self.assertIsNone(subscription.download_until)
        self.assertIsNone(subscription.deletion_due_at)

    def test_once_collection_is_live_its_dates_count_from_day_14(self):
        with collection_is_live():
            subscription = make_subscription(now=T0 + days(50))

        self.assertEqual(subscription.service_ended_at, TRIAL_END)
        self.assertEqual(subscription.deletion_due_at, T0 + days(44))
        self.assertEqual(subscription.download_until, T0 + days(44))


class TrialEndTests(BillingTestCase):
    def test_no_method_at_day_14_ends_the_trial_with_no_charge(self):
        subscription = make_subscription()

        services.sweep(now=TRIAL_END + RUN_LATE)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(subscription.method_at_trial_end, "no")
        self.assertEqual(subscription.service_ended_at, TRIAL_END)
        transition = subscription.transitions.get(code="trial_ended_no_method")
        self.assertEqual(transition.effective_at, TRIAL_END)  # the rule's time, not the run time
        self.assertEqual(transition.idempotency_key, f"trial_ended:{subscription.pk}")
        self.assertEqual(cycles(subscription), [(0, "trial", Settlement.NOT_BILLABLE)])

    def test_before_collection_is_live_a_never_paid_subscription_has_no_dates(self):
        subscription = make_subscription()

        services.sweep(now=TRIAL_END)

        subscription = fresh(subscription)
        self.assertIsNone(subscription.download_until)
        self.assertIsNone(subscription.deletion_due_at)

    def test_with_collection_live_the_last_date_to_pay_is_day_44(self):
        subscription = make_subscription()

        with collection_is_live():
            services.sweep(now=TRIAL_END)

        subscription = fresh(subscription)
        self.assertEqual(subscription.deletion_due_at, T0 + days(44))
        self.assertEqual(subscription.deletion_due_at, manila(2026, 12, 25, 10, 0))
        self.assertEqual(subscription.download_until, TRIAL_END + days(30))

    def test_the_answer_at_trial_end_is_frozen(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)

        with method_on_file():  # a later or backdated method changes nothing
            position = services.position(subscription.site, at=TRIAL_END + days(1))
            self.assertEqual(position.state, State.CANCELLED_NO_PAYMENT_METHOD)
            subscription = fresh(subscription)
            with self.assertRaises(RuntimeError):
                services._freeze_method(subscription, True)
        self.assertEqual(fresh(subscription).method_at_trial_end, "no")

    def test_a_method_on_file_is_frozen_as_yes_and_the_trial_waits_for_the_outcome(self):
        subscription = make_subscription()

        with method_on_file():
            services.sweep(now=TRIAL_END + days(1))

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.TRIAL_ACTIVE)
        self.assertEqual(subscription.method_at_trial_end, "yes")
        services.sweep(now=TRIAL_END + days(2))  # the method is gone; the answer stays yes
        self.assertEqual(fresh(subscription).state, State.TRIAL_ACTIVE)


class FirstPaymentTests(BillingTestCase):
    def test_success_opens_cycle_1_from_the_trial_end(self):
        subscription = make_subscription()

        with method_on_file():
            transition = services.first_payment_succeeded(
                subscription, paid_at=TRIAL_END + days(1), reference="pay-1"
            )

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.ACTIVE)
        self.assertEqual(subscription.anchor_day, 25)
        self.assertEqual(
            (transition.code, transition.effective_at, transition.idempotency_key),
            ("first_payment_succeeded", TRIAL_END, "paid:pay-1"),
        )
        self.assertEqual(transition.detail, {"cycle": 1, "reference": "pay-1"})
        cycle = subscription.cycles.get(sequence=1)
        self.assertEqual((cycle.start_at, cycle.end_at), (TRIAL_END, CYCLE_1_END))
        self.assertEqual(
            (cycle.kind, cycle.settlement, cycle.paid_at, cycle.payment_reference),
            ("paid", Settlement.PAID, TRIAL_END + days(1), "pay-1"),
        )

    def test_a_replay_changes_nothing_and_returns_the_first_result(self):
        subscription = make_subscription()
        with method_on_file():
            first = services.first_payment_succeeded(
                subscription, paid_at=TRIAL_END, reference="pay-1"
            )
            before = snapshot()

            again = services.first_payment_succeeded(
                subscription, paid_at=TRIAL_END, reference="pay-1"
            )

        self.assertEqual(again.pk, first.pk)
        self.assertEqual(snapshot(), before)

    def test_it_is_refused_before_the_trial_ends(self):
        subscription = make_subscription()

        with method_on_file():
            self.assertRefused(
                "not_due", services.first_payment_succeeded,
                subscription, paid_at=TRIAL_END - SECOND, reference="pay-1",
            )

    def test_it_is_refused_when_no_method_was_on_file_at_trial_end(self):
        subscription = make_subscription()

        with self.assertRaises(ApiError) as caught:
            services.first_payment_succeeded(subscription, paid_at=TRIAL_END, reference="pay-1")

        self.assertEqual(caught.exception.code, "invalid_transition")
        self.assertEqual(fresh(subscription).state, State.CANCELLED_NO_PAYMENT_METHOD)

    def test_failure_moves_to_attention_with_cycle_1_pending(self):
        subscription = make_subscription()

        with method_on_file():
            transition = services.first_payment_failed(subscription, at=TRIAL_END + days(1))

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.PAYMENT_REQUIRES_ATTENTION)
        self.assertEqual(subscription.attention_started_at, TRIAL_END)
        self.assertEqual(
            (transition.effective_at, transition.idempotency_key),
            (TRIAL_END, f"failed:{subscription.pk}:1"),
        )
        self.assertEqual(cycles(subscription)[1], (1, "paid", Settlement.PENDING))

    def test_no_outcome_for_7_days_suspends_and_marks_cycle_1_lapsed(self):
        subscription = make_subscription()

        with method_on_file(), collection_is_live():
            services.sweep(now=TRIAL_END + days(7) + RUN_LATE)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.SUSPENDED)
        transition = subscription.transitions.get(code="suspended_no_outcome")
        self.assertEqual(transition.effective_at, TRIAL_END + days(7))
        self.assertEqual(transition.idempotency_key, f"suspended:{subscription.pk}:1")
        self.assertEqual(cycles(subscription)[1], (1, "paid", Settlement.LAPSED))
        self.assertEqual(subscription.service_ended_at, TRIAL_END + days(7))
        self.assertEqual(subscription.deletion_due_at, TRIAL_END + days(37))  # never paid: 30


class RenewalTests(BillingTestCase):
    def test_renewal_opens_the_next_cycle_from_the_end_of_the_last(self):
        subscription = make_active()

        transition = services.renewed(
            subscription, paid_at=CYCLE_1_END + days(1), reference="pay-2"
        )

        self.assertEqual(fresh(subscription).state, State.ACTIVE)
        self.assertEqual(
            (transition.code, transition.from_state, transition.effective_at),
            ("renewed", State.ACTIVE, CYCLE_1_END),
        )
        cycle = subscription.cycles.get(sequence=2)
        self.assertEqual((cycle.start_at, cycle.end_at), (CYCLE_1_END, CYCLE_2_END))
        self.assertEqual(cycle.settlement, Settlement.PAID)

    def test_renewal_is_refused_before_the_cycle_ends(self):
        subscription = make_active()

        self.assertRefused(
            "not_due", services.renewed,
            subscription, paid_at=CYCLE_1_END - SECOND, reference="pay-2",
        )

    def test_a_renewal_replay_changes_nothing(self):
        subscription = make_active()
        first = services.renewed(subscription, paid_at=CYCLE_1_END, reference="pay-2")
        before = snapshot()

        again = services.renewed(subscription, paid_at=CYCLE_1_END, reference="pay-2")

        self.assertEqual(again.pk, first.pk)
        self.assertEqual(snapshot(), before)

    def test_one_reference_cannot_pay_for_two_subscriptions(self):
        first = make_active()
        other = make_subscription(email="ben@example.test", name="bens-store")

        with method_on_file():
            self.assertRefused(
                "reference_reused", services.first_payment_succeeded,
                other, paid_at=TRIAL_END, reference=f"first-{first.pk}",
            )

    def test_a_failed_renewal_moves_to_attention_with_the_next_cycle_pending(self):
        subscription = make_active()

        transition = services.renewal_failed(subscription, at=CYCLE_1_END + days(1))

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.PAYMENT_REQUIRES_ATTENTION)
        self.assertEqual(subscription.attention_started_at, CYCLE_1_END)
        self.assertEqual(
            (transition.effective_at, transition.idempotency_key),
            (CYCLE_1_END, f"failed:{subscription.pk}:2"),
        )
        self.assertEqual(cycles(subscription)[2], (2, "paid", Settlement.PENDING))

    def test_a_failed_renewal_replay_changes_nothing(self):
        subscription = make_active()
        first = services.renewal_failed(subscription, at=CYCLE_1_END)
        before = snapshot()

        again = services.renewal_failed(subscription, at=CYCLE_1_END + days(1))

        self.assertEqual(again.pk, first.pk)
        self.assertEqual(snapshot(), before)

    def test_recovery_pays_the_pending_cycle_from_its_original_start(self):
        subscription = make_attention()
        paid_at = CYCLE_1_END + days(3)

        transition = services.recovered(subscription, paid_at=paid_at, reference="pay-2")

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.ACTIVE)
        self.assertIsNone(subscription.attention_started_at)
        self.assertEqual((transition.code, transition.effective_at), ("recovered", paid_at))
        cycle = subscription.cycles.get(sequence=2)
        self.assertEqual((cycle.start_at, cycle.end_at), (CYCLE_1_END, CYCLE_2_END))
        self.assertEqual((cycle.settlement, cycle.paid_at), (Settlement.PAID, paid_at))

    def test_a_recovery_is_judged_by_its_outcome_time_not_by_when_it_is_recorded(self):
        subscription = make_attention()  # recorded late; the sweep has not run

        services.recovered(
            subscription, paid_at=CYCLE_1_END + days(7) - SECOND, reference="pay-2"
        )

        self.assertEqual(fresh(subscription).state, State.ACTIVE)

    def test_a_recovery_at_the_due_date_plus_7_days_is_refused_and_the_site_is_suspended(self):
        subscription = make_attention()

        with self.assertRaises(ApiError) as caught:
            services.recovered(subscription, paid_at=CYCLE_1_END + days(7), reference="pay-2")

        self.assertEqual(caught.exception.code, "invalid_transition")
        self.assertEqual(fresh(subscription).state, State.SUSPENDED)


class SuspensionTests(BillingTestCase):
    def test_attention_past_7_days_suspends_and_records_the_dates(self):
        subscription = make_attention()
        suspended_at = CYCLE_1_END + days(7)

        services.sweep(now=suspended_at + RUN_LATE)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.SUSPENDED)
        transition = subscription.transitions.get(code="suspended_after_attention")
        self.assertEqual(transition.effective_at, suspended_at)
        self.assertEqual(transition.idempotency_key, f"suspended:{subscription.pk}:2")
        self.assertEqual(cycles(subscription)[2], (2, "paid", Settlement.LAPSED))
        self.assertEqual(subscription.service_ended_at, suspended_at)
        self.assertEqual(subscription.download_until, suspended_at + days(30))
        self.assertEqual(subscription.deletion_due_at, suspended_at + days(90))  # it paid once

    def test_a_paid_cycle_with_no_outcome_for_7_days_suspends(self):
        subscription = make_active()
        suspended_at = CYCLE_1_END + days(7)

        services.sweep(now=suspended_at + RUN_LATE)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.SUSPENDED)
        transition = subscription.transitions.get(code="suspended_no_outcome")
        self.assertEqual((transition.effective_at, transition.from_state), (suspended_at, "active"))
        cycle = subscription.cycles.get(sequence=2)  # the due cycle is opened, then lapsed
        self.assertEqual((cycle.start_at, cycle.end_at), (CYCLE_1_END, CYCLE_2_END))
        self.assertEqual(cycle.settlement, Settlement.LAPSED)
        self.assertEqual(subscription.deletion_due_at, suspended_at + days(90))

    def test_a_never_paid_suspension_has_no_dates_before_collection_is_live(self):
        subscription = make_subscription()
        with method_on_file():
            services.first_payment_failed(subscription, at=TRIAL_END)

        services.sweep(now=TRIAL_END + days(7))

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.SUSPENDED)
        self.assertEqual(subscription.service_ended_at, TRIAL_END + days(7))
        self.assertIsNone(subscription.download_until)
        self.assertIsNone(subscription.deletion_due_at)


class CancelTests(BillingTestCase):
    def test_a_trial_cancel_ends_service_at_once_with_no_charge(self):
        subscription = make_subscription()
        now = T0 + days(3)

        transition = services.cancel(subscription, now=now)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.CANCELLED)
        self.assertEqual((subscription.cancel_requested_at, subscription.service_ended_at), (now, now))
        self.assertEqual(
            (transition.code, transition.effective_at, transition.actor, transition.idempotency_key),
            ("cancelled", now, "owner", f"cancelled:{subscription.pk}:1"),
        )
        self.assertEqual(cycles(subscription), [(0, "trial", Settlement.NOT_BILLABLE)])
        self.assertFalse(services.position(subscription.site, at=now).storefront_served)
        self.assertIsNone(subscription.deletion_due_at)  # collection is not live

    def test_a_trial_cancel_with_collection_live_starts_the_30_day_clocks(self):
        subscription = make_subscription()
        now = T0 + days(3)

        with collection_is_live():
            services.cancel(subscription, now=now)

        subscription = fresh(subscription)
        self.assertEqual(subscription.download_until, now + days(30))
        self.assertEqual(subscription.deletion_due_at, now + days(30))

    def test_a_paid_cycle_cancel_keeps_service_to_the_end_of_the_period(self):
        subscription = make_active()
        now = TRIAL_END + days(3)

        services.cancel(subscription, now=now)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.CANCELLED)
        self.assertEqual(subscription.cancel_requested_at, now)
        self.assertEqual(subscription.service_ended_at, CYCLE_1_END)
        self.assertEqual(subscription.download_until, CYCLE_1_END + days(30))
        self.assertEqual(subscription.deletion_due_at, CYCLE_1_END + days(90))
        self.assertTrue(
            services.position(subscription.site, at=CYCLE_1_END - SECOND).storefront_served
        )
        self.assertFalse(services.position(subscription.site, at=CYCLE_1_END).storefront_served)
        services.sweep(now=CYCLE_1_END + days(30))  # nothing renews or suspends it afterwards
        self.assertEqual(codes(subscription)[-1], "cancelled")

    def test_a_cancel_during_attention_after_a_paid_cycle_is_immediate(self):
        subscription = make_attention()
        now = CYCLE_1_END + days(2)

        services.cancel(subscription, now=now)

        subscription = fresh(subscription)
        self.assertEqual(subscription.service_ended_at, now)
        self.assertEqual(subscription.deletion_due_at, now + days(90))
        self.assertEqual(cycles(subscription)[2], (2, "paid", Settlement.LAPSED))  # not due

    def test_a_cancel_during_first_payment_recovery_is_immediate_with_the_30_day_clock(self):
        subscription = make_subscription()
        with method_on_file():
            services.first_payment_failed(subscription, at=TRIAL_END)
        now = TRIAL_END + days(2)

        with collection_is_live():
            services.cancel(subscription, now=now)

        subscription = fresh(subscription)
        self.assertEqual(subscription.service_ended_at, now)
        self.assertEqual(subscription.deletion_due_at, now + days(30))
        self.assertEqual(cycles(subscription)[1], (1, "paid", Settlement.LAPSED))

    def test_a_cancel_after_the_renewal_time_with_no_outcome_is_immediate(self):
        subscription = make_active()
        now = CYCLE_1_END + days(1)

        services.cancel(subscription, now=now)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.CANCELLED)
        self.assertEqual(subscription.service_ended_at, now)
        self.assertEqual(subscription.deletion_due_at, now + days(90))
        self.assertEqual(len(cycles(subscription)), 2)  # no cycle is opened for the fee not due

    def test_a_cancel_while_suspended_keeps_the_dates_the_suspension_recorded(self):
        subscription = make_suspended()
        suspended_at = CYCLE_1_END + days(7)
        now = suspended_at + days(85)

        services.cancel(subscription, now=now)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.CANCELLED)
        self.assertEqual(subscription.cancel_requested_at, now)
        self.assertEqual(subscription.service_ended_at, suspended_at)
        self.assertEqual(subscription.download_until, suspended_at + days(30))
        self.assertEqual(subscription.deletion_due_at, suspended_at + days(90))

    def test_a_second_cancel_is_refused(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))

        self.assertRefused("already_cancelled", services.cancel, subscription, now=TRIAL_END + days(4))

    def test_a_cancel_on_day_15_of_an_unswept_trial_catches_up_and_is_refused(self):
        subscription = make_subscription()

        with self.assertRaises(ApiError) as caught:
            services.cancel(subscription, now=TRIAL_END + days(1))

        self.assertEqual(caught.exception.code, "already_cancelled")
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(codes(subscription), ["trial_started", "trial_ended_no_method"])

    def test_an_operator_cancel_records_the_operator(self):
        subscription = make_subscription()

        transition = services.cancel(subscription, now=T0 + days(1), actor="operator")

        self.assertEqual(transition.actor, "operator")


class WithdrawalTests(BillingTestCase):
    def test_a_scheduled_cancellation_can_be_withdrawn_inside_the_paid_period(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))
        now = CYCLE_1_END - SECOND

        transition = services.withdraw_cancellation(subscription, now=now)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.ACTIVE)
        self.assertEqual(
            (transition.code, transition.effective_at, transition.idempotency_key),
            ("cancellation_withdrawn", now, f"withdrawn:{subscription.pk}:1"),
        )
        for field in ("cancel_requested_at", "service_ended_at", "download_until", "deletion_due_at"):
            self.assertIsNone(getattr(subscription, field), field)
        services.renewed(subscription, paid_at=CYCLE_1_END, reference="pay-2")  # it renews again
        services.cancel(subscription, now=CYCLE_1_END + days(1))  # and can cancel a second time
        self.assertEqual(
            subscription.transitions.filter(code="cancelled").last().idempotency_key,
            f"cancelled:{subscription.pk}:2",
        )

    def test_after_the_service_end_it_needs_a_payment(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))

        self.assertRefused(
            "payment_required", services.withdraw_cancellation, subscription, now=CYCLE_1_END
        )

    def test_every_other_state_needs_a_payment(self):
        trial = make_subscription()
        self.assertRefused(
            "payment_required", services.withdraw_cancellation, trial, now=T0 + days(1)
        )
        services.cancel(trial, now=T0 + days(2))
        self.assertRefused(
            "payment_required", services.withdraw_cancellation, trial, now=T0 + days(3)
        )
        suspended = make_suspended(email="ben@example.test", name="bens-store")
        self.assertRefused(
            "payment_required", services.withdraw_cancellation, suspended, now=CYCLE_1_END + days(9)
        )


class ReactivationTests(BillingTestCase):
    def test_a_payment_after_a_no_method_trial_starts_a_new_paid_cycle_at_its_time(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)
        paid_at = manila(2026, 12, 3, 9, 30)

        transition = services.reactivated(subscription, paid_at=paid_at, reference="pay-9")

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.ACTIVE)
        self.assertEqual(subscription.anchor_day, 3)
        self.assertEqual((transition.code, transition.effective_at), ("reactivated", paid_at))
        self.assertEqual(
            cycles(subscription),
            [(0, "trial", Settlement.NOT_BILLABLE), (1, "paid", Settlement.PAID)],
        )
        cycle = subscription.cycles.get(sequence=1)
        self.assertEqual((cycle.start_at, cycle.end_at), (paid_at, manila(2027, 1, 3, 15, 0)))
        self.assertIsNone(subscription.service_ended_at)
        self.assertEqual(subscription.method_at_trial_end, "no")  # still frozen

    def test_a_never_paid_site_is_refused_from_day_44_once_collection_is_live(self):
        subscription = make_subscription()
        with collection_is_live():
            services.sweep(now=TRIAL_END)

        self.assertRefused(
            "past_deletion_date", services.reactivated,
            subscription, paid_at=T0 + days(44), reference="pay-9",
        )
        services.reactivated(subscription, paid_at=T0 + days(44) - SECOND, reference="pay-9")
        self.assertEqual(fresh(subscription).state, State.ACTIVE)

    def test_with_no_dates_a_payment_is_never_refused_by_date(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)

        services.reactivated(subscription, paid_at=T0 + days(400), reference="pay-9")

        self.assertEqual(fresh(subscription).state, State.ACTIVE)

    def test_a_cancelled_paid_site_is_refused_from_service_end_plus_90_days(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))

        self.assertRefused(
            "past_deletion_date", services.reactivated,
            subscription, paid_at=CYCLE_1_END + days(90), reference="pay-9",
        )
        services.reactivated(
            subscription, paid_at=CYCLE_1_END + days(90) - SECOND, reference="pay-9"
        )
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.ACTIVE)
        for field in ("cancel_requested_at", "service_ended_at", "download_until", "deletion_due_at"):
            self.assertIsNone(getattr(subscription, field), field)

    def test_a_suspended_paid_site_is_refused_from_the_suspension_plus_90_days(self):
        subscription = make_suspended()
        suspended_at = CYCLE_1_END + days(7)

        self.assertRefused(
            "past_deletion_date", services.reactivated,
            subscription, paid_at=suspended_at + days(90), reference="pay-9",
        )
        paid_at = suspended_at + days(90) - SECOND
        services.reactivated(subscription, paid_at=paid_at, reference="pay-9")

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.ACTIVE)
        self.assertEqual(subscription.anchor_day, paid_at.astimezone(manila(2027, 1, 1).tzinfo).day)
        self.assertEqual(
            cycles(subscription)[2:], [(2, "paid", Settlement.LAPSED), (3, "paid", Settlement.PAID)]
        )

    def test_a_cancellation_still_inside_its_paid_period_is_not_a_reactivation(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))

        self.assertRefused(
            "invalid_transition", services.reactivated,
            subscription, paid_at=CYCLE_1_END - SECOND, reference="pay-9",
        )

    def test_a_running_subscription_cannot_be_reactivated(self):
        subscription = make_active()

        self.assertRefused(
            "invalid_transition", services.reactivated,
            subscription, paid_at=TRIAL_END + days(1), reference="pay-9",
        )


class ExemptTests(BillingTestCase):
    def test_nothing_changes_an_exempt_subscription(self):
        subscription = make_subscription()
        Subscription.objects.filter(pk=subscription.pk).update(billing_exempt=True)
        late = TRIAL_END + days(30)

        self.assertEqual(services.sweep(now=late), {})
        self.assertEqual(fresh(subscription).state, State.TRIAL_ACTIVE)
        self.assertTrue(services.position(subscription.site, at=late).storefront_served)
        self.assertRefused("billing_exempt", services.cancel, subscription, now=late)
        self.assertRefused("billing_exempt", services.withdraw_cancellation, subscription, now=late)
        with method_on_file():
            for call in (services.first_payment_succeeded, services.renewed, services.recovered,
                         services.reactivated):
                self.assertRefused("billing_exempt", call, subscription, paid_at=late, reference="p")
            for call in (services.first_payment_failed, services.renewal_failed):
                self.assertRefused("billing_exempt", call, subscription, at=late)


class WrongStateTests(BillingTestCase):
    def test_each_payment_function_refuses_every_state_but_its_own(self):
        cases = [
            (State.TRIAL_ACTIVE, make_subscription, T0 + days(1)),
            (State.ACTIVE, make_active, CYCLE_1_END),
            (State.PAYMENT_REQUIRES_ATTENTION, make_attention, CYCLE_1_END + days(1)),
            (State.SUSPENDED, make_suspended, CYCLE_1_END + days(8)),
        ]
        own_state = {
            services.first_payment_succeeded: State.TRIAL_ACTIVE,
            services.first_payment_failed: State.TRIAL_ACTIVE,
            services.renewed: State.ACTIVE,
            services.renewal_failed: State.ACTIVE,
            services.recovered: State.PAYMENT_REQUIRES_ATTENTION,
            services.reactivated: State.SUSPENDED,
        }
        failures = (services.first_payment_failed, services.renewal_failed)
        for index, (state, make, at) in enumerate(cases):
            subscription = make(email=f"u{index}@example.test", name=f"store-{index}")
            self.assertEqual(subscription.state, state)
            for call, own in own_state.items():
                if own == state:
                    continue
                if call is services.renewal_failed and state == State.PAYMENT_REQUIRES_ATTENTION:
                    continue  # that is a replay of the failure already recorded
                kwargs = (
                    {"at": at} if call in failures
                    else {"paid_at": at, "reference": f"ref-{index}"}
                )
                with self.subTest(state=state, call=call.__name__):
                    self.assertRefused("invalid_transition", call, subscription, **kwargs)
