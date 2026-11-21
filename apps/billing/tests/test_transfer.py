"""Bank transfer with proof (Billing Build B2a, pull request 1): the payment record, the
operator's confirmation and rejection, and how a confirmation settles a cycle."""
from datetime import date
from unittest import mock

from django.core.checks import Error, Warning
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing import methods, plan, proofs, services
from apps.billing.models import (
    BankTransferEvent, BankTransferPayment, BillingCycle, ReceivingAccount, Subscription, SubscriptionTransition,
)
from apps.billing.tests.base import (
    CYCLE_1_END, CYCLE_2_END, JPEG, PDF, PNG, SECOND, T0, TRIAL_END, days, fresh,
    make_active, make_operator, make_receiving_account, make_subscription, make_suspended, submit, transfers_on,
)
from apps.billing.tests.base import SENDER
from apps.common.errors import ApiError
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Site

State = Subscription.State
Status = BankTransferPayment.Status
Settlement = BillingCycle.Settlement
MONTHLY = 249_900


def confirm(payment, operator, at, amount=MONTHLY):
    return services.confirm_transfer(
        payment, operator, amount_received_centavos=amount, now=at
    )


def rows(subscription):
    """Everything a rejection must leave alone."""
    subscription = fresh(subscription)
    return (
        subscription.state, subscription.service_ended_at, subscription.deletion_due_at,
        subscription.method_at_trial_end,
        list(subscription.transitions.order_by("pk").values_list("code", flat=True)),
        list(subscription.cycles.order_by("sequence").values_list("sequence", "settlement")),
    )


@override_settings(BILLING_ENABLED=True)
class TransferCase(TestCase):
    def setUp(self):
        on = transfers_on()
        self.store = on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.operator = make_operator()

    def refused(self, code, call, *args, **kwargs):
        with self.assertRaises(ApiError) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception


class SubmitTests(TransferCase):
    def test_a_proof_is_stored_and_changes_nothing_on_the_subscription(self):
        subscription = make_subscription()
        before = rows(subscription)

        payment = submit(subscription, T0 + days(3), transfer_reference="REF-1")

        self.assertEqual(payment.status, Status.SUBMITTED)
        self.assertTrue(payment.public_id.startswith("btp_"))
        self.assertEqual(payment.reference, payment.public_id)
        self.assertEqual((payment.plan_code, payment.amount_centavos, payment.currency),
                         ("monthly", MONTHLY, "PHP"))
        self.assertEqual(payment.submitted_by, subscription.site.account)
        self.assertEqual(payment.proof_content_type, "image/jpeg")
        self.assertEqual(payment.proof_size, len(JPEG))
        self.assertEqual(self.store.read(payment.proof_key), JPEG)
        self.assertNotIn("ana", payment.proof_key)
        self.assertEqual(
            list(payment.events.values_list("code", "idempotency_key", "actor")),
            [("submitted", f"bt_submitted:{payment.pk}", "owner")],
        )
        self.assertEqual(rows(subscription), before)

    def test_the_file_type_is_read_from_its_bytes(self):
        self.assertEqual(proofs.sniff(JPEG), "image/jpeg")
        self.assertEqual(proofs.sniff(PNG), "image/png")
        self.assertEqual(proofs.sniff(PDF), "application/pdf")
        self.assertIsNone(proofs.sniff(b"GIF89a" + b"0" * 50))
        self.assertIsNone(proofs.sniff(b""))

    def test_a_bad_request_names_its_fields(self):
        subscription = make_subscription()
        at = T0 + days(3)
        cases = {
            "proof": dict(content=b"<html>not an image</html>"),
            "plan": dict(plan_code="annual"),
            "transferred_on": dict(transferred_on=(at + days(2)).date()),
            "transferred_at": dict(transferred_at=at + days(2)),
            "transfer_reference": dict(transfer_reference="x" * 65),
        }
        for field, values in cases.items():
            with self.subTest(field=field):
                refusal = self.refused("invalid_request", submit, subscription, at, **values)
                self.assertEqual(list(refusal.fields), [field])
        too_large = JPEG + b"0" * proofs.MAX_BYTES
        refusal = self.refused("invalid_request", submit, subscription, at, content=too_large)
        self.assertEqual(list(refusal.fields), ["proof"])
        self.assertEqual(BankTransferPayment.objects.count(), 0)
        self.assertEqual(self.store.files, {})

    def test_a_second_proof_replaces_the_one_under_review(self):
        subscription = make_subscription()
        first = submit(subscription, T0 + days(3))

        second = submit(subscription, T0 + days(4), content=PNG)

        first.refresh_from_db()
        self.assertEqual(first.status, Status.REPLACED)
        self.assertEqual(first.decided_at, T0 + days(4))
        self.assertIsNone(first.decided_by)
        self.assertEqual(second.status, Status.SUBMITTED)
        self.assertEqual(
            sorted(BankTransferEvent.objects.values_list("code", flat=True)),
            ["replaced", "submitted", "submitted"],
        )
        self.assertEqual(subscription.transfers.filter(status=Status.SUBMITTED).count(), 1)

    def test_the_same_file_again_returns_the_same_payment(self):
        subscription = make_subscription()
        first = submit(subscription, T0 + days(3))

        again, created = services.submit_transfer(
            subscription, subscription.site.account, plan_code="monthly", content=JPEG,
            transferred_at=T0 + days(4), now=T0 + days(4), **SENDER,
        )

        self.assertFalse(created)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(BankTransferPayment.objects.count(), 1)
        self.assertEqual(BankTransferEvent.objects.count(), 1)

    def test_only_one_payment_can_be_under_review(self):
        subscription = make_subscription()
        first = submit(subscription, T0 + days(3))
        with self.assertRaises(IntegrityError), transaction.atomic():
            BankTransferPayment.objects.create(
                subscription=subscription, plan_code="monthly", amount_centavos=MONTHLY,
                transferred_on=date(2026, 11, 14), proof_key="k", proof_content_type="image/png",
                proof_size=1, proof_sha256="0" * 64, submitted_by=first.submitted_by,
                submitted_at=T0 + days(3),
            )

    def test_an_exempt_site_cannot_pay(self):
        subscription = make_subscription()
        services.set_exempt(subscription.site, operator=self.operator, reason="test site")
        self.refused("billing_exempt", submit, subscription, T0 + days(3))

    def test_a_scheduled_cancellation_is_withdrawn_before_paying(self):
        subscription = make_active()
        services.cancel(subscription, now=CYCLE_1_END - days(10))
        self.refused("cancel_scheduled", submit, subscription, CYCLE_1_END - days(9))

    def test_nothing_is_accepted_at_or_after_the_deletion_date(self):
        subscription = make_suspended()
        deletion = fresh(subscription).deletion_due_at
        submit(subscription, deletion - SECOND)
        self.refused("past_deletion_date", submit, subscription, deletion, content=PNG)

    def test_a_payment_row_is_never_deleted(self):
        payment = submit(make_subscription(), T0 + days(3))
        with self.assertRaises(RuntimeError):
            payment.delete()


class ConfirmTests(TransferCase):
    def test_paid_during_the_trial_opens_cycle_1_from_the_trial_end(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3))

        confirmed = confirm(payment, self.operator, T0 + days(5))

        subscription = fresh(subscription)
        cycle = subscription.cycles.get(sequence=1)
        self.assertEqual(subscription.state, State.ACTIVE)
        self.assertEqual((cycle.start_at, cycle.end_at), (TRIAL_END, CYCLE_1_END))
        self.assertEqual((cycle.settlement, cycle.paid_at), (Settlement.PAID, T0 + days(5)))
        self.assertEqual(cycle.payment_reference, payment.reference)
        self.assertEqual(subscription.cycles.count(), 2)
        self.assertEqual(confirmed.status, Status.CONFIRMED)
        self.assertEqual(confirmed.cycle, cycle)
        self.assertEqual(confirmed.decided_by, self.operator)
        self.assertEqual(confirmed.amount_received_centavos, MONTHLY)
        transition = subscription.transitions.get(code="first_payment_succeeded")
        self.assertEqual(transition.idempotency_key, f"paid:{payment.reference}")
        self.assertEqual(transition.detail, {
            "cycle": 1, "reference": payment.reference, "plan": "monthly", "version": 1,
            "amount_centavos": MONTHLY,
        })
        self.assertTrue(services.position(subscription.site, at=T0 + days(6)).storefront_served)
        self.assertTrue(services.position(subscription.site, at=TRIAL_END + days(20)).storefront_served)

    def test_the_confirmation_is_audited_without_personal_values(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3), transfer_reference="REF-1")

        confirm(payment, self.operator, T0 + days(5))

        entry = OperatorAuditEntry.objects.get(action="billing_payment_confirmed")
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual((entry.target_type, entry.target_id), ("site", subscription.site.public_id))
        self.assertEqual(entry.detail, {
            "payment": payment.public_id, "plan": "monthly", "amount_centavos": MONTHLY, "cycle": 1,
        })
        self.assertEqual(entry.personal, {})
        self.assertEqual(
            payment.events.get(code="confirmed").idempotency_key, f"bt_confirmed:{payment.pk}"
        )

    def test_confirming_twice_settles_once(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3))
        confirm(payment, self.operator, T0 + days(5))
        before = rows(subscription)

        again = confirm(payment, make_operator("op2"), T0 + days(6))

        self.assertEqual(again.decided_by, self.operator)
        self.assertEqual(rows(subscription), before)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_payment_confirmed").count(), 1)
        self.assertEqual(payment.events.filter(code="confirmed").count(), 1)

    def test_a_different_amount_is_refused_and_nothing_changes(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3))
        before = rows(subscription)

        self.refused("amount_mismatch", confirm, payment, self.operator, T0 + days(5), MONTHLY - 1)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Status.SUBMITTED)
        self.assertEqual(rows(subscription), before)
        self.assertFalse(OperatorAuditEntry.objects.filter(action__startswith="billing_payment").exists())

    def test_a_confirmed_row_with_another_amount_cannot_be_stored(self):
        subscription = make_subscription()
        payment = confirm(submit(subscription, T0 + days(3)), self.operator, T0 + days(5))
        with self.assertRaises(IntegrityError), transaction.atomic():
            BankTransferPayment.objects.filter(pk=payment.pk).update(amount_received_centavos=1)

    def test_an_operator_is_required(self):
        payment = submit(make_subscription(), T0 + days(3))
        with self.assertRaises(ValueError):
            confirm(payment, None, T0 + days(5))
        with self.assertRaises(ValueError):
            services.reject_transfer(payment, None, reason="unreadable", now=T0 + days(5))

    def test_a_proof_at_the_trial_end_holds_the_trial_for_7_days_and_no_longer(self):
        subscription = make_subscription()
        submit(subscription, TRIAL_END - days(1))

        held = services.position(subscription.site, at=TRIAL_END + days(3))
        self.assertEqual((held.state, held.storefront_served), (State.TRIAL_ACTIVE, True))
        self.assertEqual(services.sweep(now=TRIAL_END + days(3)), {})
        self.assertTrue(methods.active_at(subscription.site, TRIAL_END))

        gone = services.position(subscription.site, at=TRIAL_END + days(7))
        self.assertEqual((gone.state, gone.storefront_served), (State.SUSPENDED, False))
        self.assertEqual(services.sweep(now=TRIAL_END + days(7)), {"suspended_no_outcome": 1})

    def test_confirmed_inside_those_7_days_the_cycle_starts_at_the_trial_end(self):
        subscription = make_subscription()
        payment = submit(subscription, TRIAL_END - days(1))

        confirm(payment, self.operator, TRIAL_END + days(2))

        cycle = fresh(subscription).cycles.get(sequence=1)
        self.assertEqual((cycle.start_at, cycle.end_at, cycle.settlement),
                         (TRIAL_END, CYCLE_1_END, Settlement.PAID))
        self.assertEqual(fresh(subscription).state, State.ACTIVE)

    def test_a_proof_rejected_before_the_trial_end_does_not_hold_the_trial(self):
        subscription = make_subscription()
        payment = submit(subscription, TRIAL_END - days(2))
        services.reject_transfer(payment, self.operator, reason="unreadable", now=TRIAL_END - days(1))

        self.assertFalse(methods.active_at(subscription.site, TRIAL_END))
        position = services.position(subscription.site, at=TRIAL_END)
        self.assertEqual(position.state, State.CANCELLED_NO_PAYMENT_METHOD)

    def test_paid_after_the_trial_ended_starts_a_new_cycle_at_the_confirmation(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)
        payment = submit(subscription, TRIAL_END + days(2))
        at = TRIAL_END + days(3)

        confirm(payment, self.operator, at)

        subscription = fresh(subscription)
        cycle = subscription.cycles.get(sequence=1)
        self.assertEqual(subscription.state, State.ACTIVE)
        self.assertEqual((cycle.start_at, cycle.paid_at, cycle.settlement), (at, at, Settlement.PAID))
        self.assertEqual(subscription.anchor_day, 28)
        self.assertIsNone(subscription.deletion_due_at)
        self.assertEqual(subscription.transitions.filter(code="reactivated").count(), 1)

    def test_a_renewal_paid_early_opens_the_next_cycle_at_the_due_date(self):
        subscription = make_active()
        payment = submit(subscription, CYCLE_1_END - days(3))

        confirm(payment, self.operator, CYCLE_1_END - days(2))

        cycle = fresh(subscription).cycles.get(sequence=2)
        self.assertEqual((cycle.start_at, cycle.end_at, cycle.settlement),
                         (CYCLE_1_END, CYCLE_2_END, Settlement.PAID))
        self.assertEqual(fresh(subscription).transitions.filter(code="renewed").count(), 1)
        # Only one cycle can be paid ahead.
        self.refused("paid_ahead", submit, subscription, CYCLE_1_END - days(1), content=PNG)

    def test_a_renewal_confirmed_inside_the_7_days_continues_from_the_due_date(self):
        subscription = make_active()
        payment = submit(subscription, CYCLE_1_END - days(1))

        confirm(payment, self.operator, CYCLE_1_END + days(2))

        cycle = fresh(subscription).cycles.get(sequence=2)
        self.assertEqual((cycle.start_at, cycle.end_at), (CYCLE_1_END, CYCLE_2_END))
        self.assertEqual(fresh(subscription).state, State.ACTIVE)

    def test_a_renewal_confirmed_after_the_7_days_starts_at_the_confirmation(self):
        """A waiting proof holds a site only inside the 7 days (L32)."""
        subscription = make_active()
        payment = submit(subscription, CYCLE_1_END + days(6))
        at = CYCLE_1_END + days(8)
        self.assertFalse(services.position(subscription.site, at=CYCLE_1_END + days(7)).storefront_served)

        confirm(payment, self.operator, at)

        subscription = fresh(subscription)
        self.assertEqual(
            list(subscription.cycles.order_by("sequence").values_list("sequence", "settlement")),
            [(0, "not_billable"), (1, "paid"), (2, "lapsed"), (3, "paid")],
        )
        self.assertEqual(subscription.cycles.get(sequence=3).start_at, at)
        self.assertEqual(subscription.state, State.ACTIVE)

    def test_a_suspended_site_is_restored_by_a_confirmed_payment(self):
        subscription = make_suspended()
        payment = submit(subscription, CYCLE_1_END + days(9))

        confirm(payment, self.operator, CYCLE_1_END + days(10))

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.ACTIVE)
        self.assertEqual(subscription.cycles.filter(settlement=Settlement.PAID).count(), 2)
        self.assertEqual(subscription.cycles.filter(settlement=Settlement.LAPSED).count(), 1)

    def test_a_cancellation_scheduled_after_the_proof_refuses_the_confirmation(self):
        subscription = make_active()
        payment = submit(subscription, CYCLE_1_END - days(5))
        services.cancel(subscription, now=CYCLE_1_END - days(4))

        self.refused("cancel_scheduled", confirm, payment, self.operator, CYCLE_1_END - days(3))

        payment.refresh_from_db()
        self.assertEqual(payment.status, Status.SUBMITTED)
        self.assertEqual(fresh(subscription).cycles.count(), 2)


class RejectTests(TransferCase):
    def test_a_rejection_changes_nothing_else_and_the_proof_can_be_replaced(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3))
        before = rows(subscription)

        rejected = services.reject_transfer(
            payment, self.operator, reason="other", note="The slip shows another name.",
            now=T0 + days(4),
        )

        self.assertEqual(rejected.status, Status.REJECTED)
        self.assertEqual((rejected.reject_reason, rejected.reject_note),
                         ("other", "The slip shows another name."))
        self.assertEqual(rejected.decided_by, self.operator)
        self.assertEqual(rows(subscription), before)
        entry = OperatorAuditEntry.objects.get(action="billing_payment_rejected")
        self.assertEqual(entry.detail, {"payment": payment.public_id, "reason": "other"})
        self.assertEqual(entry.reason, "")
        self.assertEqual(entry.personal, {})
        self.assertEqual(
            payment.events.get(code="rejected").idempotency_key, f"bt_rejected:{payment.pk}"
        )

        replacement = submit(subscription, T0 + days(5), content=PNG)
        self.assertEqual(replacement.status, Status.SUBMITTED)
        confirm(replacement, self.operator, T0 + days(6))
        self.assertEqual(fresh(subscription).cycles.filter(settlement=Settlement.PAID).count(), 1)

    def test_a_decided_payment_is_not_decided_again(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3))
        services.reject_transfer(payment, self.operator, reason="unreadable", now=T0 + days(4))

        again = services.reject_transfer(payment, self.operator, reason="duplicate", now=T0 + days(5))
        self.assertEqual(again.reject_reason, "unreadable")
        self.refused("not_under_review", confirm, payment, self.operator, T0 + days(5))
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_payment_rejected").count(), 1)

        confirmed = confirm(submit(subscription, T0 + days(6), content=PNG), self.operator, T0 + days(7))
        self.refused(
            "not_under_review", services.reject_transfer, confirmed, self.operator,
            reason="duplicate", now=T0 + days(8),
        )

    def test_a_rejection_needs_a_known_reason_and_a_note_for_other(self):
        payment = submit(make_subscription(), T0 + days(3))
        for values in (dict(reason="because"), dict(reason="other"),
                       dict(reason="unreadable", note="x" * 301)):
            with self.subTest(values=values):
                self.refused(
                    "invalid_request", services.reject_transfer, payment, self.operator,
                    now=T0 + days(4), **values,
                )
        payment.refresh_from_db()
        self.assertEqual(payment.status, Status.SUBMITTED)


class OperatorProofTests(TransferCase):
    def test_an_operator_reading_a_proof_is_audited(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3), content=PDF)

        content, content_type = services.proof_for_operator(payment, self.operator)

        self.assertEqual((content, content_type), (PDF, "application/pdf"))
        entry = OperatorAuditEntry.objects.get(action="billing_proof_viewed")
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual(entry.target_id, subscription.site.public_id)
        self.assertEqual(entry.detail, {"payment": payment.public_id})
        with self.assertRaises(ValueError):
            services.proof_for_operator(payment, None)


class LifecycleTests(TransferCase):
    def test_the_label_filter_agrees_with_position_for_a_proof_at_trial_end(self):
        held = make_subscription()
        submit(held, TRIAL_END - days(1))
        ended = make_subscription("ben@example.test", "bens-store")
        at = TRIAL_END + days(2)

        def sites(*states):
            return set(Site.objects.filter(services.state_filter(at, states)).values_list("pk", flat=True))

        self.assertEqual(sites(State.TRIAL_ACTIVE), {held.site_id})
        self.assertEqual(sites(State.CANCELLED_NO_PAYMENT_METHOD), {ended.site_id})
        late = TRIAL_END + days(7)
        self.assertEqual(
            set(Site.objects.filter(services.state_filter(late, [State.SUSPENDED])).values_list("pk", flat=True)),
            {held.site_id},
        )

    def test_an_account_disposal_waits_for_a_proof_under_review(self):
        subscription = make_subscription()
        submit(subscription, T0 + days(3))

        step = services.prepare_disposal(subscription.site.account, T0 + days(4), dry_run=True)

        self.assertEqual(
            step.reasons, ({"reason": "payment_under_review", "site": subscription.site.public_id},)
        )

    def test_a_real_disposal_is_refused_and_cancels_nothing_while_a_proof_waits(self):
        from apps.accounts.management.commands.dispose_account import _reason

        subscription = make_subscription()
        submit(subscription, T0 + days(3))

        step = services.prepare_disposal(subscription.site.account, T0 + days(4))

        self.assertEqual((step.cancelled, len(step.reasons)), ((), 1))
        self.assertEqual(fresh(subscription).state, State.TRIAL_ACTIVE)
        self.assertIn("proof of payment under review", _reason(step.reasons[0]))

    def test_a_site_made_after_its_trial_ended_has_30_days_to_pay_from_its_creation(self):
        made_at = T0 + days(50)
        subscription = make_subscription(now=made_at)

        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(subscription.service_ended_at, TRIAL_END)
        self.assertEqual(subscription.download_until, made_at + days(30))
        self.assertEqual(subscription.deletion_due_at, made_at + days(30))
        payment = submit(subscription, made_at + days(29))
        confirm(payment, self.operator, made_at + days(30) - SECOND)
        self.assertEqual(fresh(subscription).state, State.ACTIVE)

    def test_a_site_made_during_its_trial_counts_from_the_trial_end(self):
        subscription = make_subscription(now=T0 + days(5))
        services.sweep(now=TRIAL_END)
        self.assertEqual(fresh(subscription).deletion_due_at, TRIAL_END + days(30))


@override_settings(BILLING_ENABLED=True)
class ClocksStartedTests(TestCase):
    def test_the_clocks_start_once_when_collection_goes_live_and_are_not_taken_back(self):
        never_paid = make_subscription()
        paid = make_suspended(email="ben@example.test", name="bens-store")
        services.sweep(now=TRIAL_END)
        self.assertIsNone(fresh(never_paid).deletion_due_at)
        paid_dates = fresh(paid).deletion_due_at
        live_at = CYCLE_1_END + days(20)

        with transfers_on():
            self.assertEqual(services.sweep(now=live_at, dry_run=True), {"clocks_started": 1})
            self.assertIsNone(fresh(never_paid).deletion_due_at)
            self.assertEqual(services.sweep(now=live_at), {"clocks_started": 1})
            self.assertEqual(services.sweep(now=live_at + days(1)), {})

        never_paid = fresh(never_paid)
        self.assertEqual(never_paid.state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(never_paid.service_ended_at, TRIAL_END)
        self.assertEqual(never_paid.download_until, live_at + days(30))
        self.assertEqual(never_paid.deletion_due_at, live_at + days(30))
        transition = never_paid.transitions.get(code="clocks_started")
        self.assertEqual(transition.idempotency_key, f"clocks_started:{never_paid.pk}")
        self.assertEqual(transition.effective_at, live_at)
        self.assertEqual((transition.from_state, transition.to_state),
                         (State.CANCELLED_NO_PAYMENT_METHOD, State.CANCELLED_NO_PAYMENT_METHOD))
        self.assertEqual(fresh(paid).deletion_due_at, paid_dates)
        self.assertEqual(services.sweep(now=live_at + days(2)), {})  # switched off again
        self.assertEqual(fresh(never_paid).deletion_due_at, live_at + days(30))


@override_settings(BILLING_ENABLED=True)
class SwitchTests(TestCase):
    def test_collection_is_live_only_with_the_switch_an_account_and_a_store(self):
        self.assertFalse(plan.collection_live())
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=True):
            self.assertFalse(plan.collection_live())  # no account, no store
            make_receiving_account()
            self.assertFalse(plan.collection_live())  # no store
        ReceivingAccount.objects.all().delete()
        with transfers_on():
            self.assertTrue(plan.collection_live())
            self.assertEqual(plan.transfer_accounts()[0]["provider_name"], "Test Bank")
            with override_settings(BILLING_ENABLED=False):
                self.assertFalse(plan.collection_live())

    def test_no_row_or_only_inactive_rows_is_no_account_and_fails_closed(self):
        with transfers_on():
            ReceivingAccount.objects.update(active=False)
            self.assertEqual(plan.transfer_accounts(), [])
            self.assertFalse(plan.collection_live())
            ReceivingAccount.objects.all().delete()
            self.assertEqual(plan.transfer_accounts(), [])
            self.assertFalse(plan.collection_live())

    def test_switching_the_only_account_off_closes_collection_at_once(self):
        with transfers_on():
            self.assertTrue(plan.collection_live())
            ReceivingAccount.objects.update(active=False)
            self.assertFalse(plan.collection_live())  # no cache
            ReceivingAccount.objects.update(active=True)
            self.assertTrue(plan.collection_live())

    def test_accounts_are_listed_by_order_then_id(self):
        make_receiving_account([
            {"kind": "bank", "provider_name": "B", "account_name": "n", "account_number": "111111"},
            {"kind": "ewallet", "provider_name": "A", "account_name": "n", "account_number": "222222"},
            {"kind": "bank", "provider_name": "C", "account_name": "n", "account_number": "333333"},
        ])
        ReceivingAccount.objects.filter(provider_name="C").update(sort_order=0)
        ReceivingAccount.objects.filter(provider_name="B").update(active=False)
        self.assertEqual([a["provider_name"] for a in plan.transfer_accounts()], ["C", "A"])
        self.assertEqual(set(plan.transfer_accounts()[0]),
                         {"kind", "provider_name", "account_name", "account_number"})

    def test_a_switch_that_is_off_reads_no_account_table(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertFalse(plan.collection_live())  # BILLING_BANK_TRANSFER_ENABLED off
            with override_settings(BILLING_ENABLED=False, BILLING_BANK_TRANSFER_ENABLED=True):
                self.assertFalse(plan.collection_live())
            with override_settings(BILLING_BANK_TRANSFER_ENABLED=True), mock.patch(
                "apps.billing.proofs.available", return_value=False
            ):
                self.assertFalse(plan.collection_live())  # no store
        self.assertEqual([q for q in queries if "receivingaccount" in q["sql"].lower()], [])

    def test_switched_off_nothing_is_accepted_and_no_new_table_is_read(self):
        subscription = make_subscription()
        operator = make_operator()
        with transfers_on():
            payment = submit(subscription, T0 + days(3))

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(ApiError) as raised:
                submit(subscription, T0 + days(4), content=PNG)
            self.assertEqual((raised.exception.code, raised.exception.status), ("not_found", 404))
            for call in (
                lambda: confirm(payment, operator, T0 + days(4)),
                lambda: services.reject_transfer(payment, operator, reason="unreadable", now=T0 + days(4)),
                lambda: services.proof_for_operator(payment, operator),
            ):
                with self.assertRaises(ApiError) as raised:
                    call()
                self.assertEqual(raised.exception.status, 404)
            self.assertFalse(methods.active_at(subscription.site, TRIAL_END))
            services.position(subscription.site, at=TRIAL_END)
            services.overview(subscription.site, at=TRIAL_END)
            list(Site.objects.filter(services.state_filter(TRIAL_END, list(State))))
            services.sweep(now=TRIAL_END)
            services.prepare_disposal(subscription.site.account, TRIAL_END + days(1), dry_run=True)
        touched = [q["sql"] for q in queries if "banktransfer" in q["sql"].lower()
                   or "billingnotice" in q["sql"].lower()]
        self.assertEqual(touched, [])
        payment.refresh_from_db()
        self.assertEqual(payment.status, Status.SUBMITTED)

    def test_the_checks_refuse_the_switch_without_a_store_or_an_account(self):
        self.assertEqual(proofs.check_bank_transfer(None), [])
        self.assertEqual(proofs.check_receiving_account(None, databases=["default"]), [])
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=True), mock.patch(
            "apps.billing.proofs.available", return_value=False
        ):
            errors = proofs.check_bank_transfer(None)
            self.assertEqual([(type(e), e.id) for e in errors], [(Error, "billing.E002")])
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=True):
            self.assertEqual(proofs.check_receiving_account(None), [])  # no database named
            errors = proofs.check_receiving_account(None, databases=["default"])
            self.assertEqual([(type(e), e.id) for e in errors], [(Warning, "billing.W001")])
            make_receiving_account()
            self.assertEqual(proofs.check_receiving_account(None, databases=["default"]), [])
            ReceivingAccount.objects.update(active=False)
            self.assertEqual(
                [e.id for e in proofs.check_receiving_account(None, databases=["default"])],
                ["billing.W001"],
            )

    def test_the_account_check_reads_the_database_only_when_asked_to(self):
        # A plain check passes no database, so it never queries. `check --database default`
        # and `migrate` name one; the missing account is then a warning, not an error.
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import SystemCheckError

        with override_settings(BILLING_BANK_TRANSFER_ENABLED=True), mock.patch(
            "apps.billing.proofs.available", return_value=True
        ):
            with CaptureQueriesContext(connection) as queries:
                call_command("check", stdout=StringIO())
            self.assertEqual([q for q in queries if "receivingaccount" in q["sql"].lower()], [])
            err = StringIO()
            call_command("check", "--database", "default", stdout=StringIO(), stderr=err)
            self.assertIn("billing.W001", err.getvalue())
            with self.assertRaises(SystemCheckError) as raised:
                call_command("check", databases=["default"], fail_level="WARNING",
                             stdout=StringIO(), stderr=StringIO())
            self.assertIn("billing.W001", str(raised.exception))

    def test_migrate_succeeds_with_the_switch_on_and_no_receiving_account(self):
        from io import StringIO

        from django.core.management import call_command

        self.assertFalse(ReceivingAccount.objects.exists())
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=True), mock.patch(
            "apps.billing.proofs.available", return_value=True
        ):
            err = StringIO()
            call_command("migrate", verbosity=0, skip_checks=False, stdout=StringIO(), stderr=err)
            self.assertIn("billing.W001", err.getvalue())

    def test_the_account_check_never_queries_a_missing_table(self):
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=True), mock.patch(
            "django.db.backends.base.introspection.BaseDatabaseIntrospection.table_names",
            return_value=["django_migrations"],
        ), CaptureQueriesContext(connection) as queries:
            self.assertEqual(proofs.check_receiving_account(None, databases=["default"]), [])
        self.assertEqual([q for q in queries if "receivingaccount" in q["sql"].lower()], [])
