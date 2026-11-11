"""The switch and the server credential of the storefront surface."""
import logging

from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings

from apps.billing.tests.base import make_subscription
from apps.storefront import auth
from apps.storefront.tests.base import (
    HOST, NO_SUCH_ENDPOINT, NOT_FOUND, PATH, SECRET, UNAUTHENTICATED, SurfaceTestCase,
)

PATHS = (PATH, "/storefront/v1/", "/storefront/v1/nothing-here/", "/storefront/v1/site")
METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


class SwitchTests(SurfaceTestCase):
    def test_off_every_path_and_method_answers_404_with_or_without_the_secret(self):
        make_subscription()

        with override_settings(STOREFRONT_ENABLED=False):
            for path in PATHS:
                for method in METHODS:
                    for secret in (SECRET, None, "wrong"):
                        with self.subTest(path=path, method=method, secret=secret is SECRET):
                            response = self.call(path=path, method=method, secret=secret)
                            self.assertEqual(response.status_code, 404)
                            self.assertEqual(response["Cache-Control"], "no-store")
                            if method != "head":
                                self.assertEqual(response.json(), NO_SUCH_ENDPOINT)

    def test_off_is_the_default(self):
        import config.settings as defaults  # the values read from this process's environment

        with override_settings(
            STOREFRONT_ENABLED=defaults.STOREFRONT_ENABLED,
            STOREFRONT_SERVER_SECRET=defaults.STOREFRONT_SERVER_SECRET,
        ):
            self.assertFalse(defaults.STOREFRONT_ENABLED)
            self.assertFalse(auth.surface_on())

    def test_a_missing_or_short_secret_keeps_the_surface_off(self):
        make_subscription()

        for value in ("", "x" * (auth.SECRET_MIN_LENGTH - 1)):
            with self.subTest(length=len(value)), override_settings(
                STOREFRONT_SERVER_SECRET=value
            ):
                for secret in (value, None, SECRET):
                    self.assert_answer(self.call(secret=secret), 404, NO_SUCH_ENDPOINT)

    def test_off_no_failed_try_is_counted(self):
        with override_settings(STOREFRONT_ENABLED=False):
            for _ in range(auth.FAILED_TRIES + 2):
                self.assert_answer(self.call(secret="wrong"), 404, NO_SUCH_ENDPOINT)

        self.assert_answer(self.call(secret="wrong"), 401, UNAUTHENTICATED)


class CredentialTests(SurfaceTestCase):
    def test_the_right_secret_reaches_the_route(self):
        make_subscription()

        self.assertEqual(self.call().status_code, 200)

    def test_missing_malformed_and_wrong_get_the_same_401(self):
        make_subscription()
        tries = (
            None, "", SECRET, f"bearer {SECRET}", f"Basic {SECRET}", f"Token {SECRET}",
            "Bearer", "Bearer ", "Bearer wrong", f"Bearer {SECRET}x", f"Bearer {SECRET[:-1]}",
            f"Bearer  {SECRET}", f"Bearer {SECRET} extra", f"Bearer {SECRET.swapcase()}",
            "Bearer " + "é" * 40,
        )

        answers = set()
        for number, authorization in enumerate(tries):
            with self.subTest(number=number):
                response = self.call(
                    secret=None, authorization=authorization, ip=f"198.51.100.{number}"
                )
                answers.add(self.assert_answer(response, 401, UNAUTHENTICATED))
        self.assertEqual(len(answers), 1)

    def test_the_secret_in_the_query_or_a_cookie_is_not_a_credential(self):
        make_subscription()
        self.client.cookies["Authorization"] = f"Bearer {SECRET}"

        response = self.client.get(
            PATH, {"host": HOST, "secret": SECRET, "authorization": f"Bearer {SECRET}"},
            secure=True,
        )

        self.assert_answer(response, 401, UNAUTHENTICATED)

    def test_every_path_and_method_needs_the_secret(self):
        make_subscription()

        for number, path in enumerate(PATHS):
            for method in METHODS:
                with self.subTest(path=path, method=method):
                    response = self.call(
                        path=path, method=method, secret=None, ip=f"198.51.100.{number}"
                    )
                    self.assertEqual(response.status_code, 401)

    def test_with_the_secret_an_unknown_path_is_404_and_a_wrong_method_405(self):
        for path in PATHS[1:]:
            with self.subTest(path=path):
                self.assert_answer(self.call(path=path), 404, NO_SUCH_ENDPOINT)

        response = self.call(method="post")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_a_write_needs_no_csrf_token_and_no_origin(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        response = client.post(
            PATH, secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}",
            HTTP_ORIGIN="https://anas-store.negosyotest.com",
        )

        self.assertEqual(response.status_code, 405)  # reached the route, not a CSRF page

    def test_a_browser_origin_gets_no_cors_headers(self):
        make_subscription()

        for origin in ("http://localhost:3000", "https://anas-store.negosyotest.com"):
            with self.subTest(origin=origin):
                response = self.client.get(
                    PATH, {"host": HOST}, secure=True, HTTP_ORIGIN=origin,
                    HTTP_AUTHORIZATION=f"Bearer {SECRET}",
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Access-Control-Allow-Origin", response)
                self.assertNotIn("Access-Control-Allow-Credentials", response)

    def test_the_surface_is_not_in_the_published_api(self):
        document = self.client.get("/v1/openapi.json", secure=True).json()

        self.assertFalse([path for path in document["paths"] if "storefront" in path])
        for path in ("/storefront/v1/openapi.json", "/storefront/v1/docs"):
            self.assert_answer(self.call(path=path), 404, NO_SUCH_ENDPOINT)

    def test_the_subscriber_api_does_not_take_the_secret(self):
        response = self.client.get(
            "/v1/account/", secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}"
        )

        self.assertEqual(response.status_code, 401)


class LimitTests(SurfaceTestCase):
    def test_bad_credentials_from_one_address_are_limited(self):
        for _ in range(auth.FAILED_TRIES):
            self.assert_answer(self.call(secret="wrong"), 401, UNAUTHENTICATED)

        response = self.call(secret="wrong")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")
        self.assertEqual(self.call(secret=None).status_code, 429)

    def test_another_address_is_not_limited_by_it(self):
        for _ in range(auth.FAILED_TRIES + 1):
            self.call(secret="wrong")

        self.assert_answer(self.call(secret="wrong", ip="203.0.113.8"), 401, UNAUTHENTICATED)

    def test_the_right_secret_is_never_limited_and_never_counted(self):
        make_subscription()
        for _ in range(auth.FAILED_TRIES + 5):
            self.call(secret="wrong")

        for _ in range(auth.FAILED_TRIES + 5):
            self.assertEqual(self.call().status_code, 200)
        self.assert_answer(self.call("nobody-here.negosyotest.com"), 404, NOT_FOUND)


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.getMessage()} {record.args!r} {record.__dict__!r}")


class SecretNeverShownTests(SurfaceTestCase):
    def test_no_answer_and_no_log_record_carries_the_secret(self):
        make_subscription()
        records = _Records()
        root = logging.getLogger()
        level = root.level
        root.addHandler(records)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, level)
        self.addCleanup(root.removeHandler, records)

        responses = [
            self.call(), self.call("nobody-here.negosyotest.com"), self.call(secret="wrong"),
            self.call(secret=None), self.call(secret=SECRET + "x"),
            self.call(path="/storefront/v1/nothing-here/"), self.call(method="post"),
        ]
        for _ in range(auth.FAILED_TRIES + 1):
            responses.append(self.call(secret=SECRET[:-1], ip="203.0.113.9"))
        with override_settings(STOREFRONT_ENABLED=False):
            responses.append(self.call())

        self.assertEqual(responses[-2].status_code, 429)
        for response in responses:
            self.assertNotIn(SECRET, response.content.decode())
            self.assertNotIn(SECRET, repr(response.headers))
        self.assertTrue(records.lines)  # the refusals were logged, so the search means something
        for line in records.lines:
            self.assertNotIn(SECRET, line)
            self.assertNotIn(SECRET[:-1], line)


class DeployCheckTests(SimpleTestCase):
    def messages(self):
        return [
            message for message in run_checks(include_deployment_checks=True)
            if message.id == "storefront.W001"
        ]

    def test_it_warns_when_the_switch_is_on_without_a_long_enough_secret(self):
        for value in ("", "short-and-recognisable"):
            with self.subTest(value=value), override_settings(
                STOREFRONT_ENABLED=True, STOREFRONT_SERVER_SECRET=value
            ):
                (message,) = self.messages()
                self.assertNotIn("recognisable", f"{message.msg} {message.hint}")

    def test_it_is_silent_when_off_or_fully_set(self):
        with override_settings(STOREFRONT_ENABLED=False, STOREFRONT_SERVER_SECRET=""):
            self.assertEqual(self.messages(), [])
        with override_settings(STOREFRONT_ENABLED=True, STOREFRONT_SERVER_SECRET=SECRET):
            self.assertEqual(self.messages(), [])

    def test_it_runs_only_with_the_deployment_checks(self):
        with override_settings(STOREFRONT_ENABLED=True, STOREFRONT_SERVER_SECRET=""):
            self.assertFalse([m for m in run_checks() if m.id == "storefront.W001"])
