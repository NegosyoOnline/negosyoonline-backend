from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity, Session
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
        self.assertEqual(response.json(), {"revoked": 2, "signed_in_count": 1})
        self.assertEqual(len(self.get("/v1/account/sessions/").json()["items"]), 1)

    def test_sign_out_others_leaves_another_account_signed_in(self):
        stranger = Account.objects.create(email="stranger@example.test")
        start_session(stranger, RequestFactory().post("/"))

        self.post("/v1/account/sessions/sign-out-others/")

        self.assertTrue(Session.objects.filter(account=stranger, revoked_at__isnull=True).exists())


class AccountSettingsReadTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.account = Account.objects.get(email=EMAIL)

    def test_read_has_everything_the_page_reads(self):
        body = self.get("/v1/account/").json()

        self.assertEqual(body["email"], EMAIL)
        self.assertIs(body["writes_open"], True)
        self.assertIsNone(body["email_changed_at"])
        self.assertIsNone(body["email_change_available_at"])
        self.assertIsNone(body["mobile"])
        self.assertEqual(
            body["providers"],
            [
                {"provider": "facebook", "connected": False},
                {"provider": "google", "connected": False},
            ],
        )
        self.assertEqual(body["signed_in_count"], 1)
        self.assertEqual(len(body["devices"]), 1)
        self.assertEqual(
            set(body["devices"][0]), {"name", "current", "signed_in_at", "last_used_at", "ip"}
        )
        self.assertIs(body["devices"][0]["current"], True)

    def test_read_shows_only_my_providers_and_my_live_devices(self):
        factory = RequestFactory()
        ExternalIdentity.objects.create(account=self.account, provider="google", provider_user_id="g-1")
        start_session(self.account, factory.post("/", HTTP_USER_AGENT="Firefox/130 Windows"))
        revoked, _ = start_session(self.account, factory.post("/"))
        Session.objects.filter(pk=revoked.pk).update(revoked_at=timezone.now())
        expired, _ = start_session(self.account, factory.post("/"))
        Session.objects.filter(pk=expired.pk).update(expires_at=timezone.now())
        stranger = Account.objects.create(email="stranger@example.test")
        ExternalIdentity.objects.create(account=stranger, provider="facebook", provider_user_id="f-9")
        start_session(stranger, factory.post("/", HTTP_USER_AGENT="Edg/1 Android"))

        body = self.get("/v1/account/").json()

        self.assertEqual(
            body["providers"],
            [
                {"provider": "facebook", "connected": False},
                {"provider": "google", "connected": True},
            ],
        )
        self.assertEqual(body["signed_in_count"], 2)
        self.assertEqual([d["current"] for d in body["devices"]].count(True), 1)
        self.assertIn("Firefox on Windows", [d["name"] for d in body["devices"]])
        self.assertNotIn("Edge on Android", [d["name"] for d in body["devices"]])
        self.assertNotIn("g-1", str(body))

    def test_patch_answers_with_the_full_read_and_leaves_other_accounts_alone(self):
        stranger = Account.objects.create(email="stranger@example.test", name="Someone Else")

        body = self.send("patch", "/v1/account/", {"name": "Ana Reyes"}).json()

        self.assertEqual(body["name"], "Ana Reyes")
        self.assertIn("providers", body)
        self.assertIn("devices", body)
        stranger.refresh_from_db()
        self.assertEqual(stranger.name, "Someone Else")

    def test_a_write_from_another_origin_is_refused(self):
        for method, path in (
            ("patch", "/v1/account/"),
            ("post", "/v1/account/email/start/"),
            ("post", "/v1/account/email/confirm/"),
            ("post", "/v1/account/sessions/sign-out-others/"),
        ):
            response = self.send(method, path, {}, HTTP_ORIGIN="https://evil.example.test")
            self.assertEqual(response.status_code, 403, path)


class EmailChangeTests(ApiTestCase):
    NEW = "new@example.test"

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.account = Account.objects.get(email=EMAIL)
        cache.clear()

    def change_email(self, to=NEW):
        self.post("/v1/account/email/start/", {"email": to})
        return self.post("/v1/account/email/confirm/", {"email": to, "code": self.last_code()})

    def test_confirm_needs_a_session(self):
        self.client.cookies.clear()

        response = self.post("/v1/account/email/confirm/", {"email": self.NEW, "code": "123456"})

        self.assertEqual(response.status_code, 401)

    def test_confirm_tells_the_old_address_without_naming_the_new_one(self):
        self.change_email()

        notice = mail.outbox[-1]
        self.assertEqual(notice.to, [EMAIL])
        self.assertIn("was changed", notice.subject)
        self.assertNotIn(self.NEW, notice.body)
        self.assertEqual(len([m for m in mail.outbox if m.to == [EMAIL] and "changed" in m.subject]), 1)

    def test_confirm_signs_out_other_devices_and_keeps_this_one(self):
        other, _ = start_session(self.account, RequestFactory().post("/"))

        response = self.change_email()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["signed_in_count"], 1)
        other.refresh_from_db()
        self.assertIsNotNone(other.revoked_at)
        self.assertEqual(self.get("/v1/account/").status_code, 200)

    def test_confirm_answers_with_the_pause(self):
        body = self.change_email().json()

        self.assertEqual(body["email"], self.NEW)
        self.assertIsNotNone(body["email_changed_at"])
        changed = AccountAuditEvent.objects.get(account=self.account, event="email_changed").created_at
        self.assertEqual(
            body["email_change_available_at"][:19],
            (changed + timedelta(hours=24)).isoformat()[:19],
        )

    def test_a_second_change_inside_24_hours_is_refused_at_start_and_at_confirm(self):
        self.change_email()
        cache.clear()

        started = self.post("/v1/account/email/start/", {"email": "third@example.test"})
        confirmed = self.post(
            "/v1/account/email/confirm/", {"email": "third@example.test", "code": "123456"}
        )

        for response in (started, confirmed):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "email_change_cooldown")
        self.account.refresh_from_db()
        self.assertEqual(self.account.email, self.NEW)

    def test_the_pause_ends_after_24_hours(self):
        self.change_email()
        AccountAuditEvent.objects.filter(account=self.account, event="email_changed").update(
            created_at=timezone.now() - timedelta(hours=24, minutes=1)
        )
        cache.clear()

        body = self.get("/v1/account/").json()
        started = self.post("/v1/account/email/start/", {"email": "third@example.test"})

        self.assertIsNone(body["email_change_available_at"])
        self.assertIsNone(body["email_changed_at"])
        self.assertEqual(started.status_code, 204)

    def test_another_accounts_email_change_does_not_pause_mine(self):
        stranger = Account.objects.create(email="stranger@example.test")
        AccountAuditEvent.objects.create(account=stranger, event="email_changed")

        self.assertEqual(self.post("/v1/account/email/start/", {"email": self.NEW}).status_code, 204)

    def test_a_wrong_code_changes_nothing_and_sends_no_notice(self):
        self.post("/v1/account/email/start/", {"email": self.NEW})
        wrong = "000000" if self.last_code() != "000000" else "111111"
        sent = len(mail.outbox)

        response = self.post("/v1/account/email/confirm/", {"email": self.NEW, "code": wrong})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_code")
        self.assertEqual(len(mail.outbox), sent)
        self.account.refresh_from_db()
        self.assertEqual(self.account.email, EMAIL)

    def test_a_code_sent_to_one_account_cannot_change_another(self):
        self.post("/v1/account/email/start/", {"email": self.NEW})
        code = self.last_code()
        other = Client()
        self.sign_in("stranger@example.test", client=other)

        response = self.post(
            "/v1/account/email/confirm/", {"email": self.NEW, "code": code}, client=other
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Account.objects.get(email="stranger@example.test").email, "stranger@example.test")

    def test_the_change_stands_when_the_notice_cannot_be_sent(self):
        self.post("/v1/account/email/start/", {"email": self.NEW})
        code = self.last_code()

        with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("smtp down")):
            response = self.post("/v1/account/email/confirm/", {"email": self.NEW, "code": code})

        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.email, self.NEW)
