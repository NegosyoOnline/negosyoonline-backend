from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.test import override_settings

from apps.accounts import google
from apps.accounts.google import GoogleProfile
from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL, ApiTestCase

DASH = "https://dash.example.test"
SITE = "https://site.example.test"
CALLBACK = "https://testserver/v1/auth/google/callback/"
PROFILE = GoogleProfile(user_id="g-123", email=EMAIL, email_verified=True, name="Ana Reyes")


@override_settings(
    GOOGLE_CLIENT_ID="client-id",
    GOOGLE_CLIENT_SECRET="client-secret",
    DASHBOARD_URL=DASH,
    SITE_URL=SITE,
)
class GoogleApiTests(ApiTestCase):
    def start(self, query=""):
        """Begin the flow and return the state Google would send back."""
        response = self.get(f"/v1/auth/google/start/{query}")
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlsplit(response["Location"]).query)["state"][0]

    def callback(self, state, code="the-code", **fetch):
        fetch = fetch or {"return_value": PROFILE}
        query = urlencode({"state": state, "code": code})
        with mock.patch("apps.accounts.api.google.fetch_profile", **fetch) as call:
            response = self.get(f"/v1/auth/google/callback/?{query}")
        self.assertEqual(response.status_code, 302)
        return response, call

    def test_start_redirects_to_google_and_sets_the_state_cookie(self):
        response = self.get("/v1/auth/google/start/")

        self.assertEqual(response.status_code, 302)
        location = urlsplit(response["Location"])
        self.assertTrue(response["Location"].startswith(google.AUTH_URL + "?"))
        self.assertEqual(parse_qs(location.query)["redirect_uri"], [CALLBACK])
        cookie = response.cookies["no_google_state"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/v1/auth/google/")
        self.assertEqual(cookie["max-age"], 600)
        self.assertNotIn(parse_qs(location.query)["state"][0], response.cookies.output())

    def test_callback_signs_in_and_returns_to_the_dashboard(self):
        state = self.start("?accept_policies=true")

        response, call = self.callback(state)

        call.assert_called_once_with("the-code", CALLBACK)
        self.assertEqual(response["Location"], f"{DASH}/")
        self.assertTrue(response.cookies["no_session"]["httponly"])
        self.assertEqual(response.cookies["no_google_state"].value, "")
        self.assertEqual(self.get("/v1/me/").json()["account"]["email"], EMAIL)

    def test_new_account_without_accepting_policies_is_sent_to_the_site_popup(self):
        state = self.start()

        response, _call = self.callback(state)

        self.assertEqual(response["Location"], f"{SITE}/?signin=1&error=policies_required")
        self.assertNotIn("no_session", response.cookies)
        self.assertFalse(Account.objects.exists())

    def test_a_callback_this_browser_did_not_start_is_refused(self):
        without_cookie, first_call = self.callback("made-up-state")
        self.start("?accept_policies=true")
        wrong_state, second_call = self.callback("made-up-state")

        for response in (without_cookie, wrong_state):
            self.assertEqual(response["Location"], f"{SITE}/?signin=1&error=google_state_invalid")
        first_call.assert_not_called()
        second_call.assert_not_called()
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    def test_cancelling_at_google_is_sent_back(self):
        state = self.start("?accept_policies=true")

        response, call = self.callback(state, code="")

        self.assertEqual(response["Location"], f"{SITE}/?signin=1&error=google_cancelled")
        call.assert_not_called()

    def test_a_code_google_refuses_is_sent_back(self):
        state = self.start("?accept_policies=true")

        response, _call = self.callback(state, side_effect=google.GoogleSignInFailed())

        self.assertEqual(response["Location"], f"{SITE}/?signin=1&error=google_failed")
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_without_google_variables_both_endpoints_are_404(self):
        for path in ("/v1/auth/google/start/", "/v1/auth/google/callback/?state=x&code=y"):
            response = self.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")
