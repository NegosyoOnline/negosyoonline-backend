"""Simultaneous writes. These need real row locks, so they run on PostgreSQL only (CI's
"PostgreSQL and Redis" job), as apps/sites/tests/test_concurrency.py does."""
import threading
from unittest import skipUnless

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase, override_settings

from apps.billing import services
from apps.billing.models import Subscription, SubscriptionTransition
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, T0, TRIAL_END, days, fresh, make_active, make_attention, make_subscription,
)
from apps.common.errors import ApiError
from apps.sites.tests.test_concurrency import run_together

State = Subscription.State


def codes(subscription):
    return sorted(subscription.transitions.values_list("code", flat=True))


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(BILLING_ENABLED=True)
class ConcurrencyTests(TransactionTestCase):
    def test_two_sweeps_at_once_write_every_transition_exactly_once(self):
        subscriptions = [
            make_subscription(f"u{index}@example.test", f"store-{index}") for index in range(6)
        ]

        results = run_together(*[lambda: services.sweep(now=TRIAL_END) for _ in range(2)])

        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual(sum(r.get("trial_ended_no_method", 0) for r in results), 6)
        for subscription in subscriptions:
            self.assertEqual(codes(subscription), ["trial_ended_no_method", "trial_started"])

    def test_a_sweep_skips_a_subscription_another_writer_holds_and_finishes(self):
        held = make_subscription()
        free = make_subscription("ben@example.test", "bens-store")
        locked, release = threading.Event(), threading.Event()

        def holder():
            try:
                with transaction.atomic():
                    Subscription.objects.select_for_update().get(pk=held.pk)
                    locked.set()
                    release.wait(timeout=20)
            finally:
                connection.close()

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            self.assertTrue(locked.wait(timeout=10))
            counts = services.sweep(now=TRIAL_END)
        finally:
            release.set()
            thread.join(timeout=30)

        self.assertEqual(counts, {"trial_ended_no_method": 1})
        self.assertEqual(fresh(held).state, State.TRIAL_ACTIVE)
        self.assertEqual(fresh(free).state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(services.sweep(now=TRIAL_END), {"trial_ended_no_method": 1})

    def test_a_cancel_racing_the_sweep_at_trial_end_gives_one_of_the_two_orders(self):
        for attempt in range(5):
            subscription = make_subscription(f"u{attempt}@example.test", f"store-{attempt}")

            run_together(
                lambda: services.sweep(now=TRIAL_END),
                lambda: services.cancel(subscription, now=TRIAL_END - SECOND),
            )

            subscription = fresh(subscription)
            if subscription.state == State.CANCELLED:  # the cancel took the row first
                self.assertEqual(codes(subscription), ["cancelled", "trial_started"])
                self.assertEqual(subscription.service_ended_at, TRIAL_END - SECOND)
            else:  # the sweep did; the cancel was then refused
                self.assertEqual(subscription.state, State.CANCELLED_NO_PAYMENT_METHOD)
                self.assertEqual(codes(subscription), ["trial_ended_no_method", "trial_started"])
                self.assertEqual(subscription.service_ended_at, TRIAL_END)

    def test_two_cancels_at_once_write_one_transition(self):
        subscription = make_subscription()

        results = run_together(*[
            lambda: services.cancel(subscription, now=T0 + days(1)) for _ in range(2)
        ])

        done = [r for r in results if isinstance(r, SubscriptionTransition)]
        refused = [r for r in results if isinstance(r, ApiError)]
        self.assertEqual((len(done), len(refused)), (1, 1), results)
        self.assertEqual(refused[0].code, "already_cancelled")
        self.assertEqual(subscription.transitions.filter(code="cancelled").count(), 1)

    def test_a_payment_racing_a_cancel_matches_one_of_the_two_orders(self):
        for attempt in range(5):
            subscription = make_attention(
                email=f"u{attempt}@example.test", name=f"store-{attempt}"
            )
            at = CYCLE_1_END + days(1)

            results = run_together(
                lambda: services.recovered(subscription, paid_at=at, reference=f"pay-r{attempt}"),
                lambda: services.cancel(subscription, now=at),
            )

            self.assertIsInstance(results[1], SubscriptionTransition, results)
            subscription = fresh(subscription)
            self.assertEqual(subscription.state, State.CANCELLED)
            if isinstance(results[0], SubscriptionTransition):
                # paid first, so the cancel is a paid-cycle cancel to the end of cycle 2
                self.assertGreater(subscription.service_ended_at, at)
            else:
                # cancelled first, so the payment has nothing to recover
                self.assertEqual(results[0].code, "invalid_transition")
                self.assertEqual(subscription.service_ended_at, at)

    def test_the_same_payment_recorded_twice_at_once_is_applied_once(self):
        subscription = make_active()

        results = run_together(*[
            lambda: services.renewed(subscription, paid_at=CYCLE_1_END, reference="pay-2")
            for _ in range(3)
        ])

        self.assertEqual({r.pk for r in results}, {results[0].pk}, results)
        self.assertEqual(subscription.cycles.count(), 3)

    def test_the_database_refuses_a_second_row_with_the_same_key(self):
        subscription = make_subscription()

        with self.assertRaises(IntegrityError):
            SubscriptionTransition.objects.create(
                subscription=subscription, code="trial_started", to_state=State.TRIAL_ACTIVE,
                actor="sweep", idempotency_key=f"trial_started:{subscription.pk}", effective_at=T0,
            )
