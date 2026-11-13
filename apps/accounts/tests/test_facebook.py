import base64
import hashlib
import hmac
import json
import time
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from apps.accounts import facebook

REDIRECT = "https://api.example.test/v1/auth/facebook/callback/"
SECRET = "app-secret"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def signed_request(secret=SECRET, **overrides):
    """A request signed the way Meta signs its data deletion callback."""
    data = {"algorithm": "HMAC-SHA256", "user_id": "fb-1", "issued_at": int(time.time())}
    data.update(overrides)
    payload = _b64(json.dumps(data).encode())
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{_b64(signature)}.{payload}"


@override_settings(FACEBOOK_APP_ID="app-id", FACEBOOK_APP_SECRET=SECRET)
class FacebookClientTests(SimpleTestCase):
    def fetch(self, *answers):
        with mock.patch("apps.accounts.facebook._get_json", side_effect=list(answers)) as call:
            profile = facebook.fetch_profile("the-code", REDIRECT)
        return profile, call

    def test_is_configured_needs_both_the_id_and_the_secret(self):
        self.assertTrue(facebook.is_configured())
        with override_settings(FACEBOOK_APP_SECRET=""):
            self.assertFalse(facebook.is_configured())

    def test_authorization_url_asks_only_for_profile_and_email(self):
        url = urlsplit(facebook.authorization_url(REDIRECT, "the-state"))
        query = {key: values[0] for key, values in parse_qs(url.query).items()}

        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", facebook.DIALOG_URL)
        self.assertEqual(
            query,
            {
                "client_id": "app-id",
                "redirect_uri": REDIRECT,
                "state": "the-state",
                "response_type": "code",
                "scope": "public_profile,email",
            },
        )
        self.assertNotIn(SECRET, url.query)

    def test_fetch_profile_swaps_the_code_then_reads_the_person(self):
        profile, call = self.fetch(
            {"access_token": "the-token"},
            {"id": "fb-1", "name": "Ana Reyes", "email": "Ana@Example.test"},
        )

        self.assertEqual(
            profile,
            facebook.FacebookProfile(user_id="fb-1", name="Ana Reyes", email="Ana@Example.test"),
        )
        (token_url, token_params), (me_url, me_params) = [item.args for item in call.call_args_list]
        self.assertEqual(token_url, facebook.TOKEN_URL)
        self.assertEqual(
            token_params,
            {
                "client_id": "app-id",
                "client_secret": SECRET,
                "redirect_uri": REDIRECT,
                "code": "the-code",
            },
        )
        self.assertEqual(me_url, facebook.ME_URL)
        self.assertEqual(me_params["fields"], "id,name,email")
        self.assertEqual(
            me_params["appsecret_proof"],
            hmac.new(SECRET.encode(), b"the-token", hashlib.sha256).hexdigest(),
        )

    def test_a_person_who_shares_no_email_has_an_empty_one(self):
        profile, _call = self.fetch({"access_token": "the-token"}, {"id": "fb-1", "name": "Ana"})

        self.assertEqual(profile.email, "")

    def test_a_failed_exchange_is_refused_without_logging_secrets(self):
        failures = [
            [OSError("down")],
            [{"error": {"message": "bad code"}}],
            [{"access_token": "the-token"}, {"name": "No Id"}],
        ]
        for answers in failures:
            with self.assertLogs("apps.accounts.facebook", level="WARNING") as logs:
                with self.assertRaises(facebook.FacebookSignInFailed):
                    self.fetch(*answers)
            output = "".join(logs.output)
            self.assertNotIn("the-code", output)
            self.assertNotIn("the-token", output)

    def test_parse_signed_request_returns_the_facebook_user_id(self):
        self.assertEqual(facebook.parse_signed_request(signed_request()), "fb-1")

    def test_a_request_meta_did_not_sign_is_refused(self):
        good = signed_request()
        bad_requests = [
            signed_request(secret="someone-elses-secret"),
            good[:-3] + "abc",
            signed_request(algorithm="none"),
            signed_request(user_id=""),
            "no-dot-here",
            "",
        ]
        for value in bad_requests:
            with self.assertLogs("apps.accounts.facebook", level="WARNING"):
                with self.assertRaises(facebook.BadSignedRequest):
                    facebook.parse_signed_request(value)
