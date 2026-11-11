"""The six-month Launch Promo (Billing Build B2a, pull request 3): PHP 4,999.00 once for six
months, ending on the exact six-month date, then PHP 2,499.00 monthly."""
from django.test import SimpleTestCase, override_settings
from django.utils.dateparse import parse_datetime

from apps.billing import clock, plan, services
from apps.billing.models import BankTransferPayment, BillingCycle
from apps.billing.tests.base import (
    PNG, T0, TRIAL_END, days, fresh, make_active, make_subscription, manila, submit,
)
from apps.billing.tests.test_transfer import TransferCase, confirm
from apps.billing.tests.test_transfer_api import TransferApiCase

PROMO, MONTHLY = 499_900, 249_900
promo_on = override_settings(BILLING_LAUNCH_PROMO_ENABLED=True)


def three_pm(*parts):
    return manila(*parts, 15, 0)


class PromoDatesTests(SimpleTestCase):
    def test_the_term_ends_on_the_exact_six_month_date(self):
        rows = [
            # the cycle starts, the anchor day, the promo ends, the next Monthly cycle ends
            (manila(2026, 11, 8, 9, 0), 8, three_pm(2027, 5, 8), three_pm(2027, 6, 8)),
            (manila(2026, 8, 31, 9, 0), 31, three_pm(2027, 2, 28), three_pm(2027, 3, 31)),
            (manila(2027, 8, 31, 9, 0), 31, three_pm(2028, 2, 29), three_pm(2028, 3, 31)),
            (manila(2026, 9, 30, 9, 0), 30, three_pm(2027, 3, 30), three_pm(2027, 4, 30)),
            (manila(2028, 2, 29, 9, 0), 29, three_pm(2028, 8, 29), three_pm(2028, 9, 29)),
            (manila(2026, 7, 15, 9, 0), 15, three_pm(2027, 1, 15), three_pm(2027, 2, 15)),
        ]
        for start, anchor, promo_end, monthly_end in rows:
            with self.subTest(start=start):
                self.assertEqual(clock.cycle_end(start, anchor, 6), promo_end)
                self.assertEqual(clock.cycle_end(promo_end, anchor), monthly_end)

    def test_it_is_never_180_days(self):
        start = manila(2026, 11, 8, 15, 0)
        self.assertNotEqual(clock.cycle_end(start, 8, 6), start + days(180))

    def test_the_two_plans(self):
        self.assertEqual(
            [(p.code, p.price_centavos, p.months) for p in plan.PLANS.values()],
            [("monthly", MONTHLY, 1), ("launch_promo", PROMO, 6)],
        )


class PromoOfferTests(TransferCase):
    def codes(self, subscription, at):
        return [offered.code for offered in plan.offers(fresh(subscription), at)]

    def test_it_is_not_offered_while_its_switch_is_off(self):
        subscription = make_subscription()
        self.assertEqual(self.codes(subscription, T0 + days(1)), ["monthly"])
        refusal = self.refused(
            "invalid_request", submit, subscription, T0 + days(1), plan_code="launch_promo"
        )
        self.assertEqual(list(refusal.fields), ["plan"])

    @promo_on
    def test_it_leads_the_offers_of_a_site_that_never_paid(self):
        subscription = make_subscription()
        self.assertEqual(self.codes(subscription, T0 + days(1)), ["launch_promo", "monthly"])
        services.sweep(now=TRIAL_END)  # ended without paying: still never paid
        self.assertEqual(self.codes(subscription, TRIAL_END + days(1)), ["launch_promo", "monthly"])

    @promo_on
    def test_it_is_not_offered_after_any_paid_cycle(self):
        subscription = make_active()
        at = TRIAL_END + days(40)  # past the due date, inside the 7 days
        self.assertEqual(self.codes(subscription, at), ["monthly"])
        refusal = self.refused("invalid_request", submit, subscription, at, plan_code="launch_promo")
        self.assertEqual(list(refusal.fields), ["plan"])


@promo_on
class PromoSettlementTests(TransferCase):
    def test_paid_early_in_the_trial_it_opens_one_six_month_cycle_from_the_trial_end(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3), plan_code="launch_promo")
        self.assertEqual((payment.plan_code, payment.amount_centavos), ("launch_promo", PROMO))

        confirm(payment, self.operator, T0 + days(5), amount=PROMO)

        subscription = fresh(subscription)
        cycle = subscription.cycles.get(sequence=1)
        self.assertEqual(
            (cycle.start_at, cycle.end_at, cycle.settlement),
            (TRIAL_END, three_pm(2027, 5, 25), BillingCycle.Settlement.PAID),
        )
        self.assertEqual(subscription.cycles.count(), 2)
        transition = subscription.transitions.get(code="first_payment_succeeded")
        self.assertEqual(
            (transition.detail["plan"], transition.detail["amount_centavos"]), ("launch_promo", PROMO)
        )
        # Served for the whole term, and for the 7 days after it.
        self.assertTrue(services.position(subscription.site, at=three_pm(2027, 5, 25)).storefront_served)
        self.assertFalse(
            services.position(subscription.site, at=three_pm(2027, 6, 1)).storefront_served
        )

    def test_the_monthly_price_is_refused_for_a_promo_payment(self):
        payment = submit(make_subscription(), T0 + days(3), plan_code="launch_promo")
        self.refused("amount_mismatch", confirm, payment, self.operator, T0 + days(5), MONTHLY)

    def test_after_the_promo_only_monthly_can_be_bought_and_it_runs_one_month(self):
        subscription = make_subscription()
        confirm(
            submit(subscription, T0 + days(3), plan_code="launch_promo"), self.operator,
            T0 + days(5), amount=PROMO,
        )
        promo_end = three_pm(2027, 5, 25)
        at = promo_end - days(3)

        self.assertEqual([p.code for p in plan.offers(fresh(subscription), at)], ["monthly"])
        self.refused("invalid_request", submit, subscription, at, plan_code="launch_promo")
        renewal = submit(subscription, at, content=PNG)
        self.assertEqual(renewal.amount_centavos, MONTHLY)
        confirm(renewal, self.operator, promo_end - days(2))

        cycle = fresh(subscription).cycles.get(sequence=2)
        self.assertEqual((cycle.start_at, cycle.end_at), (promo_end, three_pm(2027, 6, 25)))
        self.assertEqual(
            sorted(BankTransferPayment.objects.filter(status="confirmed")
                   .values_list("plan_code", "amount_centavos")),
            [("launch_promo", PROMO), ("monthly", MONTHLY)],
        )

    def test_paid_after_the_trial_ended_the_six_months_start_at_the_confirmation(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)
        payment = submit(subscription, TRIAL_END + days(2), plan_code="launch_promo")
        at = TRIAL_END + days(3)  # 28 November 2026, 10:00 AM

        confirm(payment, self.operator, at, amount=PROMO)

        subscription = fresh(subscription)
        cycle = subscription.cycles.get(sequence=1)
        self.assertEqual((cycle.start_at, cycle.end_at), (at, three_pm(2027, 5, 28)))
        self.assertEqual(subscription.anchor_day, 28)

    def test_a_promo_proof_is_not_confirmed_once_a_cycle_was_paid(self):
        """Never PHP 4,999 twice, even for a proof sent while it was still offered."""
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3), plan_code="launch_promo")
        BillingCycle.objects.filter(subscription=subscription, sequence=0).update(settlement="paid")

        self.refused("plan_not_offered", confirm, payment, self.operator, T0 + days(5), PROMO)

        payment.refresh_from_db()
        self.assertEqual(payment.status, "submitted")

    def test_a_promo_proof_is_honoured_after_the_promo_is_switched_off(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3), plan_code="launch_promo")
        with override_settings(BILLING_LAUNCH_PROMO_ENABLED=False):
            confirm(payment, self.operator, T0 + days(5), amount=PROMO)
        self.assertEqual(fresh(subscription).cycles.get(sequence=1).end_at, three_pm(2027, 5, 25))


@promo_on
class PromoApiTests(TransferApiCase):
    def test_the_page_leads_with_the_promo_and_says_what_follows(self):
        promo, monthly = self.get(self.base + "payment/").json()["offers"]

        self.assertEqual(
            (promo["plan"], promo["name"], promo["amount_centavos"], promo["months"]),
            ("launch_promo", "NegosyoOnline Launch Promo (6 Months)", PROMO, 6),
        )
        end = clock.cycle_end(self.trial_end, clock.anchor_day(self.trial_end), 6)
        self.assertEqual(parse_datetime(promo["period_end_at"]), end.replace(microsecond=0))
        self.assertEqual(promo["then"], {
            "plan": "monthly", "amount_centavos": MONTHLY, "interval": "month",
            "first_due_at": promo["period_end_at"],
        })
        self.assertEqual((monthly["plan"], monthly["amount_centavos"], monthly["then"]),
                         ("monthly", MONTHLY, None))

    def test_an_ended_site_is_told_the_term_starts_at_the_confirmation(self):
        self.at(self.trial_end + days(1))
        promo = self.get(self.base + "payment/").json()["offers"][0]
        self.assertEqual(
            (promo["plan"], promo["starts_at_confirmation"], promo["period_end_at"],
             promo["then"]["first_due_at"]),
            ("launch_promo", True, None, None),
        )

    def test_a_promo_proof_is_accepted_and_then_the_next_offer_is_monthly(self):
        sent = self.upload(plan="launch_promo").json()
        self.assertEqual((sent["plan"], sent["amount_centavos"]), ("launch_promo", PROMO))

        self.confirm(sent["id"], self.start + days(2))

        self.at(self.trial_end + days(170))
        offers = self.get(self.base + "payment/").json()["offers"]
        self.assertEqual([(o["plan"], o["amount_centavos"]) for o in offers], [("monthly", MONTHLY)])
        body = self.error(self.upload(plan="launch_promo", content=PNG), 400, "invalid_request")
        self.assertEqual(list(body["fields"]), ["plan"])
