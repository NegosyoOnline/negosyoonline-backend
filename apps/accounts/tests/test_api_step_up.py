"""T-STEPUP: the first gate of an email or mobile change, from either verified channel."""
import json
import re
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Account, AccountAuditEvent, AccountMobile
from apps.accounts.tests.base import EMAIL, ApiTestCase
from apps.notifications import sms
from apps.notifications.models import SmsMessage
from apps.notifications.services import EmailNotSent
from apps.notifications.tests.base import gateway_on, online_phone

STEP_UP = "/v1/account/step-up/"
VERIFY = "/v1/account/step-up/verify/"
WRONG_CODE = "That code is wrong or has expired. Ask for a new one."
MOBILE = "+639171234567"
NEW_MOBILE = "+639189999999"
NEW_EMAIL = "new@example.test"


class StepUpCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.account = Account.objects.get(email=EMAIL)
        cache.clear()
        mail.outbox.clear()

    def hold_mobile(self):
        AccountMobile.objects.create(account=self.account, number=MOBILE, verified_at=timezone.now())

    def step_up(self, channel, purpose, expect=204):
        response = self.post(STEP_UP, {"channel": channel, "purpose": purpose})
        self.assertEqual(response.status_code, expect, response.content)
        return response

    def emailed_code(self):
        return self.last_code()

    def texted_code(self):
        text = sms.take_text(SmsMessage.objects.latest("pk"))
        return re.search(r"\b(\d{6})\b", text).group(1)

    def start_email(self, code, email=NEW_EMAIL):
        return self.post("/v1/account/email/start/", {"email": email, "step_up_code": code})

    def start_mobile(self, code, number=NEW_MOBILE):
        return self.post("/v1/account/mobile/start/", {"mobile": number, "step_up_code": code})


@override_settings(ACCOUNT_EMAIL_CHANGE_HARDENED=False)
class SwitchOffTests(StepUpCase):
    def test_the_new_route_is_404_and_the_read_lists_no_channel(self):
        self.step_up("email", "email_change", expect=404)
        body = self.get("/v1/account/").json()
        self.assertIs(body["email_change_step_up"], False)
        self.assertEqual(body["step_up_channels"], [])

    def test_the_verify_route_is_404(self):
        self.assertEqual(
            self.post(VERIFY, {"purpose": "email_change", "code": "123456"}).status_code, 404
        )

    def test_the_old_route_is_404(self):
        self.assertEqual(self.post("/v1/account/email/step-up/").status_code, 404)

    @gateway_on
    def test_a_mobile_add_needs_no_code_while_off(self):
        online_phone()
        self.assertEqual(self.post("/v1/account/mobile/start/", {"mobile": NEW_MOBILE}).status_code, 200)


@override_settings(ACCOUNT_EMAIL_CHANGE_HARDENED=True)
class EmailChannelTests(StepUpCase):
    def test_the_read_lists_email_only_without_a_mobile(self):
        self.assertEqual(self.get("/v1/account/").json()["step_up_channels"], ["email"])

    def test_email_channel_for_an_email_change(self):
        self.step_up("email", "email_change")
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        self.assertEqual(self.start_email(self.emailed_code()).status_code, 204)
        self.assertEqual(mail.outbox[-1].to, [NEW_EMAIL])

    def test_email_channel_for_a_first_mobile_add(self):
        self.step_up("email", "mobile_change")
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        with gateway_on:
            online_phone()
            response = self.start_mobile(self.emailed_code())
        self.assertEqual(response.status_code, 200)

    def test_the_old_route_is_an_alias_of_email_email_change(self):
        self.assertEqual(self.post("/v1/account/email/step-up/").status_code, 204)
        self.assertEqual(self.start_email(self.emailed_code()).status_code, 204)

    def test_the_mobile_channel_is_unavailable_without_a_mobile(self):
        self.step_up("mobile", "email_change", expect=400)
        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_channel_or_purpose_is_refused(self):
        self.step_up("fax", "email_change", expect=400)
        self.step_up("email", "password_change", expect=400)

    def test_a_taken_address_keeps_the_neutral_answer(self):
        Account.objects.create(email="taken@example.test")
        self.step_up("email", "email_change")
        before = len(mail.outbox)
        response = self.start_email(self.emailed_code(), "taken@example.test")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(mail.outbox), before)

    def test_a_failed_send_gives_the_wait_back(self):
        with mock.patch("apps.accounts.services.send_email", side_effect=EmailNotSent()):
            self.step_up("email", "email_change", expect=503)
        self.step_up("email", "email_change")


@override_settings(ACCOUNT_EMAIL_CHANGE_HARDENED=True)
@gateway_on
class MobileChannelTests(StepUpCase):
    def setUp(self):
        super().setUp()
        self.hold_mobile()
        online_phone()

    def test_the_read_lists_both_channels(self):
        self.assertEqual(self.get("/v1/account/").json()["step_up_channels"], ["email", "mobile"])

    def test_mobile_channel_for_an_email_change(self):
        self.step_up("mobile", "email_change")
        message = SmsMessage.objects.latest("pk")
        self.assertEqual(message.recipient, MOBILE)
        self.assertEqual(len(mail.outbox), 0)
        text = sms.take_text(message)
        self.assertIn("It expires in 10 minutes. Do not share it.", text)
        self.assertEqual(self.start_email(re.search(r"\d{6}", text).group()).status_code, 204)

    def test_mobile_channel_for_a_mobile_change(self):
        self.step_up("mobile", "mobile_change")
        code = self.texted_code()
        before = SmsMessage.objects.count()
        response = self.start_mobile(code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SmsMessage.objects.count(), before + 1)
        self.assertEqual(SmsMessage.objects.latest("pk").recipient, NEW_MOBILE)

    def test_email_channel_also_works_for_a_mobile_change(self):
        self.step_up("email", "mobile_change")
        self.assertEqual(self.start_mobile(self.emailed_code()).status_code, 200)

    def test_a_mobile_change_without_a_code_is_refused_and_sends_nothing(self):
        before = SmsMessage.objects.count()
        response = self.post("/v1/account/mobile/start/", {"mobile": NEW_MOBILE})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "step_up_required")
        self.assertEqual(SmsMessage.objects.count(), before)

    def test_a_code_works_once_the_change_is_done(self):
        self.step_up("email", "mobile_change")
        code = self.emailed_code()
        self.start_mobile(code)
        challenge_code = self.texted_code()
        self.assertEqual(
            self.post("/v1/account/mobile/verify/", {"code": challenge_code}).status_code, 200
        )
        cache.clear()
        self.assertEqual(self.start_mobile(code, "+639221234567").status_code, 400)

    def test_a_code_for_another_purpose_is_refused(self):
        self.step_up("email", "email_change")
        self.assertEqual(self.start_mobile(self.emailed_code()).status_code, 400)
        self.step_up("mobile", "mobile_change")
        self.assertEqual(self.start_email(self.texted_code()).status_code, 400)

    def test_a_code_is_refused_after_ten_minutes(self):
        self.step_up("mobile", "email_change")
        code = self.texted_code()
        with mock.patch("apps.accounts.codes.cache") as fake:
            fake.get.return_value = None
            self.assertEqual(self.start_email(code).status_code, 400)

    def test_one_throttle_covers_both_channels(self):
        limit = services.EMAIL_CHANGE_STARTS_PER_HOUR
        statuses = []
        for n in range(limit + 1):
            channel = "email" if n % 2 == 0 else "mobile"
            purpose = "email_change" if n % 4 < 2 else "mobile_change"
            statuses.append(self.post(STEP_UP, {"channel": channel, "purpose": purpose}).status_code)
            self._clear_cooldowns()  # the 60 s waits between pairs are not under test
        self.assertEqual(statuses[:limit], [204] * limit)
        self.assertEqual(statuses[-1], 429)

    def _clear_cooldowns(self):
        from apps.accounts import codes

        for purpose in services.STEP_UP_PURPOSES:
            for channel in services.STEP_UP_CHANNELS:
                value, scope = services._step_code_scope(self.account, purpose, channel)
                cache.delete(codes._key("cooldown", scope, value))
                cache.delete(codes._key("email-hour", scope, value))

    def test_audit_events_hold_channel_and_purpose_but_no_pii(self):
        self.step_up("mobile", "email_change")
        mobile_code = self.texted_code()
        self.step_up("email", "mobile_change")
        email_code = self.emailed_code()
        events = list(AccountAuditEvent.objects.filter(account=self.account, event__endswith="_step_up"))
        self.assertEqual(
            sorted((e.detail["channel"], e.detail["purpose"]) for e in events),
            [("email", "mobile_change"), ("mobile", "email_change")],
        )
        blob = json.dumps([e.detail for e in events])
        for secret in (EMAIL, MOBILE, MOBILE[-4:], mobile_code, email_code):
            self.assertNotIn(secret, blob)


@override_settings(ACCOUNT_EMAIL_CHANGE_HARDENED=True)
class MobileChannelOffTests(StepUpCase):
    def test_no_mobile_channel_while_texts_are_off_even_with_a_mobile(self):
        self.hold_mobile()
        self.assertEqual(self.get("/v1/account/").json()["step_up_channels"], ["email"])
        self.step_up("mobile", "email_change", expect=400)


@override_settings(ACCOUNT_EMAIL_CHANGE_HARDENED=True)
class VerifyTests(StepUpCase):
    def verify(self, code, purpose="email_change", expect=204):
        response = self.post(VERIFY, {"purpose": purpose, "code": code})
        self.assertEqual(response.status_code, expect, response.content)
        return response

    def wrong(self, code):
        return "000000" if code != "000000" else "111111"

    def test_a_right_code_is_accepted_and_still_usable_at_start(self):
        self.step_up("email", "email_change")
        code = self.emailed_code()
        self.verify(code)
        self.verify(code)  # not consumed
        self.assertEqual(self.start_email(code).status_code, 204)

    def test_a_right_mobile_change_code_is_still_usable_at_start(self):
        self.step_up("email", "mobile_change")
        code = self.emailed_code()
        self.verify(code, "mobile_change")
        with gateway_on:
            online_phone()
            self.assertEqual(self.start_mobile(code).status_code, 200)

    def test_a_wrong_code_gets_one_neutral_error(self):
        self.step_up("email", "email_change")
        response = self.verify(self.wrong(self.emailed_code()), expect=400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "step_up_code_invalid")
        self.assertEqual(error["message"], WRONG_CODE)

    def test_no_code_issued_gets_the_same_error(self):
        response = self.verify("123456", expect=400)
        self.assertEqual(response.json()["error"]["message"], WRONG_CODE)

    def test_a_code_for_another_purpose_is_refused(self):
        self.step_up("email", "email_change")
        self.verify(self.emailed_code(), "mobile_change", expect=400)

    @gateway_on
    def test_a_code_from_the_mobile_channel_is_accepted(self):
        self.hold_mobile()
        online_phone()
        self.step_up("mobile", "email_change")
        self.verify(self.texted_code())

    def test_the_attempt_cap_locks_the_code(self):
        self.step_up("email", "email_change")
        code = self.emailed_code()
        bad = self.wrong(code)
        for _ in range(5):
            self.verify(bad, expect=400)
        self.verify(code, expect=400)  # locked: even the right code is dead now
        self.assertEqual(self.start_email(code).status_code, 400)

    def test_signed_out_is_refused(self):
        self.client.cookies.clear()
        self.assertEqual(
            self.post(VERIFY, {"purpose": "email_change", "code": "123456"}).status_code, 401
        )

    def test_an_invalid_purpose_is_refused(self):
        response = self.verify("123456", "password_change", expect=400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_the_cap_is_shared_with_the_start_step(self):
        self.step_up("email", "email_change")
        code = self.emailed_code()
        bad = self.wrong(code)
        for _ in range(4):
            self.verify(bad, expect=400)
        self.assertEqual(self.start_email(bad).status_code, 400)  # the fifth try
        self.assertEqual(self.start_email(code).status_code, 400)  # locked
        self.verify(code, expect=400)
