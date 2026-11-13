"""The per-connection limiter: shared through the cache, a permit per slot, a bounded wait."""
from django.core.cache import cache
from django.test import SimpleTestCase

from apps.inquiries import limiter


class Clock:
    def __init__(self, start=1000.0):
        self.t = start
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class LimiterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.clock = Clock()

    def acquire(self, pk):
        return limiter.acquire(pk, now=self.clock.now, sleep=self.clock.sleep)

    def test_callers_of_one_connection_share_permits_and_are_spaced(self):
        """Two workers asking at the same instant are given different slots."""
        starts = []
        for _ in range(4):  # these could be any mix of workers: the counter is in the cache
            self.assertTrue(self.acquire(1))
            starts.append(self.clock.now())
        gaps = [round(b - a, 2) for a, b in zip(starts, starts[1:])]
        self.assertEqual(gaps, [0.25, 0.25, 0.25])

    def test_callers_at_the_same_instant_get_five_slots_then_are_refused(self):
        """A frozen clock is many workers asking at once: the wait is bounded, then it says no."""
        frozen = Clock()
        frozen.sleep = lambda seconds: frozen.slept.append(seconds)  # time does not move
        results = [limiter.acquire(1, now=frozen.now, sleep=frozen.sleep) for _ in range(8)]
        self.assertEqual(results, [True] * 5 + [False] * 3)
        self.assertLessEqual(max(frozen.slept), limiter.MAX_WAIT)

    def test_one_connections_permits_do_not_use_anothers(self):
        frozen = Clock()
        frozen.sleep = lambda seconds: frozen.slept.append(seconds)
        for _ in range(8):
            limiter.acquire(1, now=frozen.now, sleep=frozen.sleep)
        frozen.slept.clear()
        self.assertTrue(limiter.acquire(2, now=frozen.now, sleep=frozen.sleep))
        self.assertEqual(frozen.slept, [])  # connection 2's first slot was free at once

    def test_a_connection_sends_at_most_the_rate_in_a_sustained_second(self):
        granted = []
        for _ in range(12):
            if self.acquire(7):
                granted.append(self.clock.now())
        window = [t for t in granted if t < granted[0] + 1.0]
        self.assertLessEqual(len(window), limiter.RATE)
