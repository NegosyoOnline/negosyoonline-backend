from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


class HealthCheckTests(TestCase):
    def test_healthz_returns_ok(self):
        response = self.client.get(reverse("healthz"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"status": "ok"})

    def test_default_cache_round_trip(self):
        cache_key = "health-test-cache-key"

        cache.set(cache_key, "ok", timeout=30)

        self.assertEqual(cache.get(cache_key), "ok")
