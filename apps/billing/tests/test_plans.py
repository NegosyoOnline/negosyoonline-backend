"""Versioned plans and the price snapshot of a cycle (Billing Build B3, pull request 2)."""
from dataclasses import replace
from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from apps.billing import plan, services
from apps.billing.models import BillingCycle
from apps.billing.tests.base import (
    CYCLE_1_END, T0, TRIAL_END, days, fresh, make_active, make_attention, make_subscription,
    manila, method_on_file,
)

Settlement = BillingCycle.Settlement


class CatalogueTests(SimpleTestCase):
    def test_the_annual_price_is_ten_months_and_saves_twelve_less_ten(self):
        self.assertEqual(plan.ANNUAL.price_centavos, 2_499_000)  # PHP 24,990.00
        self.assertEqual(plan.ANNUAL.price_centavos, 10 * plan.MONTHLY.price_centavos)
        saving = 12 * plan.MONTHLY.price_centavos - plan.ANNUAL.price_centavos
        self.assertEqual(saving, 499_800)  # PHP 4,998.00

    def test_the_terms_and_what_follows(self):
        self.assertEqual(
            [(p.code, p.months, p.allowance_periods, p.renews_to, p.needs_consent)
             for p in (plan.MONTHLY, plan.ANNUAL, plan.LAUNCH_PROMO)],
            [("monthly", 1, 1, "monthly", False), ("annual", 12, 12, "annual", False),
             ("launch_promo", 6, 6, "monthly", True)],
        )

    def test_every_sold_plan_is_in_the_history_and_an_unknown_version_is_an_error(self):
        for sold in plan.PLANS.values():
            self.assertIs(plan.version_of(sold.code, sold.version), sold)
        with self.assertRaises(KeyError):
            plan.version_of("monthly", 99)

    def test_the_monthly_aliases_are_unchanged(self):
        self.assertEqual((plan.PLAN_NAME, plan.PRICE_CENTAVOS), ("NegosyoOnline Monthly", 249_900))

    @override_settings(BILLING_ANNUAL_ENABLED=True)
    def test_annual_is_offered_after_monthly_only_where_a_payment_can_be_taken(self):
        subscription = mock.Mock(cycles=mock.Mock(filter=lambda **k: mock.Mock(exists=lambda: False)))
        with mock.patch.object(plan, "collection_live", return_value=True):
            self.assertEqual([p.code for p in plan.offers(subscription, T0)], ["monthly", "annual"])
        with mock.patch.object(plan, "collection_live", return_value=False):
            self.assertEqual([p.code for p in plan.offers(subscription, T0)], ["monthly"])

    @override_settings(BILLING_ANNUAL_ENABLED=False)
    def test_annual_is_not_offered_while_its_switch_is_off(self):
        subscription = mock.Mock(cycles=mock.Mock(filter=lambda **k: mock.Mock(exists=lambda: False)))
        with mock.patch.object(plan, "collection_live", return_value=True):
            self.assertEqual([p.code for p in plan.offers(subscription, T0)], ["monthly"])


@override_settings(BILLING_ENABLED=True)
class SnapshotTests(TestCase):
    def test_the_trial_cycle_has_no_snapshot(self):
        trial = make_subscription().cycles.get(sequence=0)
        self.assertEqual((trial.plan_code, trial.plan_version, trial.price_centavos), ("", None, None))

    def test_a_monthly_payment_opens_a_cycle_that_says_so(self):
        cycle = make_active().cycles.get(sequence=1)
        self.assertEqual(
            (cycle.plan_code, cycle.plan_version, cycle.price_centavos, cycle.term_months),
            ("monthly", 1, 249_900, 1),
        )

    def test_an_annual_payment_opens_one_twelve_month_cycle(self):
        subscription = make_active()
        services.renewed(
            subscription, paid_at=CYCLE_1_END, reference="annual-1", bought=plan.ANNUAL
        )
        cycle = fresh(subscription).cycles.get(sequence=2)
        self.assertEqual(
            (cycle.start_at, cycle.end_at, cycle.plan_code, cycle.price_centavos, cycle.term_months),
            (CYCLE_1_END, manila(2027, 12, 25, 15, 0), "annual", 2_499_000, 12),
        )

    def test_a_pending_cycle_holds_the_terms_that_were_due(self):
        subscription = make_active()
        services.renewed(subscription, paid_at=CYCLE_1_END, reference="a", bought=plan.ANNUAL)
        term_end = fresh(subscription).cycles.get(sequence=2).end_at
        services.renewal_failed(subscription, at=term_end)
        pending = fresh(subscription).cycles.get(sequence=3)
        self.assertEqual(
            (pending.settlement, pending.plan_code, pending.price_centavos, pending.term_months),
            (Settlement.PENDING, "annual", 2_499_000, 12),
        )

    def test_a_lapsed_cycle_holds_the_terms_that_were_due(self):
        subscription = make_active()
        services.sweep(now=CYCLE_1_END + days(8))
        lapsed = fresh(subscription).cycles.get(sequence=2)
        self.assertEqual(
            (lapsed.settlement, lapsed.plan_code, lapsed.price_centavos, lapsed.term_months),
            (Settlement.LAPSED, "monthly", 249_900, 1),
        )

    def test_a_recovery_replaces_the_terms_that_were_due_with_what_was_paid(self):
        subscription = make_active()
        services.renewed(subscription, paid_at=CYCLE_1_END, reference="a", bought=plan.ANNUAL)
        term_end = fresh(subscription).cycles.get(sequence=2).end_at
        services.renewal_failed(subscription, at=term_end)

        services.recovered(subscription, paid_at=term_end + days(2), reference="b")  # the same plan
        same = fresh(subscription).cycles.get(sequence=3)
        self.assertEqual((same.plan_code, same.term_months, same.end_at), (
            "annual", 12, manila(2028, 12, 25, 15, 0)))

    def test_a_recovery_paid_with_another_plan_takes_that_plan(self):
        subscription = make_attention()
        services.recovered(
            subscription, paid_at=CYCLE_1_END + days(2), reference="b", bought=plan.LAUNCH_PROMO
        )
        cycle = fresh(subscription).cycles.get(sequence=2)
        self.assertEqual(
            (cycle.plan_code, cycle.price_centavos, cycle.term_months, cycle.end_at),
            ("launch_promo", 499_900, 6, manila(2027, 6, 25, 15, 0)),
        )

    def test_a_reactivation_takes_the_plan_paid_for(self):
        subscription = make_active()
        services.sweep(now=CYCLE_1_END + days(8))
        services.reactivated(
            subscription, paid_at=CYCLE_1_END + days(9), reference="r", bought=plan.ANNUAL
        )
        cycle = fresh(subscription).cycles.get(sequence=3)
        self.assertEqual((cycle.plan_code, cycle.term_months), ("annual", 12))

    def test_the_cycle_check_refuses_nothing_the_services_write(self):
        # A full run through every kind of cycle the lifecycle writes (the constraint is on).
        subscription = make_attention()
        services.sweep(now=CYCLE_1_END + days(7))
        self.assertTrue(fresh(subscription).cycles.filter(settlement=Settlement.LAPSED).exists())


@override_settings(BILLING_ENABLED=True)
class VersionTests(TestCase):
    """A new price is a new version; a cycle keeps the one it was opened under."""

    def new_monthly(self):
        v2 = replace(plan.MONTHLY, version=2, price_centavos=299_900)
        versions = {**plan.VERSIONS, ("monthly", 2): v2}
        plans = {**plan.PLANS, "monthly": v2}
        return v2, mock.patch.multiple(plan, VERSIONS=versions, PLANS=plans)

    def test_a_new_version_does_not_re_price_an_old_cycle(self):
        subscription = make_active()
        v2, patched = self.new_monthly()
        with patched:
            cycle = fresh(subscription).cycles.get(sequence=1)
            self.assertEqual((cycle.plan_version, cycle.price_centavos), (1, 249_900))
            self.assertEqual(plan.version_of(cycle.plan_code, cycle.plan_version).price_centavos, 249_900)

    def test_the_renewal_is_at_the_version_sold_when_it_is_paid(self):
        subscription = make_active()
        self.assertEqual(services.renewal_terms(subscription)["amount_centavos"], 249_900)
        v2, patched = self.new_monthly()
        with patched:
            terms = services.renewal_terms(fresh(subscription))
            self.assertEqual((terms["version"], terms["amount_centavos"]), (2, 299_900))
            services.renewed(
                subscription, paid_at=CYCLE_1_END, reference="r2", bought=plan.PLANS["monthly"]
            )
        first, second = (fresh(subscription).cycles.get(sequence=n) for n in (1, 2))
        self.assertEqual(
            [(c.plan_version, c.price_centavos) for c in (first, second)],
            [(1, 249_900), (2, 299_900)],
        )


@override_settings(BILLING_ENABLED=True)
class RenewalTermsTests(TestCase):
    def test_nothing_is_due_before_a_payment(self):
        self.assertIsNone(services.renewal_terms(make_subscription()))

    def test_monthly_follows_monthly_on_the_due_date(self):
        subscription = make_active()
        terms = services.renewal_terms(subscription)
        self.assertEqual(
            (terms["plan"], terms["amount_centavos"], terms["months"], terms["due_at"]),
            ("monthly", 249_900, 1, CYCLE_1_END),
        )

    def test_the_promo_is_followed_by_monthly_at_the_exact_six_month_date(self):
        subscription = make_subscription()
        with method_on_file():
            services.first_payment_succeeded(
                subscription, paid_at=TRIAL_END, reference="p", bought=plan.LAUNCH_PROMO
            )
        terms = services.renewal_terms(subscription)
        self.assertEqual(
            (terms["plan"], terms["amount_centavos"], terms["due_at"]),
            ("monthly", 249_900, manila(2027, 5, 25, 15, 0)),
        )

    def test_annual_is_followed_by_annual(self):
        subscription = make_active()
        services.renewed(subscription, paid_at=CYCLE_1_END, reference="a", bought=plan.ANNUAL)
        terms = services.renewal_terms(subscription)
        self.assertEqual(
            (terms["plan"], terms["amount_centavos"], terms["months"]), ("annual", 2_499_000, 12)
        )


@override_settings(BILLING_ENABLED=True)
class TermRunningTests(TestCase):
    def test_a_paid_cycle_covers_its_own_span_only(self):
        subscription = make_active()
        self.assertFalse(services.term_running(subscription, TRIAL_END - days(1)))
        self.assertTrue(services.term_running(subscription, TRIAL_END))
        self.assertTrue(services.term_running(subscription, CYCLE_1_END - timedelta(seconds=1)))
        self.assertFalse(services.term_running(subscription, CYCLE_1_END))
