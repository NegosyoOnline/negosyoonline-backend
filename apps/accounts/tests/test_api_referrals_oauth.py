from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.test import override_settings

from apps.accounts.facebook import FacebookProfile
from apps.accounts.google import GoogleProfile
from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL, ApiTestCase

NEW_EMAIL = "ben@example.test"
GOOGLE = GoogleProfile(user_id="g-9", email=NEW_EMAIL, email_verified=True, name="Ben Lim")
FACEBOOK = FacebookProfile(user_id="fb-9", name="Ben Lim", email=NEW_EMAIL)
OAUTH = dict(
    GOOGLE_CLIENT_ID="id",
    GOOGLE_CLIENT_SECRET="secret",
    FACEBOOK_APP_ID="id",
    FACEBOOK_APP_SECRET="secret",
    DASHBOARD_URL="https://dash.example.test",
    SITE_URL="https://site.example.test",
)


class OAuthBase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.referrer = Account.objects.create(email=EMAIL, name="Ana")

    def google(self, referral_code=None, profile=GOOGLE):
        query = {"accept_policies": "true"}
        if referral_code is not None:
            query["referral_code"] = referral_code
        start = self.get("/v1/auth/google/start/?" + urlencode(query))
        state = parse_qs(urlsplit(start["Location"]).query)["state"][0]
        with mock.patch("apps.accounts.api.google.fetch_profile", return_value=profile):
            return self.get("/v1/auth/google/callback/?" + urlencode({"state": state, "code": "c"}))

    def facebook(self, referral_code=None):
        query = {"accept_policies": "true"}
        if referral_code is not None:
            query["referral_code"] = referral_code
        start = self.get("/v1/auth/facebook/start/?" + urlencode(query))
        state = parse_qs(urlsplit(start["Location"]).query)["state"][0]
        with mock.patch("apps.accounts.api_facebook.facebook.fetch_profile", return_value=FACEBOOK):
            self.get("/v1/auth/facebook/callback/?" + urlencode({"state": state, "code": "c"}))
        self.post("/v1/auth/facebook/confirm-email/", {})
        return self.post("/v1/auth/facebook/confirm-email/", {"code": self.last_code()})

    def referred_by(self, email=NEW_EMAIL):
        return Account.objects.get(email=email).referred_by


@override_settings(BILLING_REFERRALS_ENABLED=True, **OAUTH)
class GoogleReferralTests(OAuthBase):
    def test_a_valid_code_sets_referred_by_on_a_new_google_account(self):
        self.google(self.referrer.referral_code)

        self.assertEqual(self.referred_by(), self.referrer)

    def test_an_invalid_code_is_ignored_and_signup_succeeds(self):
        response = self.google("ZZZZZZZZ")

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("error", response["Location"])
        self.assertIsNone(self.referred_by())

    def test_no_code_leaves_it_empty(self):
        self.google()

        self.assertIsNone(self.referred_by())

    def test_an_existing_account_is_unchanged(self):
        Account.objects.create(email=NEW_EMAIL)
        self.google(self.referrer.referral_code)

        self.assertIsNone(self.referred_by())

    def test_an_overlong_code_is_refused_at_start(self):
        response = self.get("/v1/auth/google/start/?referral_code=" + "x" * 65)

        self.assertEqual(response.status_code, 400)

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_switch_off_has_no_effect(self):
        self.google(self.referrer.referral_code)

        self.assertTrue(Account.objects.filter(email=NEW_EMAIL).exists())
        self.assertIsNone(self.referred_by())


@override_settings(BILLING_REFERRALS_ENABLED=True, **OAUTH)
class FacebookReferralTests(OAuthBase):
    def test_a_valid_code_sets_referred_by_on_a_new_facebook_account(self):
        response = self.facebook(self.referrer.referral_code)

        self.assertTrue(response.json()["created"])
        self.assertEqual(self.referred_by(), self.referrer)

    def test_an_invalid_code_is_ignored(self):
        response = self.facebook("ZZZZZZZZ")

        self.assertTrue(response.json()["created"])
        self.assertIsNone(self.referred_by())

    def test_an_existing_account_is_unchanged(self):
        Account.objects.create(email=NEW_EMAIL)
        response = self.facebook(self.referrer.referral_code)

        self.assertFalse(response.json()["created"])
        self.assertIsNone(self.referred_by())

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_switch_off_has_no_effect(self):
        self.facebook(self.referrer.referral_code)

        self.assertIsNone(self.referred_by())
