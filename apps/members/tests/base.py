"""Helpers shared by the member tests."""
import re

from unittest import mock

from django.core import mail
from django.test import override_settings

from apps.billing.tests.base import make_subscription
from apps.members import services, signin
from apps.storefront.tests.base import HOST, SECRET, SurfaceTestCase

OTHER_HOST = "bens-store.negosyotest.com"
BASE = "/storefront/v1/member/"
VISITOR = "203.0.113.50"
EMAIL, MOBILE = "maria@buyer.test", "09171234567"
NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
INVALID_CODE = {"error": {"code": "invalid_code", "message": "That code is wrong or has expired."}}
NOT_SIGNED_IN = {"error": {"code": "member_session_invalid", "message": "Not signed in."}}


@override_settings(MEMBERS_ENABLED=True)
class MembersTestCase(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        sleeper = mock.patch.object(signin, "_sleep")  # the response-time floor is tested alone
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.site = make_subscription().site
        self.other_site = make_subscription(email="ben@example.test", name="bens-store").site
        self.member = self.new_member()

    def new_member(self, site=None, email=EMAIL, mobile=MOBILE, name="Maria Santos"):
        return services.create_member(
            site or self.site, full_name=name, mobile=mobile, email=email
        )

    def post(self, path, host=HOST, secret=SECRET, ip=VISITOR, **fields):
        body = {"host": host, **fields}
        if ip is not None:
            body["visitor_ip"] = ip
        headers = {"HTTP_X_REAL_IP": "198.51.100.200"}
        if secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return self.client.post(
            BASE + path, body, content_type="application/json", secure=True, **headers
        )

    def request_code(self, email=EMAIL, host=HOST, **extra):
        return self.post("signin/request/", host=host, email=email, **extra)

    def last_code(self):
        found = re.search(r"code is (\d{6})", mail.outbox[-1].body)
        return found.group(1)

    def sign_in(self, email=EMAIL, host=HOST):
        """Request and verify a code. Returns the raw session token."""
        self.assertEqual(self.request_code(email, host).status_code, 200)
        response = self.post("signin/verify/", host=host, email=email, code=self.last_code())
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["token"]
