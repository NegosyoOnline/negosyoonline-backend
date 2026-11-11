"""The Annual Plan (Billing Build B3, pull request 3): PHP 24,990.00 paid in advance for one
12-month term, offered only while its switch works, payable again only in its last 30 days."""
from django.test import Client, SimpleTestCase, TestCase, override_settings

from apps.billing import clock, plan, services
from apps.billing.models import BillingCycle, Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, PNG, SECOND, T0, TRIAL_END, days, fresh, make_active, make_subscription, manila,
    submit,
)
from apps.billing.tests.test_transfer import TransferCase, confirm

ANNUAL, MONTHLY = 2_499_000, 249_900
annual_on = override_settings(BILLING_ANNUAL_ENABLED=True)


def three_pm(*parts):
    return manila(*parts, 15, 0)


class AnnualDatesTests(SimpleTestCase):
    def test_the_term_ends_on_the_exact_twelve_month_date(self):
        rows = [
            # the term starts, the anchor day, the term ends
            (manila(2026, 11, 25, 10, 0), 25, three_pm(2027, 11, 25)),
            (manila(2028, 2, 29, 10, 0), 29, three_pm(2029, 2, 28)),  # leap day, then a short Feb
            (manila(2027, 8, 31, 10, 0), 31, three_pm(2028, 8, 31)),
            (manila(2027, 2, 28, 10, 0), 31, three_pm(2028, 2, 29)),  # anchor 31 into a leap Feb
            (manila(2026, 9, 30, 10, 0), 30, three_pm(2027, 9, 30)),
        ]
        for start, anchor, end in rows:
            with self.subTest(start=start):
                self.assertEqual(clock.cycle_end(start, anchor, 12), end)

    def test_the_plan(self):
        self.assertEqual(
            (plan.ANNUAL.price_centavos, plan.ANNUAL.months, plan.ANNUAL.allowance_periods,
             plan.ANNUAL.renews_to),
            (ANNUAL, 12, 12, "annual"),
        )
        # Ten Monthly payments: two months free.
        self.assertEqual(plan.MONTHLY.price_centavos * 10, ANNUAL)


class AnnualOfferTests(TransferCase):
    def codes(self, subscription, at):
        return [offered.code for offered in plan.offers(fresh(subscription), at)]

    def test_it_is_not_offered_while_its_switch_is_off(self):
        subscription = make_subscription()
        self.assertEqual(self.codes(subscription, T0 + days(1)), ["monthly"])
        refusal = self.refused(
            "invalid_request", submit, subscription, T0 + days(1), plan_code="annual"
        )
        self.assertEqual(list(refusal.fields), ["plan"])

    @annual_on
    def test_it_follows_monthly_while_its_switch_works(self):
        subscription = make_subscription()
        self.assertEqual(self.codes(subscription, T0 + days(1)), ["monthly", "annual"])

    @annual_on
    def test_it_is_not_offered_where_nothing_can_be_collected(self):
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            self.assertFalse(plan.annual_open())

    @override_settings(BILLING_LAUNCH_PROMO_ENABLED=True)
    @annual_on
    def test_the_promo_still_leads_a_site_that_never_paid(self):
        self.assertEqual(
            self.codes(make_subscription(), T0 + days(1)), ["launch_promo", "monthly", "annual"]
        )

    @annual_on
    def test_a_proof_already_sent_can_be_confirmed_after_the_switch_goes_off(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3), plan_code="annual")
        with override_settings(BILLING_ANNUAL_ENABLED=False):
            confirm(payment, self.operator, T0 + days(5), amount=ANNUAL)
        cycle = fresh(subscription).cycles.get(sequence=1)
        self.assertEqual((cycle.plan_code, cycle.term_months), ("annual", 12))


@annual_on
class AnnualSettlementTests(TransferCase):
    def test_one_proof_opens_one_twelve_month_cycle_from_the_trial_end(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3), plan_code="annual")
        self.assertEqual((payment.plan_code, payment.amount_centavos), ("annual", ANNUAL))
        self.refused("amount_mismatch", confirm, payment, self.operator, T0 + days(5), MONTHLY)

        confirm(payment, self.operator, T0 + days(5), amount=ANNUAL)
        confirm(payment, self.operator, T0 + days(6), amount=ANNUAL)  # twice: still one cycle

        subscription = fresh(subscription)
        paid = subscription.cycles.filter(settlement="paid")
        self.assertEqual(paid.count(), 1)
        cycle = paid.get()
        self.assertEqual(
            (cycle.start_at, cycle.end_at, cycle.price_centavos, cycle.term_months,
             cycle.plan_code, cycle.plan_version),
            (TRIAL_END, three_pm(2027, 11, 25), ANNUAL, 12, "annual", 1),
        )
        # No monthly cycle is opened inside the term: the trial row and the one paid cycle.
        self.assertEqual(
            list(subscription.cycles.order_by("sequence").values_list("sequence", "settlement")),
            [(0, "not_billable"), (1, "paid")],
        )
        self.assertEqual(subscription.transitions.get(code="first_payment_succeeded")
                         .detail["plan"], "annual")

    def test_a_leap_day_anchor_ends_on_28_february(self):
        subscription = make_subscription(start=manila(2028, 2, 15, 10, 0))
        self.assertEqual(subscription.trial_end_at, manila(2028, 2, 29, 10, 0))
        payment = submit(subscription, manila(2028, 2, 16, 10, 0), plan_code="annual")
        confirm(payment, self.operator, manila(2028, 2, 17, 10, 0), amount=ANNUAL)
        cycle = fresh(subscription).cycles.get(sequence=1)
        self.assertEqual(
            (cycle.start_at, cycle.end_at), (manila(2028, 2, 29, 10, 0), three_pm(2029, 2, 28))
        )
        self.assertEqual(fresh(subscription).anchor_day, 29)

    def test_an_anchor_of_31_keeps_the_last_day_of_each_month(self):
        subscription = make_subscription(start=manila(2026, 8, 17, 10, 0))
        payment = submit(subscription, manila(2026, 8, 18, 10, 0), plan_code="annual")
        confirm(payment, self.operator, manila(2026, 8, 19, 10, 0), amount=ANNUAL)
        cycle = fresh(subscription).cycles.get(sequence=1)
        self.assertEqual((cycle.start_at, cycle.end_at),
                         (manila(2026, 8, 31, 10, 0), three_pm(2027, 8, 31)))
        # The Monthly cycle after it ends on the last day of September.
        self.assertEqual(clock.cycle_end(cycle.end_at, 31, 1), three_pm(2027, 9, 30))

    def test_a_monthly_cycle_paid_for_annual_next_starts_where_it_ends(self):
        subscription = make_active()
        payment = submit(subscription, CYCLE_1_END - days(3), plan_code="annual")
        confirm(payment, self.operator, CYCLE_1_END - days(2), amount=ANNUAL)
        cycle = fresh(subscription).cycles.get(sequence=2)
        self.assertEqual(
            (cycle.start_at, cycle.end_at, cycle.term_months),
            (CYCLE_1_END, three_pm(2027, 12, 25), 12),
        )


@annual_on
class AnnualCycleRulesTests(TransferCase):
    def setUp(self):
        super().setUp()
        self.subscription = make_subscription()
        confirm(
            submit(self.subscription, T0 + days(3), plan_code="annual"),
            self.operator, T0 + days(5), amount=ANNUAL,
        )
        self.end = three_pm(2027, 11, 25)

    def test_a_term_cannot_be_paid_for_again_until_its_last_30_days(self):
        early = self.end - days(30) - SECOND
        self.refused("too_early", submit, self.subscription, early)
        self.refused("too_early", submit, self.subscription, TRIAL_END + days(100), content=PNG)
        options = services.payment_options(fresh(self.subscription), early)
        self.assertEqual(
            (options["payable"], options["not_payable_reason"], options["offers"],
             options["payable_from"]),
            (False, "too_early", [], self.end - days(30)),
        )

    def test_it_can_from_exactly_30_days_before_the_end(self):
        at = self.end - days(30)
        options = services.payment_options(fresh(self.subscription), at)
        self.assertEqual((options["payable"], options["payable_from"]), (True, None))
        self.assertEqual([o["plan"] for o in options["offers"]], ["monthly", "annual"])
        offers = {o["plan"]: o for o in options["offers"]}
        self.assertEqual(offers["annual"]["then"]["interval"], "year")
        self.assertIsNone(offers["monthly"]["then"])
        self.assertEqual(offers["annual"]["period_start_at"], self.end)
        self.assertEqual(offers["annual"]["period_end_at"], three_pm(2028, 11, 25))
        self.assertEqual((offers["annual"]["version"], offers["annual"]["term_months"]), (1, 12))

    def test_the_plan_changes_only_at_renewal(self):
        self.refused("too_early", submit, self.subscription, TRIAL_END + days(60))
        at = self.end - days(10)
        payment = submit(self.subscription, at)  # Monthly, inside the window
        confirm(payment, self.operator, at + days(1))
        cycle = fresh(self.subscription).cycles.get(sequence=2)
        self.assertEqual(
            (cycle.plan_code, cycle.start_at, cycle.end_at),
            ("monthly", self.end, three_pm(2027, 12, 25)),
        )
        self.assertEqual(
            fresh(self.subscription).cycles.get(sequence=1).plan_code, "annual"
        )  # the old term is not re-priced

    def test_cancelling_keeps_service_to_the_end_of_the_term(self):
        services.cancel(fresh(self.subscription), now=TRIAL_END + days(60))
        subscription = fresh(self.subscription)
        self.assertEqual(subscription.service_ended_at, self.end)

    def test_cancelling_after_paying_ahead_keeps_the_paid_term(self):
        at = self.end - days(10)
        confirm(submit(self.subscription, at, plan_code="annual"), self.operator,
                at + days(1), amount=ANNUAL)
        services.cancel(fresh(self.subscription), now=at + days(2))
        self.assertEqual(fresh(self.subscription).service_ended_at, three_pm(2028, 11, 25))

    def test_a_site_that_is_not_active_is_not_held_back(self):
        # Past the end and the 7 days, the site is suspended and can pay at once.
        late = self.end + days(8)
        services.sweep(now=late)
        self.assertEqual(fresh(self.subscription).state, Subscription.State.SUSPENDED)
        payment = submit(self.subscription, late, content=PNG)
        self.assertEqual(payment.plan_code, "monthly")


@annual_on
class FinancePageTests(TransferCase):
    def test_finance_lists_an_annual_proof_with_its_plan(self):
        from apps.billing import staff

        payment = submit(make_subscription(), T0 + days(3), plan_code="annual")
        shown = staff._payment(payment)
        self.assertEqual(
            (shown["plan"], shown["amount_centavos"]),
            ({"code": "annual", "name": "NegosyoOnline Annual", "months": 12}, ANNUAL),
        )


class PlanRouteTests(TestCase):
    URL = "/v1/plan/"

    def get(self):
        return Client().get(self.URL, secure=True)

    def test_monthly_only_while_annual_is_off(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"plans": [{
            "plan": "monthly", "version": 1, "name": "NegosyoOnline Monthly",
            "amount_centavos": MONTHLY, "currency": "PHP", "term_months": 1,
            "interval": "month", "saving_centavos": None,
        }]})

    @override_settings(BILLING_ENABLED=True, BILLING_LAUNCH_PROMO_ENABLED=True)
    def test_annual_is_listed_only_where_it_can_be_paid_and_the_promo_never(self):
        from apps.billing.tests.base import transfers_on

        with override_settings(BILLING_ANNUAL_ENABLED=True):
            self.assertEqual([p["plan"] for p in self.get().json()["plans"]], ["monthly"])
            with transfers_on():
                plans = self.get().json()["plans"]
        self.assertEqual([p["plan"] for p in plans], ["monthly", "annual"])
        self.assertEqual(plans[1], {
            "plan": "annual", "version": 1, "name": "NegosyoOnline Annual",
            "amount_centavos": ANNUAL, "currency": "PHP", "term_months": 12,
            "interval": "year", "saving_centavos": 499_800,
        })

    def test_it_needs_no_sign_in_and_reads_no_table(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            response = self.get()
        self.assertEqual(response.status_code, 200)
        billing_queries = [q for q in queries if "billing_" in q["sql"]]
        self.assertEqual(billing_queries, [])
