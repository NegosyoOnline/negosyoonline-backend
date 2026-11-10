import base64
import json
import time
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from apps.accounts import google

REDIRECT = "https://api.example.test/v1/auth/google/callback/"


def id_token(**overrides):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "client-id",
        "exp": time.time() + 3600,
        "sub": "g-123",
        "email": "Ana@Example.test",
        "email_verified": True,
        "name": "Ana Reyes",
    }
    claims.update(overrides)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


@override_settings(GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret")
class GoogleClientTests(SimpleTestCase):
    def fetch(self, **exchange):
        with mock.patch("apps.accounts.google._token_response", **exchange) as call:
            profile = google.fetch_profile("the-code", REDIRECT)
        call.assert_called_once_with("the-code", REDIRECT)
        return profile

    def test_is_configured_needs_both_the_id_and_the_secret(self):
        self.assertTrue(google.is_configured())
        with override_settings(GOOGLE_CLIENT_SECRET=""):
            self.assertFalse(google.is_configured())

    def test_authorization_url_asks_google_for_a_code(self):
        url = urlsplit(google.authorization_url(REDIRECT, "the-state"))
        query = {key: values[0] for key, values in parse_qs(url.query).items()}

        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", google.AUTH_URL)
        self.assertEqual(
            query,
            {
                "client_id": "client-id",
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "scope": "openid email profile",
                "state": "the-state",
                "prompt": "select_account",
            },
        )
        self.assertNotIn("client-secret", url.query)

    def test_fetch_profile_reads_the_person_from_the_id_token(self):
        profile = self.fetch(return_value={"id_token": id_token(), "access_token": "unused"})

        self.assertEqual(
            profile,
            google.GoogleProfile(
                user_id="g-123", email="Ana@Example.test", email_verified=True, name="Ana Reyes"
            ),
        )

    def test_a_token_not_meant_for_us_is_refused(self):
        bad_tokens = [
            id_token(aud="someone-else"),
            id_token(iss="https://evil.example.test"),
            id_token(exp=time.time() - 1),
            id_token(sub=""),
            "not-a-token",
        ]
        for token in bad_tokens:
            with self.assertLogs("apps.accounts.google", level="WARNING"):
                with self.assertRaises(google.GoogleSignInFailed):
                    self.fetch(return_value={"id_token": token})

    def test_a_failed_exchange_is_refused(self):
        for exchange in ({"side_effect": OSError("down")}, {"return_value": {"error": "bad"}}):
            with self.assertLogs("apps.accounts.google", level="WARNING") as logs:
                with self.assertRaises(google.GoogleSignInFailed):
                    self.fetch(**exchange)
            self.assertNotIn("the-code", "".join(logs.output))
