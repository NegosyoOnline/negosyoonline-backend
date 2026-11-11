"""Helpers shared by the storefront surface tests. The billing clock is set by each test."""
import secrets
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.billing.tests.base import T0, days

PATH = "/storefront/v1/site/"
HOST = "anas-store.negosyotest.com"
SECRET = secrets.token_urlsafe(32)  # made for this test run; never a real value
NO_SUCH_ENDPOINT = {"error": {"code": "not_found", "message": "No such endpoint."}}
NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
UNAUTHENTICATED = {"error": {"code": "unauthenticated", "message": "Unknown caller."}}


@override_settings(
    BILLING_ENABLED=True, SITE_PARENT_HOST="negosyotest.com", STOREFRONT_ENABLED=True,
    STOREFRONT_SERVER_SECRET=SECRET,
)
class SurfaceTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = mock.patch("apps.billing.services.timezone")
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.at(T0 + days(1))

    def at(self, moment):
        self.clock.now.return_value = moment
        return moment

    def call(self, host=HOST, *, secret=SECRET, authorization=None, path=PATH,
             ip="203.0.113.7", method="get"):
        headers = {"HTTP_X_REAL_IP": ip}
        if authorization is not None:
            headers["HTTP_AUTHORIZATION"] = authorization
        elif secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        data = {} if host is None else {"host": host}
        return getattr(self.client, method)(path, data, secure=True, **headers)

    def assert_answer(self, response, status, body):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json(), body)
        self.assertEqual(response["Cache-Control"], "no-store")
        return response.content
