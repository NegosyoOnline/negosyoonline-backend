"""Simultaneous writes. These need real row locks, so they run on PostgreSQL only (CI's
"PostgreSQL and Redis" job), as apps/sites/tests/test_concurrency.py does."""
import threading
from unittest import skipUnless

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase, override_settings

from apps.accounts import retention as rules
from apps.accounts.models import Account
from apps.billing import services
from apps.billing.models import BankTransferPayment, BillingNotice, Subscription, SubscriptionTransition
from apps.billing.tests.base import (
    CYCLE_1_END, PNG, SECOND, T0, TRIAL_END, days, fresh, make_active, make_attention,
    make_operator, make_subscription, submit, transfers_on,
)
from apps.common.errors import ApiError
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Site
from apps.sites.tests.base import make_account
from apps.sites.tests.test_concurrency import create as create_site
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

    def test_two_site_creations_at_once_make_one_site_and_one_subscription(self):
        account = make_account()

        results = run_together(
            create_site(account.pk, "store-one"), create_site(account.pk, "store-two")
        )

        sites = [result for result in results if isinstance(result, Site)]
        self.assertEqual(len(sites), 1, results)
        self.assertEqual(Site.objects.count(), 1)
        subscription = Subscription.objects.get()
        self.assertEqual(subscription.site, sites[0])
        self.assertEqual(codes(subscription), ["trial_started"])
        self.assertEqual(subscription.cycles.count(), 1)

    @override_settings(RETENTION_ENABLED=True)
    def test_a_disposal_racing_a_cancel_writes_one_cancellation_and_leaves_none_running(self):
        for attempt in range(5):
            subscription = make_subscription(f"u{attempt}@example.test", f"store-{attempt}")
            account_pk = subscription.site.account_id
            at = T0 + days(1)

            results = run_together(
                lambda: rules.dispose_account(
                    Account.objects.get(pk=account_pk), now=at, rule="request",
                    request_ref="DPO-1",
                ),
                lambda: services.cancel(subscription, now=at),
            )

            self.assertIsInstance(results[0], dict, results)
            self.assertTrue(rules.is_disposed(Account.objects.get(pk=account_pk)))
            subscription = fresh(subscription)
            # Never a blanked account with a running subscription, whichever went first.
            self.assertEqual(
                (subscription.state, subscription.service_ended_at), (State.CANCELLED, at)
            )
            self.assertEqual(subscription.transitions.filter(code="cancelled").count(), 1)
            if isinstance(results[1], ApiError):  # the disposal cancelled it first
                self.assertEqual(results[1].code, "already_cancelled")
                self.assertEqual(results[0]["subscriptions_cancelled"], 1)
            else:  # the owner's cancel went first; the disposal found it ended
                self.assertIsInstance(results[1], SubscriptionTransition, results)
                self.assertNotIn("subscriptions_cancelled", results[0])

    def test_the_database_refuses_a_second_row_with_the_same_key(self):
        subscription = make_subscription()

        with self.assertRaises(IntegrityError):
            SubscriptionTransition.objects.create(
                subscription=subscription, code="trial_started", to_state=State.TRIAL_ACTIVE,
                actor="sweep", idempotency_key=f"trial_started:{subscription.pk}", effective_at=T0,
            )


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(BILLING_ENABLED=True)
class TransferConcurrencyTests(TransactionTestCase):
    """Bank transfer (Billing Build B2a): one payment settles one cycle, whatever races it."""

    def setUp(self):
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)

    def test_two_notice_scans_at_once_send_each_email_once(self):
        from django.core import mail

        from apps.billing import notices

        submit(make_subscription(), T0 + days(3))
        at = T0 + days(3) + days(1) / 24

        results = run_together(*[lambda: notices.run(at) for _ in range(2)])

        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(BillingNotice.objects.count(), 1)

    def test_two_confirmations_at_once_settle_one_cycle(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3))
        operators = [make_operator(f"op{index}") for index in range(3)]
        at = T0 + days(5)

        results = run_together(*[
            lambda operator=operator: services.confirm_transfer(
                payment, operator, amount_received_centavos=payment.amount_centavos, now=at
            )
            for operator in operators
        ])

        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual({r.decided_by_id for r in results}, {results[0].decided_by_id})
        self.assertEqual(subscription.cycles.filter(settlement="paid").count(), 1)
        self.assertEqual(subscription.cycles.count(), 2)
        self.assertEqual(codes(subscription), ["first_payment_succeeded", "trial_started"])
        self.assertEqual(payment.events.filter(code="confirmed").count(), 1)
        self.assertEqual(
            OperatorAuditEntry.objects.filter(action="billing_payment_confirmed").count(), 1
        )

    def test_a_confirmation_racing_a_replacing_proof_matches_one_of_the_two_orders(self):
        for attempt in range(5):
            subscription = make_subscription(f"u{attempt}@example.test", f"store-{attempt}")
            payment = submit(subscription, T0 + days(3))
            operator = make_operator(f"op{attempt}")
            at = T0 + days(5)

            results = run_together(
                lambda: services.confirm_transfer(
                    payment, operator, amount_received_centavos=payment.amount_centavos, now=at
                ),
                lambda: submit(subscription, at, content=PNG),
            )

            payment.refresh_from_db()
            paid = subscription.cycles.filter(settlement="paid").count()
            if isinstance(results[0], BankTransferPayment):
                # confirmed first, so the next period is paid and a new proof is refused
                self.assertEqual((payment.status, paid), ("confirmed", 1))
                self.assertEqual(results[1].code, "paid_ahead")
                self.assertEqual(subscription.transfers.count(), 1)
            else:
                # replaced first, so the old proof has nothing to confirm
                self.assertEqual(results[0].code, "not_under_review")
                self.assertEqual((payment.status, paid), ("replaced", 0))
                self.assertEqual(results[1].status, "submitted")
            self.assertLessEqual(subscription.transfers.filter(status="submitted").count(), 1)
