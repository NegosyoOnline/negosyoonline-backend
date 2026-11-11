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


class StaffTestCase(HqTestCase):
    def call(self, method, path, data=None, origin=STAFF, client=None, host=HQ):
        extra = {"secure": True, "HTTP_HOST": host}
        if origin:
            extra["HTTP_ORIGIN"] = origin
        client = client or self.client
        if method == "GET":
            return client.get(path, **extra)
        if method == "OPTIONS":
            return client.options(path, HTTP_ACCESS_CONTROL_REQUEST_METHOD=data, **extra)
        return client.post(
            path, data=json.dumps(data or {}), content_type="application/json", **extra
        )

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)

    def staff_email_step(self, email=EMAIL):
        self.assertEqual(self.call("POST", "/staff/v1/sign-in/", {"email": email}).status_code, 204)
        response = self.call("POST", "/staff/v1/sign-in/code/", {"code": self.emailed_code()})
        self.assertEqual(response.status_code, 204)
        return response

    def staff_sign_in(self, email=EMAIL):
        self.staff_email_step(email)
        response = self.call("POST", "/staff/v1/sign-in/verify/", {"code": self.code()})
        self.assertEqual(response.status_code, 204)
        return response


class StaffRoutesOffTests(StaffTestCase):
    def test_unset_every_staff_path_answers_as_an_unknown_path_does_today(self):
        self.enrolled()
        # The second client checks the form token as a browser's request is checked.
        strict = Client(enforce_csrf_checks=True)
        for method, path in PATHS:
            for origin in (None, STAFF):
                with self.subTest(path=path, origin=origin):
                    got = self.call("GET", path, origin=origin)
                    want = self.call("GET", "/no-such-page/", origin=origin)
                    self.assertEqual((got.status_code, got.content), (404, want.content))
                    self.assertEqual(sorted(got.headers.items()), sorted(want.headers.items()))
                    got = self.call("POST", path, {"email": EMAIL}, origin=origin, client=strict)
                    want = self.call(
                        "POST", "/no-such-page/", {"email": EMAIL}, origin=origin, client=strict
                    )
                    self.assertEqual((got.status_code, got.content), (403, want.content))
                    self.assertNotIn("Access-Control-Allow-Origin", got.headers)
                    self.assertNotIn(PRE, got.cookies)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(OperatorAuditEntry.objects.exists())

    @override_settings(HQ_STAFF_ORIGIN="https://hq-app.vercel.app")
    def test_a_refused_value_leaves_the_routes_off(self):
        got = self.call("GET", "/staff/v1/me/", origin="https://hq-app.vercel.app")
        self.assertEqual((got.status_code, got.content), (404, self.get("/no-such-page/").content))

    @on_staff
    @override_settings(HQ_HOST="")
    def test_with_no_hq_host_the_routes_do_not_exist(self):
        self.assertEqual(self.call("GET", "/staff/v1/me/", host=OTHER).status_code, 404)

    @on_staff
    def test_other_hosts_serve_no_staff_route(self):
        for method, path in PATHS:
            response = self.call(method, path, host=OTHER)
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn("Access-Control-Allow-Origin", response.headers)


@on_staff
@override_settings(DASHBOARD_URL="https://dash.example.test")
class StaffOriginTests(StaffTestCase):
    def test_any_other_origin_is_refused_on_every_route(self):
        self.enrolled()
        others = (
            None, "https://dash.example.test", "https://hq-app-git-x.vercel.app",
            f"https://{HQ}", STAFF + "/", "null",
        )
        for method, path in PATHS:
            for origin in others:
                for verb in (method, "OPTIONS"):
                    with self.subTest(path=path, origin=origin, verb=verb):
                        data = method if verb == "OPTIONS" else {"email": EMAIL}
                        response = self.call(verb, path, data, origin=origin)
                        self.assertError(response, 403, "origin_not_allowed")
                        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
                        self.assertNotIn("Access-Control-Allow-Credentials", response.headers)
                        self.assertEqual(len(response.cookies), 0)
                        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(mail.outbox, [])
        self.assertFalse(OperatorAuditEntry.objects.exists())

    def test_another_origin_cannot_tell_a_route_from_no_route(self):
        there = self.call("GET", "/staff/v1/me/", origin="https://dash.example.test")
        absent = self.call("GET", "/staff/v1/nothing/", origin="https://dash.example.test")
        self.assertEqual((there.status_code, there.content), (absent.status_code, absent.content))

    def test_a_preflight_from_the_staff_origin_is_answered(self):
        for method, path in PATHS:
            response = self.call("OPTIONS", path, method)
            self.assertEqual(response.status_code, 204, path)
            self.assertEqual(response["Access-Control-Allow-Origin"], STAFF)
            self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
            self.assertEqual(response["Access-Control-Allow-Methods"], f"{method}, OPTIONS")
            self.assertEqual(response["Access-Control-Allow-Headers"], "content-type")
            self.assertIn("Origin", response["Vary"])

    def test_an_unknown_path_and_a_wrong_method(self):
        self.assertError(self.call("GET", "/staff/v1/nothing/"), 404, "not_found")
        self.assertError(self.call("GET", "/staff/v1/sign-in/"), 405, "method_not_allowed")
        self.assertError(self.call("POST", "/staff/v1/me/"), 405, "method_not_allowed")
        self.assertError(self.call("GET", "/staff/v1/me"), 404, "not_found")

    def test_every_answer_is_uncached_unindexed_and_names_the_origin(self):
        response = self.call("GET", "/staff/v1/me/")
        self.assertError(response, 401, "unauthenticated")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertEqual(response["Access-Control-Allow-Origin"], STAFF)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_every_staff_answer_may_not_be_framed_or_sniffed(self):
        # X-Frame-Options is set by dispatch(): XFrameOptionsMiddleware is skipped.
        # X-Content-Type-Options comes from SecurityMiddleware, which still runs.
        answers = [
            self.call("GET", "/staff/v1/me/"),                                  # 401
            self.call("POST", "/staff/v1/sign-in/", {"email": "nobody@example.test"}),  # 204
            self.call("OPTIONS", "/staff/v1/sign-in/", "POST"),                 # preflight
            self.call("GET", "/staff/v1/nothing/"),                             # 404
            self.call("GET", "/staff/v1/me/", origin="https://dash.example.test"),  # 403
            self.call("GET", "/staff/v1/me/", origin=None),                     # 403
        ]
        with mock.patch("apps.operators.staff_views.hq_views.load_session", side_effect=RuntimeError):
            with self.assertLogs("apps.operators.staff_views", "ERROR"):
                answers.append(self.call("GET", "/staff/v1/me/"))               # 500
        for response in answers:
            with self.subTest(status=response.status_code):
                self.assertEqual(response["X-Frame-Options"], "DENY")
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_a_failing_route_answers_a_plain_json_error(self):
        with mock.patch("apps.operators.staff_views.hq_views.load_session", side_effect=RuntimeError):
            with self.assertLogs("apps.operators.staff_views", "ERROR"):
                response = self.call("GET", "/staff/v1/me/")
        self.assertError(response, 500, "server_error")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Access-Control-Allow-Origin"], STAFF)

    def test_the_api_prefix_is_still_not_found_on_the_hq_host(self):
        self.assertEqual(self.call("GET", "/v1/account/").status_code, 404)

    def test_health_is_untouched(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
        with override_settings(ALLOWED_HOSTS=["healthcheck.railway.app", HQ]):
            response = self.client.get("/healthz/", HTTP_HOST="healthcheck.railway.app")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_health_over_plain_http_is_untouched(self):
        from django.conf import settings

        self.assertEqual(settings.SECURE_REDIRECT_EXEMPT, [r"^healthz/$"])
        hosts = ["healthcheck.railway.app", HQ, "testserver"]
        with override_settings(SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=hosts):
            # Railway's check: plain http, its own host name. Answered, not redirected.
            response = self.client.get("/healthz/", HTTP_HOST="healthcheck.railway.app")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ok")
            # A staff path over plain http is still redirected, like every other path.
            self.assertEqual(self.client.get("/staff/v1/me/", HTTP_HOST=HQ).status_code, 301)

    def test_the_staff_origin_changes_no_allowed_host(self):
        from pathlib import Path

        from django.conf import settings

        # Read as text: the settings module is never loaded a second time inside the suite.
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text()
        code = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
        self.assertEqual(
            [line for line in code if "HQ_STAFF_ORIGIN" in line],
            ['HQ_STAFF_ORIGIN = os.environ.get("HQ_STAFF_ORIGIN", "").strip()'],
        )
        self.assertEqual(
            [line for line in code if "SECURE_REDIRECT_EXEMPT" in line],
            ['SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]'],
        )
        # Under this class's override the staff host is still not a host the service answers on.
        self.assertNotIn("hq-app.example.test", settings.ALLOWED_HOSTS)
