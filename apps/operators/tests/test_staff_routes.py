"""The staff routes under /staff/v1/ on the hq host: off by default, one origin, today's cookies."""
import json
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from apps.operators import checks
from apps.operators.models import AdminSession, OperatorAuditEntry
from apps.operators.tests.base import EMAIL, HQ, OTHER
from apps.operators.tests.test_hq import PRE, SESSION, HqTestCase

STAFF = "https://hq-app.example.test"
PATHS = (
    ("POST", "/staff/v1/sign-in/"),
    ("POST", "/staff/v1/sign-in/code/"),
    ("POST", "/staff/v1/sign-in/verify/"),
    ("POST", "/staff/v1/sign-out/"),
    ("GET", "/staff/v1/me/"),
    ("GET", "/staff/v1/console/"),
)
BAD_ORIGINS = (
    "hq-app.example.test",                    # no scheme
    "http://hq-app.example.test",             # not https
    "https://hq-app.example.test/",           # a trailing slash
    "https://hq-app.example.test:8443",       # a port
    "https://hq-app.example.test/sign-in",    # a path
    "https://HQ-APP.example.test",            # upper case
    "https://user@hq-app.example.test",       # a user
    "https://*.example.test",                 # a pattern
    "https://hq.example.test",                # the hq host itself
    "https://hq-app.vercel.app",              # another parent: the cookie would not be sent
    "https://app.hq.example.test",            # not a sibling of the hq host
    "https://dash.example.test",              # the dashboard
    "https://www.example.test",               # the public site
    "https://extra.example.test",             # an API origin
)
on_staff = override_settings(HQ_STAFF_ORIGIN=STAFF)


class StaffOriginCheckTests(HqTestCase):
    @on_staff
    def test_a_good_origin_passes_the_check(self):
        self.assertEqual(checks.staff_origin_problem(), "")
        self.assertEqual(checks.active_staff_origin(), STAFF)
        self.assertEqual(checks.check_staff_origin(None), [])

    def test_an_empty_origin_is_off_and_is_not_an_error(self):
        self.assertEqual(checks.active_staff_origin(), "")
        self.assertEqual(checks.check_staff_origin(None), [])

    @override_settings(
        DASHBOARD_URL="https://dash.example.test", SITE_URL="https://www.example.test",
        API_ALLOWED_ORIGINS=["https://dash.example.test", "https://extra.example.test"],
    )
    def test_a_bad_origin_is_refused_and_the_routes_stay_off(self):
        for value in BAD_ORIGINS:
            cache.clear()
            with self.subTest(value), override_settings(HQ_STAFF_ORIGIN=value):
                errors = checks.check_staff_origin(None)
                self.assertEqual([e.id for e in errors], ["operators.E002"])
                with self.assertLogs("apps.operators.checks", "ERROR") as logs:
                    self.assertEqual(checks.active_staff_origin(), "")
                self.assertIn("hq-staff-origin-refused:", logs.output[0])
                # Said once an hour, not on every request.
                with self.assertNoLogs("apps.operators.checks", "ERROR"):
                    self.assertEqual(checks.active_staff_origin(), "")

    @override_settings(HQ_HOST="", HQ_STAFF_ORIGIN=STAFF)
    def test_an_origin_without_an_hq_host_is_refused(self):
        self.assertEqual([e.id for e in checks.check_staff_origin(None)], ["operators.E002"])

    @override_settings(SITE_PARENT_HOST="example.test", HQ_STAFF_ORIGIN="https://tindahan.example.test")
    def test_an_origin_a_subscriber_site_could_take_is_refused(self):
        # hq.example.test itself is reserved, so HQ_HOST passes; "tindahan" is a name anyone can take.
        self.assertEqual([e.id for e in checks.check_staff_origin(None)], ["operators.E002"])

    @override_settings(DEBUG=True, HQ_STAFF_ORIGIN="http://hq-app.example.test:3002")
    def test_local_development_may_use_http_and_a_port(self):
        self.assertEqual(checks.staff_origin_problem(), "")
