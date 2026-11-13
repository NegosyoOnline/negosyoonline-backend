"""Upload body cap, DEBUG default, the Redis deploy check and the `security` denial log."""
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from apps.accounts import codes
from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL
from apps.common import checks, middleware
from apps.sites.tests.api_base import SitesApiTestCase
from apps.storefront.tests.base import SurfaceTestCase

ROOT = Path(__file__).resolve().parents[3]
SECURITY = "security"


class RequestSizeCapTests(TestCase):
    def test_a_declared_body_above_the_cap_is_refused_before_it_is_read(self):
        too_big = middleware.MAX_REQUEST_BYTES + 1

        response = self.client.post(
            "/v1/anything/", data=b"x", content_type="application/octet-stream",
            secure=True, CONTENT_LENGTH=too_big,
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "payload_too_large")

    def test_a_body_at_the_cap_passes_the_middleware(self):
        response = self.client.post(
            "/v1/anything/", data=b"x", content_type="application/octet-stream",
            secure=True, CONTENT_LENGTH=middleware.MAX_REQUEST_BYTES,
        )

        self.assertNotEqual(response.status_code, 413)

    def test_the_cap_is_21_mib(self):
        self.assertEqual(middleware.MAX_REQUEST_BYTES, 21 * 1024 * 1024)


class DebugDefaultTests(SimpleTestCase):
    def test_debug_is_off_when_the_variable_is_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_DEBUG"}
        env["DJANGO_SECRET_KEY"] = "a-real-looking-secret-for-this-test"
        done = subprocess.run(
            [sys.executable, "-c", "from config import settings; print(settings.DEBUG)"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True,
        )

        self.assertEqual(done.stdout.strip(), "False")


class ThrottleCacheCheckTests(SimpleTestCase):
    def ids(self):
        return [error.id for error in checks.throttles_need_a_shared_cache(None)]

    def test_debug_off_without_redis_is_an_error(self):
        with override_settings(DEBUG=False, REDIS_URL=None):
            self.assertEqual(self.ids(), ["common.E001"])

    def test_debug_off_with_redis_is_clean(self):
        with override_settings(DEBUG=False, REDIS_URL="redis://127.0.0.1:6379/0"):
            self.assertEqual(self.ids(), [])

    def test_debug_on_without_redis_is_clean(self):
        with override_settings(DEBUG=True, REDIS_URL=None):
            self.assertEqual(self.ids(), [])


class SecurityLogTests(SitesApiTestCase):
    """Each denial writes one line, and the line holds no email, mobile or id."""

    def assert_one_line(self, logs, *, event, reason, authenticated, forbidden=()):
        self.assertEqual(len(logs.output), 1, logs.output)
        line = logs.output[0]
        self.assertIn(f"event={event} ", line)
        self.assertIn(f"reason={reason} ", line)
        self.assertIn(f"authenticated={authenticated}", line)
        for text in ("example.test", "@", *forbidden):
            self.assertNotIn(text, line)
        return line

    def test_a_foreign_site_logs_site_not_found_foreign(self):
        site = self.create_site()
        ben = self.other_client()

        with self.assertLogs(SECURITY, level="WARNING") as logs:
            response = ben.get(f"/v1/sites/{site['id']}/billing/", secure=True)

        self.assertEqual(response.status_code, 404)
        line = self.assert_one_line(
            logs, event="site_not_found", reason="foreign", authenticated="yes",
            forbidden=(site["id"], str(self.account.public_id)),
        )
        self.assertIn("route=v1/sites/<site_id>/billing/", line)
        self.assertIn("method=GET", line)

    def test_a_missing_site_logs_reason_missing(self):
        with self.assertLogs(SECURITY, level="WARNING") as logs:
            self.get("/v1/sites/ste_doesnotexist/billing/")

        self.assert_one_line(logs, event="site_not_found", reason="missing", authenticated="yes")

    def test_a_deleted_site_logs_reason_deleted(self):
        from django.utils import timezone

        from apps.sites.models import Site

        site = self.create_site()
        Site.objects.filter(public_id=site["id"]).update(deleted_at=timezone.now())

        with self.assertLogs(SECURITY, level="WARNING") as logs:
            self.get(f"/v1/sites/{site['id']}/billing/")

        self.assert_one_line(
            logs, event="site_not_found", reason="deleted", authenticated="yes",
            forbidden=(site["id"],),
        )

    def test_an_object_that_is_not_there_logs_object_not_found(self):
        site = self.create_site()

        with self.assertLogs(SECURITY, level="WARNING") as logs:
            response = self.get(f"/v1/sites/{site['id']}/billing/")

        self.assertEqual(response.status_code, 404)
        self.assert_one_line(
            logs, event="object_not_found", reason="missing", authenticated="yes",
            forbidden=(site["id"],),
        )

    def test_a_refused_code_logs_code_refused_without_the_email_or_code(self):
        self.client.logout()
        cache.clear()
        self.post("/v1/auth/email-code/request/", {"email": "zed@example.test"})
        real = self.last_code()
        wrong = "000000" if real != "000000" else "111111"

        with self.assertLogs(SECURITY, level="WARNING") as logs:
            response = self.post(
                "/v1/auth/email-code/verify/",
                {"email": "zed@example.test", "code": wrong, "accept_policies": True},
            )

        self.assertEqual(response.json()["error"]["code"], "invalid_code")
        self.assert_one_line(
            logs, event="code_refused", reason="missing", authenticated="no",
            forbidden=("zed", real, wrong),
        )

    def test_a_locked_code_logs_code_locked_and_answers_the_same(self):
        cache.clear()
        self.post("/v1/auth/email-code/request/", {"email": "zed@example.test"})
        real = self.last_code()
        wrong = "000000" if real != "000000" else "111111"
        body = {"email": "zed@example.test", "code": wrong, "accept_policies": True}

        with self.assertLogs(SECURITY, level="WARNING"):
            for _ in range(codes.MAX_ATTEMPTS):
                self.post("/v1/auth/email-code/verify/", body)
        with self.assertLogs(SECURITY, level="WARNING") as logs:
            response = self.post("/v1/auth/email-code/verify/", body)

        self.assertEqual(response.json()["error"]["code"], "invalid_code")
        self.assert_one_line(
            logs, event="code_locked", reason="missing", authenticated="no",
            forbidden=("zed", real, wrong),
        )

    def test_the_security_logger_goes_to_the_console_only(self):
        config = settings.LOGGING["loggers"]["security"]

        self.assertEqual(config["handlers"], ["security_console"])
        self.assertFalse(config["propagate"])


class StorefrontDenialLogTests(SurfaceTestCase):
    def test_an_unknown_host_logs_site_not_found_for_a_signed_out_caller(self):
        with self.assertLogs(SECURITY, level="WARNING") as logs:
            response = self.call("nobody.negosyotest.com")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(logs.output), 1, logs.output)
        line = logs.output[0]
        self.assertIn("event=site_not_found ", line)
        self.assertIn("authenticated=no", line)
        self.assertIn("route=storefront/v1/site/", line)
        self.assertNotIn("nobody", line)
        self.assertNotIn("203.0.113.7", line)
