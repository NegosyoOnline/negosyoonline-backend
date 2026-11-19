from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.core import mail
from django.test import Client, override_settings

from apps.accounts import facebook, google
from apps.accounts.facebook import FacebookProfile, FacebookSignInFailed
from apps.accounts.google import GoogleProfile, GoogleSignInFailed
from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity, Session
from apps.accounts.tests.base import EMAIL, ApiTestCase

DASH = "https://dash.example.test"
SITE = "https://site.example.test"
PROFILES = {
    "google": GoogleProfile(
        user_id="g-123", email="other@gmail.test", email_verified=False, name="Ana"
    ),
    "facebook": FacebookProfile(user_id="f-123", name="Ana", email=""),
}
FAILURES = {"google": GoogleSignInFailed, "facebook": FacebookSignInFailed}
FETCH = {
    "google": "apps.accounts.api.google.fetch_profile",
    "facebook": "apps.accounts.api_facebook.facebook.fetch_profile",
}
AUTH_URLS = {"google": google.AUTH_URL, "facebook": facebook.DIALOG_URL}
STATE_COOKIES = {"google": "no_google_state", "facebook": "no_facebook_state"}
PROVIDERS = ("google", "facebook")


@override_settings(
    GOOGLE_CLIENT_ID="client-id",
    GOOGLE_CLIENT_SECRET="client-secret",
    FACEBOOK_APP_ID="app-id",
    FACEBOOK_APP_SECRET="app-secret",
    DASHBOARD_URL=DASH,
    SITE_URL=SITE,
)
class ConnectionTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.account = Account.objects.get(email=EMAIL)

    def start(self, provider, client=None):
        response = self.post(f"/v1/account/connections/{provider}/start/", client=client)
        self.assertEqual(response.status_code, 200, response.content)
        return response, parse_qs(urlsplit(response.json()["url"]).query)["state"][0]

    def callback(self, provider, state, client=None, code="the-code", error="", **fetch):
        fetch = fetch or {"return_value": PROFILES[provider]}
        query = urlencode({"state": state, "code": code, "error": error})
        with mock.patch(FETCH[provider], **fetch) as call:
            response = self.get(f"/v1/auth/{provider}/callback/?{query}", client=client)
        self.assertEqual(response.status_code, 302)
        return response, call

    def result(self, provider, slug):
        return f"{DASH}/account/#connect={provider}&result={slug}"

    def stranger(self):
        """A second signed-in account on its own browser."""
        client = Client()
        self.sign_in("stranger@example.test", client=client)
        return client, Account.objects.get(email="stranger@example.test")

    def test_every_connection_endpoint_needs_a_session(self):
        self.client.cookies.clear()

        for provider in PROVIDERS:
            for action in ("start", "disconnect"):
                response = self.post(f"/v1/account/connections/{provider}/{action}/")
                self.assertEqual(response.status_code, 401, f"{provider} {action}")

    def test_a_call_from_another_origin_is_refused(self):
        for provider in PROVIDERS:
            for action in ("start", "disconnect"):
                response = self.post(
                    f"/v1/account/connections/{provider}/{action}/",
                    HTTP_ORIGIN="https://evil.example.test",
                )
                self.assertEqual(response.status_code, 403, f"{provider} {action}")

    def test_an_unknown_provider_is_404(self):
        for action in ("start", "disconnect"):
            response = self.post(f"/v1/account/connections/twitter/{action}/")
            self.assertEqual(response.status_code, 404)

    def test_start_answers_with_the_provider_url_and_sets_the_state_cookie(self):
        for provider in PROVIDERS:
            response, state = self.start(provider)

            url = urlsplit(response.json()["url"])
            self.assertTrue(response.json()["url"].startswith(AUTH_URLS[provider] + "?"))
            self.assertEqual(
                parse_qs(url.query)["redirect_uri"],
                [f"https://testserver/v1/auth/{provider}/callback/"],
            )
            cookie = response.cookies[STATE_COOKIES[provider]]
            self.assertTrue(cookie["httponly"])
            self.assertTrue(cookie["secure"])
            self.assertEqual(cookie["samesite"], "Lax")
            self.assertEqual(cookie["path"], f"/v1/auth/{provider}/")
            self.assertNotIn(state, response.cookies.output())
            self.assertNotIn(self.account.public_id, response.cookies.output())

    @override_settings(FACEBOOK_LOGIN_CONFIG_ID="cfg-123")
    def test_facebook_start_sends_the_config_id_and_no_scope_when_set(self):
        response, _state = self.start("facebook")
        query = parse_qs(urlsplit(response.json()["url"]).query)
        self.assertEqual(query["config_id"], ["cfg-123"])
        self.assertNotIn("scope", query)
        self.assertEqual((query["client_id"], query["response_type"]), (["app-id"], ["code"]))
        self.assertEqual(query["redirect_uri"], ["https://testserver/v1/auth/facebook/callback/"])

    @override_settings(FACEBOOK_LOGIN_CONFIG_ID="")
    def test_facebook_start_sends_the_scope_and_no_config_id_when_empty(self):
        response, _state = self.start("facebook")
        query = parse_qs(urlsplit(response.json()["url"]).query)
        self.assertEqual(query["scope"], ["public_profile,email"])
        self.assertNotIn("config_id", query)

    @override_settings(GOOGLE_CLIENT_ID="", FACEBOOK_APP_ID="")
    def test_start_says_not_configured_when_the_provider_has_no_keys(self):
        for provider in PROVIDERS:
            response = self.post(f"/v1/account/connections/{provider}/start/")

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "not_configured")

    def test_start_is_refused_when_the_provider_is_already_connected(self):
        for provider in PROVIDERS:
            ExternalIdentity.objects.create(
                account=self.account, provider=provider, provider_user_id="mine"
            )

            response = self.post(f"/v1/account/connections/{provider}/start/")

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "already_connected")

    def test_callback_connects_without_signing_in_or_matching_by_email(self):
        for provider in PROVIDERS:
            sessions = Session.objects.count()
            _, state = self.start(provider)

            response, call = self.callback(provider, state)

            call.assert_called_once()
            self.assertEqual(response["Location"], self.result(provider, "connected"))
            self.assertNotIn("no_session", response.cookies)
            self.assertEqual(response.cookies[STATE_COOKIES[provider]].value, "")
            identity = ExternalIdentity.objects.get(provider=provider)
            self.assertEqual(identity.account, self.account)
            self.assertEqual(identity.provider_user_id, PROFILES[provider].user_id)
            self.assertEqual(Session.objects.count(), sessions)
            self.assertEqual(Account.objects.count(), 1)
            event = AccountAuditEvent.objects.get(
                account=self.account, event="identity_linked", detail__provider=provider
            )
            self.assertEqual(event.detail["via"], "settings")
        connected = {p["provider"]: p["connected"] for p in self.get("/v1/account/").json()["providers"]}
        self.assertEqual(connected, {"facebook": True, "google": True})

    def test_callback_says_in_use_and_leaves_the_other_accounts_link_alone(self):
        other, stranger = self.stranger()
        for provider in PROVIDERS:
            theirs = ExternalIdentity.objects.create(
                account=stranger, provider=provider, provider_user_id=PROFILES[provider].user_id
            )
            _, state = self.start(provider)

            response, _ = self.callback(provider, state)

            self.assertEqual(response["Location"], self.result(provider, "in_use"))
            theirs.refresh_from_db()
            self.assertEqual(theirs.account, stranger)
            self.assertFalse(self.account.identities.exists())

    def test_callback_says_already_connected(self):
        for provider in PROVIDERS:
            _, state = self.start(provider)
            ExternalIdentity.objects.create(
                account=self.account, provider=provider, provider_user_id="linked-meanwhile"
            )

            response, _ = self.callback(provider, state)

            self.assertEqual(response["Location"], self.result(provider, "already_connected"))
            self.assertEqual(self.account.identities.filter(provider=provider).count(), 1)

    def test_callback_says_cancelled_when_the_provider_returns_an_error_or_no_code(self):
        for provider in PROVIDERS:
            for extra in ({"error": "access_denied"}, {"code": ""}):
                _, state = self.start(provider)

                response, call = self.callback(provider, state, **extra)

                call.assert_not_called()
                self.assertEqual(response["Location"], self.result(provider, "cancelled"))
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_callback_says_failed_when_the_provider_refuses_the_code(self):
        for provider in PROVIDERS:
            _, state = self.start(provider)

            response, _ = self.callback(provider, state, side_effect=FAILURES[provider]())

            self.assertEqual(response["Location"], self.result(provider, "failed"))
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_callback_says_invalid_state_when_the_state_does_not_match(self):
        for provider in PROVIDERS:
            self.start(provider)

            response, call = self.callback(provider, "not-the-state")

            call.assert_not_called()
            self.assertEqual(response["Location"], self.result(provider, "invalid_state"))
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_callback_links_nothing_once_the_browser_is_signed_out(self):
        for provider in PROVIDERS:
            _, state = self.start(provider)
            Session.objects.filter(account=self.account).update(revoked_at="2026-01-01T00:00:00Z")

            response, call = self.callback(provider, state)

            call.assert_not_called()
            self.assertEqual(response["Location"], self.result(provider, "failed"))
            self.sign_in()
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_callback_links_nothing_when_another_account_is_now_signed_in(self):
        for provider in PROVIDERS:
            _, state = self.start(provider)
            flow_cookie = self.client.cookies[STATE_COOKIES[provider]].value
            other, stranger = self.stranger()
            other.cookies[STATE_COOKIES[provider]] = flow_cookie

            response, call = self.callback(provider, state, client=other)

            call.assert_not_called()
            self.assertEqual(response["Location"], self.result(provider, "failed"))
            Account.objects.filter(pk=stranger.pk).delete()
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_a_sign_in_without_connect_mode_still_signs_in(self):
        self.client.cookies.clear()
        started = self.get("/v1/auth/google/start/")
        state = parse_qs(urlsplit(started["Location"]).query)["state"][0]
        profile = GoogleProfile(user_id="g-1", email=EMAIL, email_verified=True, name="Ana")

        response, _ = self.callback("google", state, return_value=profile)

        self.assertEqual(response["Location"], f"{DASH}/")
        self.assertIn("no_session", response.cookies)

    def test_disconnect_removes_only_my_link_and_answers_with_the_read(self):
        other, stranger = self.stranger()
        for provider in PROVIDERS:
            ExternalIdentity.objects.create(
                account=self.account, provider=provider, provider_user_id="mine"
            )
            ExternalIdentity.objects.create(
                account=stranger, provider=provider, provider_user_id="theirs"
            )

            response = self.post(f"/v1/account/connections/{provider}/disconnect/")

            self.assertEqual(response.status_code, 200)
            shown = {p["provider"]: p["connected"] for p in response.json()["providers"]}
            self.assertIs(shown[provider], False)
            self.assertIn("devices", response.json())
            self.assertFalse(self.account.identities.filter(provider=provider).exists())
            self.assertTrue(stranger.identities.filter(provider=provider).exists())
            event = AccountAuditEvent.objects.get(
                account=self.account, event="identity_removed", detail__provider=provider
            )
            self.assertEqual(event.detail["reason"], "owner")
        self.assertFalse(AccountAuditEvent.objects.filter(account=stranger, event="identity_removed").exists())

    def test_disconnect_when_not_connected_is_a_quiet_200(self):
        response = self.post("/v1/account/connections/google/disconnect/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(AccountAuditEvent.objects.filter(event="identity_removed").exists())

    def test_email_code_sign_in_still_works_after_disconnecting_everything(self):
        for provider in PROVIDERS:
            ExternalIdentity.objects.create(
                account=self.account, provider=provider, provider_user_id="mine"
            )
            self.post(f"/v1/account/connections/{provider}/disconnect/")
        fresh = Client()

        response = self.sign_in(client=fresh)

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["created"], False)

    def test_a_connect_and_a_disconnect_each_send_one_security_notice(self):
        for provider, label in (("google", "Google"), ("facebook", "Facebook")):
            _, state = self.start(provider)
            mail.outbox.clear()

            self.callback(provider, state)

            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].to, [EMAIL])
            self.assertEqual(mail.outbox[0].subject, f"{label} was connected to your NegosyoOnline account")
            self.assertIn("contact support@negosyoonline.com", mail.outbox[0].body)
            self.assertNotIn(PROFILES[provider].user_id, mail.outbox[0].body)
            mail.outbox.clear()

            self.post(f"/v1/account/connections/{provider}/disconnect/")

            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].to, [EMAIL])
            self.assertEqual(
                mail.outbox[0].subject, f"{label} was disconnected from your NegosyoOnline account"
            )
            self.assertNotIn(PROFILES[provider].user_id, mail.outbox[0].body)

    def test_nothing_is_sent_when_nothing_changed(self):
        other, stranger = self.stranger()
        ExternalIdentity.objects.create(
            account=stranger, provider="google", provider_user_id=PROFILES["google"].user_id
        )
        _, in_use = self.start("google")
        _, cancelled = self.start("facebook")
        mail.outbox.clear()

        self.callback("google", in_use)
        self.callback("facebook", cancelled, error="access_denied")
        self.post("/v1/account/connections/facebook/disconnect/")

        self.assertEqual(mail.outbox, [])

    def test_the_change_stands_when_the_notice_cannot_be_sent(self):
        _, state = self.start("google")

        with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("smtp down")):
            response, _ = self.callback("google", state)
            disconnected = self.post("/v1/account/connections/google/disconnect/")

        self.assertEqual(response["Location"], self.result("google", "connected"))
        self.assertEqual(disconnected.status_code, 200)
        self.assertFalse(self.account.identities.exists())
