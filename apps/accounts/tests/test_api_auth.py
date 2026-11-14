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


class DuplicateSessionCookieTests(ApiTestCase):
    """A stale `Path=/` copy of the cookie must not shadow the fresh `Path=/v1/` one."""

    def fresh_token(self):
        return self.sign_in().cookies["no_session"].value

    def me(self, header):
        return Client().get("/v1/me/", secure=True, HTTP_COOKIE=header)

    def session_set_cookies(self, response):
        return [m.OutputString() for m in response.cookies.values() if m.key == "no_session"]

    def test_stale_duplicate_after_the_fresh_cookie_still_signs_in(self):
        fresh = self.fresh_token()

        self.assertEqual(self.me(f"no_session={fresh}; no_session=stale").status_code, 200)

    def test_stale_value_first_then_fresh_also_signs_in(self):
        fresh = self.fresh_token()

        self.assertEqual(self.me(f"no_session=stale; no_session={fresh}").status_code, 200)

    def test_only_stale_values_are_401(self):
        self.fresh_token()

        self.assertEqual(self.me("no_session=one; no_session=two").status_code, 401)

    def test_single_valid_cookie_works_and_single_invalid_is_401(self):
        fresh = self.fresh_token()

        self.assertEqual(self.me(f"other=x; no_session={fresh}").status_code, 200)
        self.assertEqual(self.me("no_session=stale").status_code, 401)
        self.assertEqual(self.me("other=x").status_code, 401)

    def test_verify_and_logout_also_expire_the_root_path_cookie(self):
        lines = self.session_set_cookies(self.sign_in())

        self.assertEqual(len(lines), 2)
        fresh = [line for line in lines if "Path=/v1/" in line]
        root = [line for line in lines if "Path=/v1/" not in line]
        self.assertEqual(len(fresh), 1)
        self.assertNotIn("Max-Age=0", fresh[0])
        self.assertEqual(len(root), 1)
        self.assertIn("Max-Age=0", root[0])
        self.assertIn("Path=/;", root[0] + ";")

        lines = self.session_set_cookies(self.post("/v1/auth/logout/"))

        self.assertEqual(len(lines), 2)
        self.assertEqual(sorted("Path=/v1/" in line for line in lines), [False, True])
        self.assertTrue(all("Max-Age=0" in line for line in lines))

    def test_other_responses_do_not_set_the_session_cookie_at_the_root_path(self):
        self.sign_in()
        request_code = self.post("/v1/auth/email-code/request/", {"email": EMAIL})

        self.assertEqual(self.session_set_cookies(self.get("/v1/me/")), [])
        self.assertEqual(self.session_set_cookies(request_code), [])


def email_of_length(length):
    domain = ".".join(["d" * 60] * 3) + ".test"
    local = "a" * (length - len(domain) - 1)
    return f"{local}@{domain}"


class EmailLengthTests(ApiTestCase):
    def test_254_is_accepted_and_255_rejected_on_code_request(self):
        ok = self.post("/v1/auth/email-code/request/", {"email": email_of_length(254)})
        self.assertEqual(ok.status_code, 204)
        self.assertEqual(len(mail.outbox), 1)

        too_long = self.post("/v1/auth/email-code/request/", {"email": email_of_length(255)})
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("email", too_long.json()["error"]["fields"])
        self.assertEqual(len(mail.outbox), 1)

    def test_254_is_accepted_and_255_rejected_on_verify(self):
        long_email = email_of_length(254)
        self.post("/v1/auth/email-code/request/", {"email": long_email})
        code = self.last_code()

        too_long = self.post(
            "/v1/auth/email-code/verify/",
            {"email": email_of_length(255), "code": code, "accept_policies": True},
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("email", too_long.json()["error"]["fields"])
        self.assertFalse(Account.objects.exists())

        ok = self.post(
            "/v1/auth/email-code/verify/", {"email": long_email, "code": code, "accept_policies": True}
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["account"]["email"], long_email)
