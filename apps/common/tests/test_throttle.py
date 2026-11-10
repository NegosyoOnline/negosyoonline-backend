from django.core.cache import cache
from django.test import SimpleTestCase

from apps.common.errors import ApiError
from apps.common.throttle import hit


class ThrottleTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_the_call_at_the_limit_passes_and_the_next_is_429(self):
        for _ in range(3):
            hit("test", "a", limit=3, window=60)

        with self.assertRaises(ApiError) as raised:
            hit("test", "a", limit=3, window=60)

        self.assertEqual(raised.exception.code, "rate_limited")
        self.assertEqual(raised.exception.status, 429)

    def test_keys_and_scopes_are_counted_separately(self):
        hit("test", "a", limit=1, window=60)

        hit("test", "b", limit=1, window=60)
        hit("other", "a", limit=1, window=60)
