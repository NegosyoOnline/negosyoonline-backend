from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


class HealthCheckTests(TestCase):
    @override_settings(RELEASE_COMMIT="0123456789ab")
    def test_healthz_returns_ok_with_commit(self):
        response = self.client.get(reverse("healthz"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content, {"status": "ok", "commit": "0123456789ab"}
        )

    @override_settings(RELEASE_COMMIT="unknown")
    def test_healthz_reports_unknown_commit_when_unset(self):
        response = self.client.get(reverse("healthz"), secure=True)

        self.assertJSONEqual(response.content, {"status": "ok", "commit": "unknown"})

    def test_default_cache_round_trip(self):
        cache_key = "health-test-cache-key"

        cache.set(cache_key, "ok", timeout=30)

        self.assertEqual(cache.get(cache_key), "ok")
