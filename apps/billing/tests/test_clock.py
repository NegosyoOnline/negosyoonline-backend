from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from apps.billing import clock
from apps.billing.tests.base import MANILA, manila


def three_pm(year, month, day):
    return manila(year, month, day, 15, 0)


class CycleEndTests(SimpleTestCase):
    def chain(self, start, anchor, count):
        ends = []
        for _ in range(count):
            start = clock.cycle_end(start, anchor)
            ends.append(start)
        return ends

    def test_anchor_31_shortens_in_short_months_and_does_not_carry_forward(self):
        self.assertEqual(
            self.chain(manila(2027, 1, 31, 15, 0), 31, 4),
            [three_pm(2027, 2, 28), three_pm(2027, 3, 31), three_pm(2027, 4, 30),
             three_pm(2027, 5, 31)],
        )

    def test_anchor_31_in_a_leap_year(self):
        self.assertEqual(clock.cycle_end(manila(2028, 1, 31, 15, 0), 31), three_pm(2028, 2, 29))

    def test_anchor_29_in_a_normal_year(self):
        self.assertEqual(
            self.chain(manila(2027, 1, 29, 15, 0), 29, 2),
            [three_pm(2027, 2, 28), three_pm(2027, 3, 29)],
        )

    def test_anchor_29_from_a_leap_day(self):
        self.assertEqual(clock.cycle_end(manila(2028, 2, 29, 15, 0), 29), three_pm(2028, 3, 29))

    def test_anchor_30_across_february(self):
        self.assertEqual(
            self.chain(manila(2027, 1, 30, 15, 0), 30, 2),
            [three_pm(2027, 2, 28), three_pm(2027, 3, 30)],
        )
        self.assertEqual(
            self.chain(manila(2028, 1, 30, 15, 0), 30, 2),
            [three_pm(2028, 2, 29), three_pm(2028, 3, 30)],
        )

    def test_december_rolls_into_january(self):
        self.assertEqual(clock.cycle_end(manila(2026, 12, 25, 15, 0), 25), three_pm(2027, 1, 25))

    def test_the_month_and_the_anchor_are_read_in_manila_not_utc(self):
        start = datetime(2027, 3, 31, 23, 30, tzinfo=timezone.utc)  # 1 April, 7:30 AM in Manila

        self.assertEqual(clock.anchor_day(start), 1)
        self.assertEqual(clock.cycle_end(start, 1), three_pm(2027, 5, 1))

    def test_a_start_before_at_and_after_3pm_all_end_at_3pm_on_the_anchor_day(self):
        for hour in (10, 15, 20):
            with self.subTest(hour=hour):
                end = clock.cycle_end(manila(2026, 11, 25, hour, 0), 25)
                self.assertEqual(end, three_pm(2026, 12, 25))

    def test_every_end_is_3pm_manila_and_carries_its_time_zone(self):
        end = clock.cycle_end(manila(2026, 11, 25, 10, 0), 25)

        self.assertIs(end.tzinfo, MANILA)
        self.assertEqual((end.hour, end.minute, end.second, end.microsecond), (15, 0, 0, 0))
        self.assertEqual(end.utcoffset(), timedelta(hours=8))


class TrialEndTests(SimpleTestCase):
    def test_the_trial_ends_exactly_14_days_after_it_starts_whatever_the_hour(self):
        for start in (manila(2026, 11, 11, 10, 0, 7), manila(2026, 11, 11, 23, 59, 59),
                      datetime(2027, 2, 15, 1, 2, 3, tzinfo=timezone.utc)):
            with self.subTest(start=start):
                self.assertEqual(clock.trial_end(start), start + timedelta(days=14))
