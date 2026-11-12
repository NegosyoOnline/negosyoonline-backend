"""The billing emails (Billing Build B2a-E): what is sent, once, and when nothing is."""
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from apps.billing import notices, plan, services, tasks
from apps.billing.models import BankTransferPayment, BillingNotice
from apps.billing.tests.base import (
    CYCLE_1_END, JPEG, T0, TEST_ACCOUNTS, TRIAL_END, days, make_active, make_operator,
    make_subscription, make_suspended, submit, transfers_on,
)
from apps.notifications.services import EmailNotSent

HOUR = days(1) / 24
STAFF = "alerts@example.test"


def reject(payment, operator, at, reason="unreadable", note=""):
    return services.reject_transfer(payment, operator, reason=reason, note=note, now=at)


def confirm(payment, operator, at):
    return services.confirm_transfer(
        payment, operator, amount_received_centavos=payment.amount_centavos, now=at
    )


@override_settings(BILLING_ENABLED=True, BILLING_STAFF_ALERT_EMAIL=STAFF)
class NoticeCase(TestCase):
    def setUp(self):
        on = transfers_on()
        self.store = on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.operator = make_operator()

    def run_at(self, moment):
        return notices.run(moment)

    def sent_to(self, address):
        return [m for m in mail.outbox if m.to == [address]]


class ProofEmailTests(NoticeCase):
    def test_a_proof_gives_one_email_to_the_owner_and_one_alert_to_staff_once(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(1))

        first = self.run_at(T0 + days(1) + HOUR)
        second = self.run_at(T0 + days(1) + 2 * HOUR)

        self.assertEqual(first, {"proof_received": 1, "staff_proof_waiting": 1})
        self.assertEqual(second, {})
        owner = self.sent_to("ana@example.test")
        self.assertEqual([m.subject for m in owner], ["We received your bank transfer proof"])
        self.assertIn("PHP 2,499.00", owner[0].body)
        self.assertIn("Ana's Store", owner[0].body)
        self.assertIn("/settings/billing/", owner[0].body)
        alert = self.sent_to(STAFF)
        self.assertEqual(alert[0].subject, f"Bank transfer proof waiting: {payment.public_id}")
        self.assertNotIn("ana@example.test", alert[0].body)
        self.assertNotIn("Ana's Store", alert[0].body)
        self.assertEqual(BillingNotice.objects.count(), 2)

    def test_no_alert_without_a_staff_address(self):
        submit(make_subscription(), T0 + days(1))
        with override_settings(BILLING_STAFF_ALERT_EMAIL=""):
            self.assertEqual(self.run_at(T0 + days(1) + HOUR), {"proof_received": 1})
        self.assertEqual(self.sent_to(STAFF), [])

    def test_a_proof_already_decided_gets_no_received_email(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(1))
        reject(payment, self.operator, T0 + days(1) + HOUR)
        result = self.run_at(T0 + days(1) + 2 * HOUR)
        self.assertEqual(result, {"payment_rejected": 1})

    def test_an_old_event_is_not_announced(self):
        submit(make_subscription(), T0 + days(1))
        self.assertEqual(self.run_at(T0 + days(4)), {})
        self.assertEqual(mail.outbox, [])


class DecisionEmailTests(NoticeCase):
    def test_a_monthly_approval_names_the_term_and_the_next_payment(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(1))
        confirm(payment, self.operator, T0 + days(1) + HOUR)

        self.run_at(T0 + days(1) + 2 * HOUR)

        approved = [m for m in self.sent_to("ana@example.test") if m.subject == "Your payment is approved"]
        self.assertEqual(len(approved), 1)
        body = approved[0].body
        self.assertIn("PHP 2,499.00", body)
        self.assertIn("NegosyoOnline Monthly", body)
        self.assertIn("Your next payment is due on", body)
        self.assertNotIn("active again", body)
        self.assertNotIn("proof", body.lower())

    def test_a_promo_approval_names_the_monthly_price_after_it(self):
        subscription = make_subscription()
        with override_settings(BILLING_LAUNCH_PROMO_ENABLED=True):
            payment = submit(subscription, T0 + days(1), plan_code="launch_promo")
        confirm(payment, self.operator, T0 + days(1) + HOUR)
        self.run_at(T0 + days(1) + 2 * HOUR)
        body = [m for m in mail.outbox if m.subject == "Your payment is approved"][0].body
        self.assertIn("PHP 4,999.00", body)
        self.assertIn("Your next payment of PHP 2,499.00 is due on", body)

    def test_approving_a_paused_site_says_it_is_active_again(self):
        subscription = make_suspended()
        at = CYCLE_1_END + days(8)
        payment = submit(subscription, at)
        confirm(payment, self.operator, at + HOUR)
        self.run_at(at + 2 * HOUR)
        body = [m for m in mail.outbox if m.subject == "Your payment is approved"][0].body
        self.assertIn("Your site is active again.", body)

    def test_a_rejection_gives_the_reason_and_the_note_only_for_other(self):
        for reason, sentence in (
            ("amount_mismatch", "the amount does not match the amount due"),
            ("not_received", "we did not find the transfer in our account"),
        ):
            with self.subTest(reason=reason):
                mail.outbox.clear()
                subscription = make_subscription(email=f"{reason}@example.test", name=reason)
                payment = submit(subscription, T0 + days(1))
                reject(payment, self.operator, T0 + days(1) + HOUR, reason, note="secret note")
                self.run_at(T0 + days(1) + 2 * HOUR)
                body = self.sent_to(f"{reason}@example.test")[-1].body
                self.assertIn(f"Reason: {sentence}.", body)
                self.assertNotIn("secret note", body)
                self.assertIn("send a new proof", body)

    def test_a_rejection_with_reason_other_carries_the_operators_note(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(1))
        reject(payment, self.operator, T0 + days(1) + HOUR, "other", note="Please send the whole page.")
        self.run_at(T0 + days(1) + 2 * HOUR)
        body = self.sent_to("ana@example.test")[-1].body
        self.assertIn("Reason: Please send the whole page.", body)


class ReminderTests(NoticeCase):
    def test_trial_reminders_at_three_days_and_twenty_four_hours_once_each(self):
        make_subscription()
        self.assertEqual(self.run_at(TRIAL_END - days(4)), {})
        self.assertEqual(self.run_at(TRIAL_END - days(2)), {"trial_reminder_3d": 1})
        self.assertEqual(self.run_at(TRIAL_END - days(1) - HOUR), {})
        self.assertEqual(self.run_at(TRIAL_END - 23 * HOUR), {"trial_reminder_1d": 1})
        self.assertEqual(self.run_at(TRIAL_END - 2 * HOUR), {})
        subjects = [m.subject for m in mail.outbox]
        self.assertEqual(
            subjects,
            ["Your NegosyoOnline trial ends in 3 days", "Your NegosyoOnline trial ends tomorrow"],
        )
        self.assertNotIn("delet", mail.outbox[0].body.lower())

    def test_only_the_latest_due_trial_reminder_is_sent(self):
        make_subscription()
        self.assertEqual(self.run_at(TRIAL_END - 10 * HOUR), {"trial_reminder_1d": 1})

    def test_no_trial_reminder_while_a_proof_waits(self):
        subscription = make_subscription()
        submit(subscription, TRIAL_END - days(2) - HOUR)
        result = self.run_at(TRIAL_END - days(2))
        self.assertNotIn("trial_reminder_3d", result)

    def test_no_reminder_for_an_exempt_site(self):
        subscription = make_subscription()
        type(subscription).objects.filter(pk=subscription.pk).update(billing_exempt=True)
        self.assertEqual(self.run_at(TRIAL_END - days(2)), {})

    def test_renewal_reminders_at_seven_and_three_days_once_each(self):
        make_active()
        self.assertEqual(self.run_at(CYCLE_1_END - days(8)), {})
        self.assertEqual(self.run_at(CYCLE_1_END - days(6)), {"renewal_reminder_7d": 1})
        self.assertEqual(self.run_at(CYCLE_1_END - days(5)), {})
        self.assertEqual(self.run_at(CYCLE_1_END - days(2)), {"renewal_reminder_3d": 1})
        self.assertEqual(self.run_at(CYCLE_1_END - days(1)), {})
        body = mail.outbox[0].body
        self.assertIn("PHP 2,499.00", body)
        self.assertIn("stays on for a short time", body)
        self.assertNotIn("7 days", body)

    def test_no_renewal_reminder_while_a_proof_waits(self):
        subscription = make_active()
        submit(subscription, CYCLE_1_END - days(6) - HOUR)
        self.assertNotIn("renewal_reminder_7d", self.run_at(CYCLE_1_END - days(6)))


class PausedEmailTests(NoticeCase):
    def test_a_suspension_gives_one_paused_email_with_the_last_date_to_pay(self):
        subscription = make_suspended()
        at = CYCLE_1_END + days(7) + HOUR
        self.assertEqual(self.run_at(at), {"service_paused": 1})
        self.assertEqual(self.run_at(at + HOUR), {})
        message = self.sent_to("ana@example.test")[0]
        self.assertEqual(message.subject, "Your NegosyoOnline site is paused")
        self.assertIn("Payment can be accepted until", message.body)
        self.assertNotIn("delet", message.body.lower())

    def test_a_trial_that_ended_unpaid_gives_the_trial_ended_email(self):
        make_subscription()
        services.sweep(now=TRIAL_END + HOUR)
        self.assertEqual(self.run_at(TRIAL_END + 2 * HOUR), {"trial_ended": 1})
        self.assertEqual(mail.outbox[0].subject, "Your NegosyoOnline trial has ended")
        self.assertIn("is paused", mail.outbox[0].body)

    def test_a_paused_email_is_skipped_when_the_site_is_no_longer_suspended(self):
        subscription = make_suspended()
        at = CYCLE_1_END + days(7) + HOUR
        payment = submit(subscription, at)
        confirm(payment, self.operator, at + HOUR)
        result = self.run_at(at + 2 * HOUR)
        self.assertNotIn("service_paused", result)


class GuardTests(NoticeCase):
    def test_nothing_is_sent_or_read_while_collection_is_off(self):
        make_subscription()
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            with self.assertNumQueries(0):
                self.assertEqual(self.run_at(TRIAL_END - days(2)), {})
        self.assertEqual(mail.outbox, [])

    def test_nothing_is_sent_while_billing_is_off(self):
        make_subscription()
        with override_settings(BILLING_ENABLED=False):
            self.assertEqual(self.run_at(TRIAL_END - days(2)), {})

    def test_a_disposed_address_gets_nothing_and_no_notice_row(self):
        submit(make_subscription(), T0 + days(1))
        with mock.patch("apps.billing.notices.is_disposed_address", return_value=True):
            result = self.run_at(T0 + days(1) + HOUR)
        self.assertEqual(result, {"staff_proof_waiting": 1})
        self.assertEqual(self.sent_to("ana@example.test"), [])
        self.assertEqual(BillingNotice.objects.filter(code="proof_received").count(), 0)

    def test_a_failed_send_writes_no_row_and_is_tried_again_until_the_window_ends(self):
        submit(make_subscription(), T0 + days(1))
        with override_settings(BILLING_STAFF_ALERT_EMAIL=""):
            with mock.patch("apps.billing.notices.send_email", side_effect=EmailNotSent()):
                with self.assertLogs("apps.billing.notices", level="WARNING") as logged:
                    self.assertEqual(self.run_at(T0 + days(1) + HOUR), {})
            self.assertEqual(BillingNotice.objects.count(), 0)
            self.assertNotIn("ana@example.test", "".join(logged.output))
            self.assertEqual(self.run_at(T0 + days(1) + 2 * HOUR), {"proof_received": 1})
            self.assertEqual(self.run_at(T0 + days(1) + 3 * HOUR), {})

    def test_a_failed_send_is_given_up_after_48_hours(self):
        submit(make_subscription(), T0 + days(1))
        with override_settings(BILLING_STAFF_ALERT_EMAIL=""):
            with mock.patch("apps.billing.notices.send_email", side_effect=EmailNotSent()):
                self.run_at(T0 + days(1) + HOUR)
            self.assertEqual(self.run_at(T0 + days(3) + HOUR), {})

    def test_no_body_carries_an_account_number_a_key_or_a_tax_claim(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(1))
        confirm(payment, self.operator, T0 + days(1) + HOUR)
        other = submit(make_subscription(email="b@example.test", name="bs-store"), T0 + days(1), content=JPEG + b"x")
        reject(other, self.operator, T0 + days(1) + HOUR, "wrong_account")
        self.run_at(T0 + days(1) + 2 * HOUR)
        self.run_at(TRIAL_END - days(2))
        self.assertTrue(mail.outbox)
        for message in mail.outbox:
            text = (message.subject + message.body).lower()
            for banned in ("0000 0000 00", "billing/proofs", "bir", "test receiver", payment.proof_key):
                self.assertNotIn(banned.lower(), text, message.subject)

    def test_the_task_runs_the_scan_in_process(self):
        submit(make_subscription(), T0 + days(1))
        with mock.patch("apps.billing.notices.timezone") as clock:
            clock.now.return_value = T0 + days(1) + HOUR
            outcome = tasks.send_notices.apply()
        self.assertTrue(outcome.successful(), outcome.traceback)
        self.assertEqual(outcome.result, {"proof_received": 1, "staff_proof_waiting": 1})
