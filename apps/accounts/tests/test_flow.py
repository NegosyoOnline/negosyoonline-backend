from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.accounts import flow

NAME = "no_test_flow"
PATH = "/v1/auth/test/"


def request_with(response, name=NAME):
    request = RequestFactory().get(PATH)
    request.COOKIES[name] = response.cookies[NAME].value
    return request


class FlowCookieTests(SimpleTestCase):
    def test_payload_round_trips_through_a_locked_down_cookie(self):
        response = HttpResponse()

        flow.set_flow_cookie(response, NAME, PATH, {"state": "abc", "n": 1}, 600)

        cookie = response.cookies[NAME]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], PATH)
        self.assertEqual(cookie["max-age"], 600)
        self.assertNotIn("abc", cookie.value)
        self.assertEqual(
            flow.read_flow_cookie(request_with(response), NAME, 600), {"state": "abc", "n": 1}
        )

    def test_missing_altered_expired_or_borrowed_cookies_read_as_none(self):
        response = HttpResponse()
        flow.set_flow_cookie(response, NAME, PATH, {"state": "abc"}, 600)
        altered = request_with(response)
        altered.COOKIES[NAME] = altered.COOKIES[NAME][:-2] + "xx"

        self.assertIsNone(flow.read_flow_cookie(RequestFactory().get(PATH), NAME, 600))
        self.assertIsNone(flow.read_flow_cookie(altered, NAME, 600))
        self.assertIsNone(flow.read_flow_cookie(request_with(response), NAME, -1))
        self.assertIsNone(
            flow.read_flow_cookie(request_with(response, "no_other_flow"), "no_other_flow", 600)
        )

    def test_clear_flow_cookie_empties_it(self):
        response = HttpResponse()

        flow.clear_flow_cookie(response, NAME, PATH)

        self.assertEqual(response.cookies[NAME].value, "")
        self.assertEqual(response.cookies[NAME]["path"], PATH)

    def test_same_state_compares_text_safely(self):
        self.assertTrue(flow.same_state("abc", "abc"))
        self.assertFalse(flow.same_state("abc", "abd"))
        self.assertFalse(flow.same_state("abc", ""))
        self.assertFalse(flow.same_state("abc", "ábc"))

    @override_settings(DASHBOARD_URL="https://dash.example.test", SITE_URL="https://site.example.test")
    def test_a_flow_ends_on_the_dashboard_or_in_the_site_popup(self):
        self.assertEqual(flow.finish_redirect()["Location"], "https://dash.example.test/")
        self.assertEqual(
            flow.finish_redirect("some_error")["Location"],
            "https://site.example.test/?signin=1&error=some_error",
        )
