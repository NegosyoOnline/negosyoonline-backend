"""Annual is sold at launch (Coach Ed, 2026-11-13: "no the annual must be there.. dont turn it
off"): over HTTP, in the emails, and on a site that was paused or whose service had ended."""
from django.core import mail
from django.test import override_settings

from apps.billing import clock, services
from apps.billing.models import BankTransferPayment, Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, T0, TRIAL_END, days, fresh, make_subscription, make_suspended, submit,
)
from apps.billing.tests.test_annual import ANNUAL, three_pm
from apps.billing.tests.test_notices import HOUR, NoticeCase, confirm
from apps.billing.tests.test_transfer import TransferCase
from apps.billing.tests.test_transfer import confirm as confirm_amount
from apps.billing.tests.test_transfer_api import TransferApiCase

annual_on = override_settings(BILLING_ANNUAL_ENABLED=True)


@annual_on
class AnnualOverHttpTests(TransferApiCase):
    def test_a_proof_for_annual_is_accepted_at_the_annual_amount(self):
        response = self.upload(plan="annual", amount_sent="24990.00")

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["plan"], body["amount_centavos"]), ("annual", 2_499_000))
        payment = BankTransferPayment.objects.get(public_id=body["id"])
        self.assertEqual((payment.plan_code, payment.amount_centavos), ("annual", 2_499_000))

    def test_the_payment_options_offer_annual_after_monthly(self):
        offers = self.get(self.base + "payment/").json()["offers"]

        self.assertEqual([o["plan"] for o in offers], ["monthly", "annual"])
        self.assertEqual(offers[1]["amount_centavos"], 2_499_000)

    def test_with_the_switch_off_annual_is_refused(self):
        with override_settings(BILLING_ANNUAL_ENABLED=False):
            response = self.upload(plan="annual", amount_sent="24990.00")
        self.assertEqual(response.status_code, 400, response.content)


@annual_on
class AnnualEmailTests(NoticeCase):
    def test_the_approval_email_names_the_annual_plan_and_its_twelve_months(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(1), plan_code="annual")
        confirm(payment, self.operator, T0 + days(1) + HOUR)

        self.run_at(T0 + days(1) + 2 * HOUR)

        approved = [m for m in mail.outbox if m.subject == "Your payment is approved"]
        self.assertEqual(len(approved), 1)
        body = approved[0].body
        self.assertIn("PHP 24,990.00", body)
        self.assertIn("NegosyoOnline Annual", body)
        self.assertIn("25 November 2026 to 25 November 2027", body)
        self.assertIn("Your next payment is due on 25 November 2027", body)

    def test_the_renewal_reminder_says_php_24990_and_the_due_date(self):
        subscription = make_subscription()
        confirm_amount(
            submit(subscription, T0 + days(3), plan_code="annual"), self.operator,
            T0 + days(5), amount=ANNUAL,
        )
        end = three_pm(2027, 11, 25)
        mail.outbox.clear()

        self.assertEqual(self.run_at(end - days(6)), {"renewal_reminder_7d": 1})

        reminder = mail.outbox[0]
        self.assertEqual(reminder.subject, "Your NegosyoOnline payment is due on 25 November 2027")
        self.assertIn("PHP 24,990.00", reminder.body)
        self.assertNotIn("PHP 2,499.00", reminder.body)


@annual_on
class AnnualAfterAStopTests(TransferCase):
    def test_confirmed_on_a_suspended_site_the_term_starts_at_the_confirmation(self):
        subscription = make_suspended()
        at = CYCLE_1_END + days(8)
        payment = submit(subscription, at, plan_code="annual")

        confirm_amount(payment, self.operator, at + days(1), amount=ANNUAL)

        subscription = fresh(subscription)
        cycle = subscription.cycles.order_by("sequence").last()
        self.assertEqual(subscription.state, Subscription.State.ACTIVE)
        self.assertEqual((cycle.plan_code, cycle.start_at), ("annual", at + days(1)))
        self.assertEqual(cycle.term_months, 12)
        self.assertEqual(
            cycle.end_at.astimezone(clock.MANILA).year,
            (at + days(1)).astimezone(clock.MANILA).year + 1,
        )

    def test_confirmed_on_a_site_whose_trial_ended_unpaid_the_term_starts_at_the_confirmation(self):
        subscription = make_subscription()
        at = TRIAL_END + days(2)
        services.sweep(now=at)
        payment = submit(subscription, at, plan_code="annual")

        confirm_amount(payment, self.operator, at + days(1), amount=ANNUAL)

        cycle = fresh(subscription).cycles.order_by("sequence").last()
        self.assertEqual((cycle.plan_code, cycle.start_at, cycle.term_months),
                         ("annual", at + days(1), 12))
