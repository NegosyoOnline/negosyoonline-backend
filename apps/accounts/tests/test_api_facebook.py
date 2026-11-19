from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.core import mail
from django.test import override_settings

from apps.accounts import facebook
from apps.accounts.facebook import FacebookProfile
from apps.accounts.models import Account, ExternalIdentity
from apps.accounts.tests.base import EMAIL, ApiTestCase

DASH = "https://dash.example.test"
SITE = "https://site.example.test"
CALLBACK = "https://testserver/v1/auth/facebook/callback/"
CONFIRM = "/v1/auth/facebook/confirm-email/"
PROFILE = FacebookProfile(user_id="fb-1", name="Ana Reyes", email=EMAIL)
NO_EMAIL = FacebookProfile(user_id="fb-1", name="Ana Reyes", email="")


@override_settings(
    FACEBOOK_APP_ID="app-id", FACEBOOK_APP_SECRET="app-secret", DASHBOARD_URL=DASH, SITE_URL=SITE
)
class FacebookApiTests(ApiTestCase):
    def start(self, query="?accept_policies=true"):
        """Begin the flow and return the state Facebook would send back."""
        response = self.get(f"/v1/auth/facebook/start/{query}")
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlsplit(response["Location"]).query)["state"][0]

    def callback(self, state, code="the-code", **fetch):
        fetch = fetch or {"return_value": PROFILE}
        query = urlencode({"state": state, "code": code})
        with mock.patch("apps.accounts.api_facebook.facebook.fetch_profile", **fetch) as call:
            response = self.get(f"/v1/auth/facebook/callback/?{query}")
        self.assertEqual(response.status_code, 302)
        return response, call

    def arrive(self, profile=PROFILE, query="?accept_policies=true"):
        """A first-time Facebook user, back from Facebook and waiting to confirm an email."""
        response, _call = self.callback(self.start(query), return_value=profile)
        self.assertEqual(response["Location"], f"{SITE}/?signin=1&facebook=confirm")
        return response

    def test_start_redirects_to_facebook_and_sets_the_state_cookie(self):
        response = self.get("/v1/auth/facebook/start/")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(facebook.DIALOG_URL + "?"))
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["redirect_uri"], [CALLBACK])
        cookie = response.cookies["no_facebook_state"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["path"], "/v1/auth/facebook/")
        self.assertEqual(cookie["max-age"], 600)

    @override_settings(FACEBOOK_LOGIN_CONFIG_ID="cfg-123")
    def test_start_sends_the_config_id_and_no_scope_when_set(self):
        query = parse_qs(urlsplit(self.get("/v1/auth/facebook/start/")["Location"]).query)
        self.assertEqual(query["config_id"], ["cfg-123"])
        self.assertNotIn("scope", query)
        self.assertEqual(
            (query["client_id"], query["redirect_uri"], query["response_type"]),
            (["app-id"], [CALLBACK], ["code"]),
        )
        self.assertIn("state", query)

    @override_settings(FACEBOOK_LOGIN_CONFIG_ID="")
    def test_start_sends_the_scope_and_no_config_id_when_empty(self):
        query = parse_qs(urlsplit(self.get("/v1/auth/facebook/start/")["Location"]).query)
        self.assertEqual(query["scope"], ["public_profile,email"])
        self.assertNotIn("config_id", query)

    def test_a_linked_facebook_user_is_signed_in_and_sent_to_the_dashboard(self):
        account = Account.objects.create(email=EMAIL)
        ExternalIdentity.objects.create(account=account, provider="facebook", provider_user_id="fb-1")

        response, call = self.callback(self.start())

        call.assert_called_once_with("the-code", CALLBACK)
        self.assertEqual(response["Location"], f"{DASH}/")
        self.assertTrue(response.cookies["no_session"]["httponly"])
        self.assertEqual(response.cookies["no_facebook_state"].value, "")
        self.assertEqual(self.get("/v1/me/").json()["account"]["email"], EMAIL)

    def test_a_first_time_user_is_sent_to_confirm_and_is_not_signed_in(self):
        response = self.arrive()

        self.assertNotIn("no_session", response.cookies)
        self.assertNotIn("example.test/?signin=1&facebook=confirm&", response["Location"])
        pending = response.cookies["no_facebook_pending"]
        self.assertTrue(pending["httponly"])
        self.assertEqual(pending["path"], "/v1/auth/facebook/")
        self.assertEqual(pending["max-age"], 900)
        self.assertEqual(self.get("/v1/me/").status_code, 401)
        self.assertFalse(Account.objects.exists())

    def test_confirming_the_facebook_email_creates_the_account(self):
        self.arrive()

        sent = self.post(CONFIRM, {"email": "ignored@example.test"})

        self.assertEqual(sent.status_code, 202)
        self.assertEqual(sent.json(), {"email": EMAIL})
        self.assertEqual(mail.outbox[-1].to, [EMAIL])

        confirmed = self.post(CONFIRM, {"code": self.last_code()})

        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.json()["created"])
        self.assertEqual(confirmed.json()["account"]["email"], EMAIL)
        self.assertTrue(confirmed.cookies["no_session"]["httponly"])
        self.assertEqual(confirmed.cookies["no_facebook_pending"].value, "")
        self.assertEqual(self.get("/v1/me/").json()["account"]["name"], "Ana Reyes")

    def test_a_user_who_shared_no_email_types_one(self):
        self.arrive(NO_EMAIL)

        needs_email = self.post(CONFIRM, {})
        sent = self.post(CONFIRM, {"email": " Ana@Example.TEST "})
        confirmed = self.post(CONFIRM, {"code": self.last_code()})

        self.assertEqual(needs_email.status_code, 400)
        self.assertIn("email", needs_email.json()["error"]["fields"])
        self.assertEqual((sent.status_code, sent.json()), (202, {"email": EMAIL}))
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(Account.objects.filter(email=EMAIL).exists())

    def test_policies_can_be_accepted_when_confirming(self):
        self.arrive(query="")
        self.post(CONFIRM, {})
        code = self.last_code()

        refused = self.post(CONFIRM, {"code": code})
        accepted = self.post(CONFIRM, {"code": code, "accept_policies": True})

        self.assertEqual(refused.json()["error"]["code"], "policies_required")
        self.assertEqual(accepted.status_code, 200)

    def test_confirming_needs_a_pending_sign_in_and_an_allowed_origin(self):
        not_started = self.post(CONFIRM, {})
        self.arrive()
        no_origin = self.post(CONFIRM, {}, HTTP_ORIGIN=None)

        self.assertEqual(not_started.status_code, 400)
        self.assertEqual(not_started.json()["error"]["code"], "facebook_not_started")
        self.assertEqual(no_origin.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_callback_this_browser_did_not_start_is_refused(self):
        response, call = self.callback("made-up-state")
        self.start()
        wrong_state, second_call = self.callback("made-up-state")

        for landed in (response, wrong_state):
            self.assertEqual(landed["Location"], f"{SITE}/?signin=1&error=facebook_state_invalid")
        call.assert_not_called()
        second_call.assert_not_called()

    def test_cancelling_or_a_refused_code_is_sent_back(self):
        cancelled, call = self.callback(self.start(), code="")
        failed, _call = self.callback(self.start(), side_effect=facebook.FacebookSignInFailed())

        self.assertEqual(cancelled["Location"], f"{SITE}/?signin=1&error=facebook_cancelled")
        call.assert_not_called()
        self.assertEqual(failed["Location"], f"{SITE}/?signin=1&error=facebook_failed")
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    @override_settings(FACEBOOK_APP_ID="")
    def test_without_facebook_variables_the_endpoints_are_404(self):
        responses = [
            self.get("/v1/auth/facebook/start/"),
            self.get("/v1/auth/facebook/callback/?state=x&code=y"),
            self.post(CONFIRM, {}),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")
