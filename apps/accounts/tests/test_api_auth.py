from django.core import mail
from django.test import Client

from apps.accounts.models import Account, PolicyAcceptance
from apps.accounts.tests.base import EMAIL, ApiTestCase


class SignInApiTests(ApiTestCase):
    def test_request_sends_a_code_and_answers_204(self):
        response = self.post("/v1/auth/email-code/request/", {"email": EMAIL})

        self.assertEqual(response.status_code, 204)
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        self.assertFalse(Account.objects.exists())

    def test_invalid_email_answers_400_with_the_field(self):
        response = self.post("/v1/auth/email-code/request/", {"email": "nope"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["error"]["fields"])

    def test_verify_creates_the_account_and_sets_the_cookie(self):
        response = self.sign_in()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["account"]["email"], EMAIL)
        self.assertTrue(body["account"]["id"].startswith("acc_"))
        self.assertNotIn("referral_code", body["account"])
        cookie = response.cookies["no_session"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertTrue(PolicyAcceptance.objects.filter(account__email=EMAIL).exists())

    def test_me_returns_the_signed_in_account(self):
        self.sign_in()

        response = self.get("/v1/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account"]["email"], EMAIL)

    def test_me_without_a_session_is_401(self):
        response = self.get("/v1/me/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthenticated")

    def test_new_account_without_policies_is_refused_then_succeeds_with_the_same_code(self):
        self.post("/v1/auth/email-code/request/", {"email": EMAIL})
        payload = {"email": EMAIL, "code": self.last_code()}

        refused = self.post("/v1/auth/email-code/verify/", payload)
        accepted = self.post("/v1/auth/email-code/verify/", {**payload, "accept_policies": True})

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"]["code"], "policies_required")
        self.assertNotIn("no_session", refused.cookies)
        self.assertEqual(accepted.status_code, 200)

    def test_wrong_code_is_400(self):
        self.post("/v1/auth/email-code/request/", {"email": EMAIL})
        wrong = "000000" if self.last_code() != "000000" else "111111"

        response = self.post(
            "/v1/auth/email-code/verify/", {"email": EMAIL, "code": wrong, "accept_policies": True}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_code")

    def test_second_code_within_a_minute_is_429(self):
        self.post("/v1/auth/email-code/request/", {"email": EMAIL})

        response = self.post("/v1/auth/email-code/request/", {"email": EMAIL})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")

    def test_signing_in_again_signs_out_the_earlier_session(self):
        self.sign_in()
        other_device = Client()

        self.sign_in(client=other_device)

        self.assertEqual(self.get("/v1/me/").status_code, 401)
        self.assertEqual(self.get("/v1/me/", client=other_device).status_code, 200)

    def test_logout_revokes_the_session_and_clears_the_cookie(self):
        self.sign_in()

        response = self.post("/v1/auth/logout/")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.cookies["no_session"].value, "")
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    def test_writes_without_an_origin_are_403(self):
        request_code = self.post(
            "/v1/auth/email-code/request/", {"email": EMAIL}, HTTP_ORIGIN=None
        )
        self.sign_in()
        logout = self.post("/v1/auth/logout/", HTTP_ORIGIN=None)

        self.assertEqual(request_code.status_code, 403)
        self.assertEqual(request_code.json()["error"]["code"], "origin_required")
        self.assertEqual(logout.status_code, 403)
        self.assertEqual(self.get("/v1/me/").status_code, 200)
