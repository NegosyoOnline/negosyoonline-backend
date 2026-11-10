from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory

from apps.accounts.models import Account
from apps.accounts.sessions import start_session
from apps.accounts.tests.base import EMAIL, ApiTestCase


class AccountApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.account = Account.objects.get(email=EMAIL)

    def test_every_account_endpoint_needs_a_session(self):
        self.client.cookies.clear()

        self.assertEqual(self.get("/v1/account/").status_code, 401)
        self.assertEqual(self.get("/v1/account/sessions/").status_code, 401)
        self.assertEqual(self.send("patch", "/v1/account/", {"name": "x"}).status_code, 401)
        self.assertEqual(self.post("/v1/account/email/start/", {"email": "n@example.test"}).status_code, 401)
        self.assertEqual(self.post("/v1/account/sessions/sign-out-others/").status_code, 401)

    def test_get_and_patch_account(self):
        patched = self.send("patch", "/v1/account/", {"name": "  Ana Reyes "})
        fetched = self.get("/v1/account/")

        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["name"], "Ana Reyes")
        self.assertEqual(fetched.json()["name"], "Ana Reyes")
        self.assertEqual(fetched.json()["email"], EMAIL)

    def test_name_longer_than_120_characters_is_400(self):
        response = self.send("patch", "/v1/account/", {"name": "x" * 121})

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["error"]["fields"])

    def test_email_change_start_then_confirm(self):
        cache.clear()

        started = self.post("/v1/account/email/start/", {"email": "new@example.test"})
        self.assertEqual(started.status_code, 204)
        self.assertEqual(mail.outbox[-1].to, ["new@example.test"])

        confirmed = self.post(
            "/v1/account/email/confirm/", {"email": "new@example.test", "code": self.last_code()}
        )

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["email"], "new@example.test")
        self.assertEqual(self.get("/v1/me/").json()["account"]["email"], "new@example.test")

    def test_email_change_to_a_taken_email_is_409(self):
        Account.objects.create(email="taken@example.test")
        cache.clear()

        response = self.post("/v1/account/email/start/", {"email": "taken@example.test"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "email_taken")

    def test_sessions_list_shows_only_my_live_sessions_and_marks_the_current_one(self):
        factory = RequestFactory()
        start_session(self.account, factory.post("/", HTTP_USER_AGENT="Firefox/130 Windows"))
        stranger = Account.objects.create(email="stranger@example.test")
        start_session(stranger, factory.post("/"))

        body = self.get("/v1/account/sessions/").json()

        self.assertEqual(len(body["items"]), 2)
        self.assertIsNone(body["next_cursor"])
        self.assertEqual([item["current"] for item in body["items"]].count(True), 1)
        self.assertIn("Firefox on Windows", [item["device_label"] for item in body["items"]])
        self.assertTrue(all(item["id"].startswith("ses_") for item in body["items"]))
        self.assertNotIn("token_hash", body["items"][0])

    def test_sign_out_others_keeps_the_current_session(self):
        start_session(self.account, RequestFactory().post("/"))
        start_session(self.account, RequestFactory().post("/"))

        response = self.post("/v1/account/sessions/sign-out-others/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"revoked": 2})
        self.assertEqual(len(self.get("/v1/account/sessions/").json()["items"]), 1)
