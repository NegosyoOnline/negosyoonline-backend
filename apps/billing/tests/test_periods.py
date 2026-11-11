"""Allowance periods and the earned figure (Billing Build B3, pull request 4). Nothing is
stored: B4 reads these."""
from datetime import date

from datetime import timedelta

from django.test import SimpleTestCase, TestCase, override_settings

from apps.billing import clock, plan
from apps.billing.earned import earned_centavos
from apps.billing.tests.base import make_active, manila
from apps.billing import services


def three_pm(*parts):
    return manila(*parts, 15, 0)


class PeriodTests(SimpleTestCase):
    def test_anchor_31_shortens_one_month_without_carrying_it_forward(self):
        periods = clock.allowance_periods(manila(2026, 12, 31, 10, 0), 31, 12, 12)
        ends = [end for _, end in periods]
        self.assertEqual(ends[1], three_pm(2027, 2, 28))
        self.assertEqual(ends[2], three_pm(2027, 3, 31))
        self.assertEqual(periods[0][0], manila(2026, 12, 31, 10, 0))

    def test_periods_tile_the_term_on_every_anchor_day_across_a_leap_year(self):
        for anchor in range(1, 32):
            for start in (manila(2027, 3, 1, 10, 0), manila(2028, 2, 1, 10, 0)):
                for months, periods in ((1, 1), (6, 6), (12, 12)):
                    with self.subTest(anchor=anchor, start=start, months=months):
                        rows = clock.allowance_periods(start, anchor, months, periods)
                        self.assertEqual(len(rows), periods)
                        self.assertEqual(rows[0][0], start)
                        self.assertEqual(rows[-1][1], clock.cycle_end(start, anchor, months))
                        for before, after in zip(rows, rows[1:]):
                            self.assertEqual(after[0], before[1])
                        for begins, ends in rows:
                            self.assertLess(begins, ends)

    def test_the_catalogue_counts(self):
        self.assertEqual(
            (plan.MONTHLY.allowance_periods, plan.ANNUAL.allowance_periods,
             plan.LAUNCH_PROMO.allowance_periods),
            (1, 12, 6),
        )


@override_settings(BILLING_ENABLED=True)
class PeriodAtTests(TestCase):
    def test_a_monthly_cycle_is_one_period_and_an_unpaid_instant_has_none(self):
        subscription = make_active()
        cycle = subscription.cycles.get(sequence=1)
        found = services.allowance_period_at(subscription, cycle.start_at)
        self.assertEqual(
            (found["cycle"].pk, found["index"], found["start_at"], found["end_at"]),
            (cycle.pk, 1, cycle.start_at, cycle.end_at),
        )
        self.assertIsNone(services.allowance_period_at(subscription, cycle.end_at))
        self.assertIsNone(services.allowance_period_at(subscription, cycle.start_at - timedelta(days=1)))


class EarnedTests(SimpleTestCase):
    START, END = date(2026, 11, 8), date(2027, 5, 8)

    def test_the_181_day_example(self):
        self.assertEqual((self.END - self.START).days, 181)
        earned = earned_centavos(499_900, self.START, self.END, date(2026, 12, 31))
        self.assertEqual((earned, 499_900 - earned), (149_141, 350_759))

    def test_nothing_before_the_start_and_everything_at_the_end(self):
        self.assertEqual(earned_centavos(499_900, self.START, self.END, date(2026, 11, 7)), 0)
        self.assertEqual(earned_centavos(499_900, self.START, self.END, self.END), 499_900)
        self.assertEqual(earned_centavos(499_900, self.START, self.END, date(2028, 1, 1)), 499_900)

    def test_it_never_goes_backwards_and_ends_exactly(self):
        last = -1
        for offset in range(0, 200):
            value = earned_centavos(499_900, self.START, self.END,
                                    date.fromordinal(self.START.toordinal() + offset))
            self.assertGreaterEqual(value, last)
            last = value
        self.assertEqual(last, 499_900)
