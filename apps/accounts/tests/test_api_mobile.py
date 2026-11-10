"""T-MOB: mobile verification on Account Settings."""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts import mobile
from apps.accounts.models import Account, AccountAuditEvent, AccountMobile, MobileOtpChallenge
from apps.accounts.tests.base import EMAIL, ApiTestCase
from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.tests.base import gateway_on, online_phone, set_policy

MOBILE = "+639171234567"
MASKED = "+63917***4567"
OTHER = "+639189999999"
START = "/v1/account/mobile/start/"
VERIFY = "/v1/account/mobile/verify/"
NOT_VERIFIED = {"verified": False, "mobile_masked": ""}
MOBILE_TABLES = ("accounts_accountmobile", "accounts_mobileotpchallenge", "notifications_sms")


@gateway_on
class MobileCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.account = Account.objects.get(email=EMAIL)
        self.phone = online_phone()
        mail.outbox.clear()

    def start(self, number=MOBILE, client=None):
        return self.post(START, {"mobile": number}, client=client)

    def verify(self, code, client=None):
        return self.post(VERIFY, {"code": code}, client=client)

    def texted_code(self):
        """The code in the newest queued text, read the way the phone would get it."""
        text = sms.take_text(SmsMessage.objects.latest("pk"))
        return re.search(r"\b(\d{6})\b", text).group(1)

    def wrong(self, code):
        return "000000" if code != "000000" else "111111"

    def verified(self, number=MOBILE):
        self.start(number)
        response = self.verify(self.texted_code())
        self.assertEqual(response.status_code, 200)
        cache.clear()
        return response

    def error(self, response):
        return response.json()["error"]


class MobileStartTests(MobileCase):
    def test_1_every_form_of_a_philippine_number_is_accepted(self):
        forms = ("+639171234567", "09171234567", "9171234567", "639171234567",
                 "0917 123 4567", "+63 (917) 123-4567")
        for form in forms:
            with self.subTest(form=form):
                cache.clear()
                response = self.start(form)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(SmsMessage.objects.latest("pk").recipient, MOBILE)

    def test_1_a_number_on_any_network_is_accepted(self):
        for number in ("+639171234567", "+639181234567", "+639221234567", "+639951234567"):
            with self.subTest(number=number):
                cache.clear()
                self.assertEqual(self.start(number).status_code, 200)

    def test_2_a_bad_number_answers_invalid_mobile(self):
        for bad in ("", "12345", "+14155550100", "0817123456", "0917123456789", "mobile"):
            with self.subTest(bad=bad):
                response = self.start(bad)
                self.assertEqual(response.status_code, 400)
                error = self.error(response)
                self.assertEqual(error["code"], "invalid_mobile")
                self.assertEqual(error["message"], "Enter a valid Philippine mobile number.")
                self.assertEqual(
                    error["fields"], {"mobile": ["Enter a valid Philippine mobile number."]}
                )
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertEqual(MobileOtpChallenge.objects.count(), 0)

    def test_2_an_oversized_number_is_refused_by_the_schema(self):
        self.assertEqual(self.start("+63 (917) 123-4567").status_code, 200)  # 18 characters

        response = self.start("0917" + "1" * 17)  # 21 characters

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error(response)["code"], "invalid_request")
        self.assertIn("mobile", self.error(response)["fields"])
        self.assertEqual(SmsMessage.objects.count(), 1)

    def test_2_the_accounts_own_verified_number_answers_mobile_unchanged(self):
        self.verified()

        response = self.start("0917 123 4567")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error(response)["code"], "mobile_unchanged")
        self.assertEqual(
            self.error(response)["fields"], {"mobile": ["That is already your mobile number."]}
        )
        self.assertEqual(SmsMessage.objects.count(), 1)

    def test_3_three_starts_per_account_in_15_minutes(self):
        for number in ("+639170000001", "+639170000002", "+639170000003"):
            self.assertEqual(self.start(number).status_code, 200)

        refused = self.start("+639170000004")

        self.assertEqual(refused.status_code, 429)
        self.assertEqual(self.error(refused)["code"], "rate_limited")
        self.assertEqual(SmsMessage.objects.count(), 3)

    def test_3_three_starts_per_number_in_15_minutes(self):
        other = Client()
        self.sign_in("ben@example.test", client=other)
        cache.clear()
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.start(client=other).status_code, 200)

        refused = self.start(client=other)

        self.assertEqual(refused.status_code, 429)
        self.assertEqual(SmsMessage.objects.count(), 3)

    def test_3_ten_starts_per_number_in_a_day(self):
        key = f"throttle:mobile-start-number-day:{mobile._number_key(MOBILE)}"
        cache.set(key, 9, 86400)
        self.assertEqual(self.start().status_code, 200)

        self.assertEqual(self.start().status_code, 429)
        self.assertEqual(self.start(OTHER).status_code, 200)

    def test_3_twenty_starts_per_ip_in_an_hour(self):
        cache.set("throttle:mobile-start-ip:127.0.0.1", 19, 3600)
        self.assertEqual(self.start().status_code, 200)

        self.assertEqual(self.start(OTHER).status_code, 429)

    def test_3_no_cache_key_holds_a_number(self):
        self.start()

        for key in cache._cache:  # the local-memory cache of the tests
            self.assertNotIn("9171234567", key)

    def test_4_start_answers_queued_and_queues_one_text(self):
        response = self.start("09171234567")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"queued": True})
        message = SmsMessage.objects.get()
        self.assertEqual(message.purpose, "mobile_verify")
        self.assertEqual(message.category, "code")
        self.assertEqual(message.status, SmsMessage.Status.QUEUED)
        self.assertEqual(message.recipient, MOBILE)
        self.assertFalse(message.emailed)
        challenge = MobileOtpChallenge.objects.get()
        self.assertEqual(challenge.account, self.account)
        self.assertEqual(challenge.number, MOBILE)
        self.assertEqual(challenge.sms_public_id, message.public_id)
        self.assertEqual(challenge.attempts, 0)
        self.assertAlmostEqual(
            challenge.expires_at, timezone.now() + timedelta(minutes=10), delta=timedelta(seconds=5)
        )
        self.assertFalse(AccountMobile.objects.exists())
        self.assertEqual(mail.outbox, [])  # a code is never offered by email instead

    def test_4_the_text_is_the_approved_wording(self):
        self.start()

        text = sms.take_text(SmsMessage.objects.get())

        self.assertRegex(
            text,
            r"^Your NegosyoOnline code is \d{6}\. It expires in 10 minutes\. Do not share it\.$",
        )
        self.assertEqual(sms.text_problem(text), "")  # one plain SMS, no link

    def test_4_the_code_is_in_no_database_row(self):
        self.start()
        code = self.texted_code()

        challenge = MobileOtpChallenge.objects.get()
        for field in MobileOtpChallenge._meta.concrete_fields:
            self.assertNotIn(code, str(getattr(challenge, field.attname)), field.name)
        self.assertEqual(len(challenge.code_hash), 64)
        self.assertNotIn(MOBILE, str(challenge))

    def test_5_a_new_start_supersedes_the_old_challenge(self):
        self.start()
        first = self.texted_code()
        self.start(OTHER)
        second = self.texted_code()

        old, new = MobileOtpChallenge.objects.order_by("pk")
        self.assertIsNotNone(old.superseded_at)
        self.assertIsNone(new.superseded_at)
        if first != second:
            self.assertEqual(self.error(self.verify(first))["code"], "wrong_code")
        response = self.verify(second)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AccountMobile.objects.get().number, OTHER)

    def test_11_gateway_off_answers_503_and_touches_no_mobile_table(self):
        with override_settings(SMS_GATEWAY_ENABLED=False), CaptureQueriesContext(connection) as seen:
            started = self.start()
            checked = self.verify("123456")
            account = self.get("/v1/account/")

        for response in (started, checked):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.error(response)["code"], "sms_unavailable")
            self.assertEqual(
                self.error(response)["message"],
                "Text messages are not available right now. Please try again later.",
            )
        self.assertEqual(account.status_code, 200)
        self.assertEqual(account.json()["mobile"], NOT_VERIFIED)
        for query in seen.captured_queries:
            for table in MOBILE_TABLES:
                self.assertNotIn(table, query["sql"])

    def test_11_a_deployment_without_redis_counts_as_off(self):
        with override_settings(REDIS_URL=None):
            self.assertEqual(self.error(self.start())["code"], "sms_unavailable")

    def test_11_phone_offline_answers_sms_unavailable(self):
        SmsDevice.objects.update(last_contact_at=timezone.now() - timedelta(minutes=5))

        response = self.start()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.error(response)["code"], "sms_unavailable")
        self.assertEqual(MobileOtpChallenge.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_11_sending_stopped_answers_sms_paused(self):
        set_policy(sending_stopped=True)

        response = self.start()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.error(response)["code"], "sms_paused")
        self.assertEqual(self.error(response)["message"], "Please try again shortly.")
        self.assertEqual(MobileOtpChallenge.objects.count(), 0)

    def test_11_the_platform_limit_answers_sms_paused(self):
        set_policy(platform_daily_limit=0)

        response = self.start()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.error(response)["code"], "sms_paused")

    def test_11_a_refused_start_leaves_the_earlier_code_usable(self):
        self.start()
        code = self.texted_code()
        set_policy(sending_stopped=True)
        self.start(OTHER)

        self.assertEqual(self.verify(code).status_code, 200)
        self.assertEqual(AccountMobile.objects.get().number, MOBILE)

    def test_12_signed_out_calls_answer_401(self):
        self.client.cookies.clear()

        self.assertEqual(self.start().status_code, 401)
        self.assertEqual(self.verify("123456").status_code, 401)
        self.assertEqual(SmsMessage.objects.count(), 0)


class MobileVerifyTests(MobileCase):
    def test_6_the_right_code_verifies_the_number(self):
        self.start()

        response = self.verify(self.texted_code())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"verified": True, "mobile_masked": MASKED})
        held = AccountMobile.objects.get()
        self.assertEqual(held.account, self.account)
        self.assertEqual(held.number, MOBILE)
        self.assertIsNotNone(held.verified_at)
        self.assertIsNotNone(MobileOtpChallenge.objects.get().used_at)
        self.assertNotIn(MOBILE, str(held))

    def test_6_a_code_typed_with_spaces_is_accepted(self):
        self.start()
        code = self.texted_code()

        self.assertEqual(self.verify(f" {code[:3]} {code[3:]} ").status_code, 200)

    def test_7_a_wrong_code(self):
        self.start()

        response = self.verify(self.wrong(self.texted_code()))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error(response)["code"], "wrong_code")
        self.assertEqual(
            self.error(response)["fields"], {"code": ["That code did not work. Please try again."]}
        )
        self.assertEqual(self.error(response)["message"], "That code did not work. Please try again.")
        self.assertEqual(MobileOtpChallenge.objects.get().attempts, 1)
        self.assertFalse(AccountMobile.objects.exists())

    def test_7_an_expired_code(self):
        self.start()
        code = self.texted_code()
        MobileOtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        response = self.verify(code)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error(response)["code"], "code_expired")
        self.assertEqual(
            self.error(response)["fields"], {"code": ["This code has expired. Request a new one."]}
        )
        self.assertFalse(AccountMobile.objects.exists())

    def test_7_a_used_code(self):
        self.start()
        code = self.texted_code()
        self.assertEqual(self.verify(code).status_code, 200)

        again = self.verify(code)

        self.assertEqual(again.status_code, 400)
        self.assertEqual(self.error(again)["code"], "code_expired")

    def test_7_an_oversized_code_is_refused_by_the_schema(self):
        self.start()

        response = self.verify("1" * 21)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error(response)["code"], "invalid_request")
        self.assertIn("code", self.error(response)["fields"])
        self.assertEqual(MobileOtpChallenge.objects.get().attempts, 0)  # it used no try

    def test_7_no_code_was_asked_for(self):
        response = self.verify("123456")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error(response)["code"], "code_expired")

    def test_7_the_sixth_try_is_refused_even_when_right(self):
        self.start()
        code = self.texted_code()
        for _ in range(5):
            self.assertEqual(self.error(self.verify(self.wrong(code)))["code"], "wrong_code")

        sixth = self.verify(code)

        self.assertEqual(sixth.status_code, 400)
        self.assertEqual(self.error(sixth)["code"], "code_burned")
        self.assertEqual(
            self.error(sixth)["fields"], {"code": ["Too many wrong codes. Request a new one."]}
        )
        self.assertFalse(AccountMobile.objects.exists())

    def test_7_ten_tries_per_account_in_15_minutes(self):
        for _ in range(10):
            self.assertEqual(self.verify("123456").status_code, 400)

        self.assertEqual(self.verify("123456").status_code, 429)

    def test_another_accounts_code_does_not_work(self):
        other = Client()
        self.sign_in("ben@example.test", client=other)
        cache.clear()
        self.start()
        code = self.texted_code()

        response = self.verify(code, client=other)

        self.assertEqual(self.error(response)["code"], "code_expired")
        self.assertFalse(AccountMobile.objects.exists())

    def test_two_accounts_may_hold_the_same_number(self):
        other = Client()
        self.sign_in("ben@example.test", client=other)
        self.verified()
        self.start(client=other)

        self.assertEqual(self.verify(self.texted_code(), client=other).status_code, 200)
        self.assertEqual(AccountMobile.objects.filter(number=MOBILE).count(), 2)

    def test_8_the_account_shows_the_mobile_object(self):
        before = self.get("/v1/account/").json()["mobile"]
        self.verified()
        after = self.get("/v1/account/").json()["mobile"]
        patched = self.send("patch", "/v1/account/", {"name": "Ana"}).json()["mobile"]

        self.assertEqual(before, NOT_VERIFIED)
        self.assertEqual(after, {"verified": True, "mobile_masked": MASKED})
        self.assertEqual(patched, after)

    def test_9_the_full_number_is_in_no_response(self):
        bodies = [self.start().content.decode()]
        bodies.append(self.verify(self.texted_code()).content.decode())
        bodies.append(self.get("/v1/account/").content.decode())
        bodies.append(self.start().content.decode())  # mobile_unchanged

        for body in bodies:
            self.assertNotIn(MOBILE, body)
            self.assertNotIn("9171234567", body)

    def test_10_changing_a_verified_number_emails_the_notice_and_texts_nobody(self):
        self.verified()
        mail.outbox.clear()
        self.start(OTHER)

        response = self.verify(self.texted_code())

        self.assertEqual(response.json(), {"verified": True, "mobile_masked": "+63918***9999"})
        self.assertEqual(AccountMobile.objects.get().number, OTHER)
        notice = mail.outbox[-1]
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(notice.to, [EMAIL])
        self.assertEqual(notice.subject, "Your NegosyoOnline mobile number changed")
        self.assertEqual(
            notice.body,
            "The mobile number on your NegosyoOnline account was changed to the number ending "
            "in 9999. If you did not make this change, sign in and update it.",
        )
        # One text each for the two codes; nothing to the old number about the change.
        self.assertEqual(SmsMessage.objects.count(), 2)
        self.assertEqual(SmsMessage.objects.filter(recipient=MOBILE).count(), 1)
        self.assertEqual(
            list(self.account.audit_events.filter(event__startswith="mobile_")
                 .order_by("pk").values_list("event", flat=True)),
            ["mobile_verified", "mobile_changed"],
        )

    def test_10_a_first_number_sends_no_notice(self):
        self.verified()

        self.assertEqual(mail.outbox, [])

    def test_10_a_failed_notice_email_leaves_the_change_in_place(self):
        self.verified()
        self.start(OTHER)
        code = self.texted_code()

        with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("down")):
            response = self.verify(code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AccountMobile.objects.get().number, OTHER)

    def test_10_get_account_mobile_is_not_built(self):
        self.assertEqual(self.get("/v1/account/mobile/").status_code, 404)

    def test_13_the_audit_event_holds_no_number(self):
        self.verified()
        self.start(OTHER)
        self.verify(self.texted_code())

        events = AccountAuditEvent.objects.filter(event__startswith="mobile_")
        self.assertEqual(events.count(), 2)
        for event in events:
            self.assertEqual(event.detail, {})
