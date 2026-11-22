from django.conf import settings
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


@override_settings(SECURE_SSL_REDIRECT=True)
class HealthCheckHttpsRedirectTests(TestCase):
    """The platform health check calls /healthz/ over plain HTTP. Only that path skips the redirect."""

    def test_healthz_answers_over_plain_http(self):
        response = self.client.get("/healthz/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_healthz_answers_over_https(self):
        self.assertEqual(self.client.get("/healthz/", secure=True).status_code, 200)

    def test_other_paths_still_redirect_plain_http(self):
        for path in ("/healthz", "/healthz/x", "/v1/healthz/", "/v1/account"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 301, path)
            self.assertEqual(response["Location"], f"https://testserver{path}")

    @override_settings(ALLOWED_HOSTS=["healthcheck.railway.app"])
    def test_healthz_answers_the_railway_health_check_host(self):
        response = self.client.get("/healthz/", HTTP_HOST="healthcheck.railway.app")

        self.assertEqual(response.status_code, 200)

    @override_settings(ALLOWED_HOSTS=["api.example.test"])
    def test_healthz_refuses_a_host_that_is_not_allowed(self):
        response = self.client.get("/healthz/", HTTP_HOST="healthcheck.railway.app")

        self.assertEqual(response.status_code, 400)

    def test_only_the_exact_healthz_path_is_exempt(self):
        self.assertEqual(settings.SECURE_REDIRECT_EXEMPT, [r"^healthz/$"])
