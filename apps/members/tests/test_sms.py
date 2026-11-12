"""The member code by SMS: the transport behind the hook, through the real send function."""
import logging
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings

from apps.members import hooks, messaging, otp, retention as member_retention, sms as member_sms
from apps.members.models import Member, MemberOtpChallenge
from apps.members.tests.base import BINDING, EMAIL, MOBILE, MembersTestCase
from apps.notifications import sms
from apps.notifications.models import SmsMessage
from apps.notifications.tests.base import gateway_on, online_phone, set_policy

NUMBER = "+639171234567"


@gateway_on
class MemberSmsTestCase(MembersTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        hooks._sms_transport.clear()
        hooks._sign_in_sms_first.clear()
        member_sms.register()
        self.addCleanup(hooks._sms_transport.clear)
        self.addCleanup(hooks._sign_in_sms_first.clear)
        later = mock.patch("apps.operators.background.run_later")
        later.start()
        self.addCleanup(later.stop)
        online_phone()
        Member.objects.filter(pk=self.member.pk).update(mobile_state="verified")


class DeliveryTests(MemberSmsTestCase):
    def test_a_signin_code_is_one_queued_text_with_the_store_name_and_the_code(self):
        response = self.request_code(MOBILE)
        self.assertEqual(response.status_code, 200)
        message = SmsMessage.objects.get()
        self.assertEqual((message.purpose, message.category, message.priority, message.recipient),
                         ("member_signin_code", "code", sms.Priority.CODE, NUMBER))
        self.assertEqual(message.status, "queued")
        self.assertEqual(message.site_id, self.site.pk)
        ch = MemberOtpChallenge.objects.get()
        self.assertEqual((ch.channel, ch.sms_message_id), ("sms", message.pk))
        self.assertEqual(mail.outbox, [])

    def test_the_body_is_the_twcinc_wording_byte_for_byte(self):
        body = messaging.otp_sms_body(self.site, "123456", "signin")
        name = self.site.display_name
        self.assertEqual(body, f"{name}: your sign-in code is 123456. It expires in 10 minutes. Never share this code with anyone.")
        self.assertEqual(
            messaging.otp_sms_body(self.site, "123456", "onboarding"),
            f"{name}: your mobile confirmation code is 123456. It expires in 10 minutes. Do not share it.")

    def test_an_onboarding_code_uses_its_own_purpose(self):
        member = self.member
        ch, outcome = otp.start_challenge(self.site, purpose="onboarding", channel="sms", target=member.mobile,
                                          binding_raw=BINDING, member=member)
        self.assertEqual(outcome, "queued")
        self.assertEqual(SmsMessage.objects.get().purpose, "member_mobile_verify")

    def test_a_purpose_with_no_declared_text_is_held_and_never_sent(self):
        ch, outcome = otp.start_challenge(self.site, purpose="cod_confirmation", channel="sms", target=self.member.mobile,
                                          binding_raw=BINDING, member=self.member)
        self.assertEqual(outcome, "sms_unavailable")
        self.assertEqual(SmsMessage.objects.count(), 0)
        ch.refresh_from_db()
        self.assertIsNotNone(ch.superseded_at)

    def test_the_text_handed_to_the_gateway_carries_the_code_and_it_is_in_no_row_or_log(self):
        sent = []
        real = member_sms.send_sms

        def spy(**kwargs):
            sent.append(kwargs)
            return real(**kwargs)

        with mock.patch.object(member_sms, "send_sms", spy), self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("test").debug("start")
            self.request_code(MOBILE)
        (call,) = sent
        self.assertIsNone(call["fallback"])
        code = call["text"].split("code is ")[1][:6]
        self.assertRegex(code, r"^\d{6}$")
        self.assertNotIn(code, str(logs.output))
        self.assertNotIn(MOBILE, str(logs.output))
        row = SmsMessage.objects.get()
        self.assertNotIn(code, "".join(str(v) for v in SmsMessage.objects.values().get().values()))
        self.assertNotRegex(MemberOtpChallenge.objects.get().code_hash, r"^\d{6}$")
        self.assertEqual(row.reason, "")

    def test_a_decoy_sends_nothing_and_writes_no_send_record(self):
        response = self.request_code("09175550999")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertEqual(MemberOtpChallenge.objects.get().delivery, "held")

    def test_a_resend_keeps_the_code_and_is_a_new_text(self):
        self.request_code(MOBILE)
        self.age_challenges()
        self.request_code(MOBILE)
        self.assertEqual(SmsMessage.objects.count(), 2)
        first, second = MemberOtpChallenge.objects.order_by("pk")
        self.assertEqual(first.code_hash, second.code_hash)


class RefusalTests(MemberSmsTestCase):
    def test_gateway_off_answers_503_sms_unavailable_for_every_input(self):
        with override_settings(SMS_GATEWAY_ENABLED=False):
            for contact in (MOBILE, "09175550999"):
                response = self.request_code(contact)
                self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "sms_unavailable"))
        self.assertEqual((SmsMessage.objects.count(), MemberOtpChallenge.objects.count()), (0, 0))

    def test_paused_answers_sms_paused_for_every_input_before_any_lookup(self):
        set_policy(sending_stopped=True)
        for contact in (MOBILE, "09175550999"):
            self.assertEqual(self.request_code(contact).json()["error"]["code"], "sms_paused")
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_the_platform_limit_answers_sms_paused(self):
        set_policy(platform_daily_limit=0)
        self.assertEqual(self.request_code(MOBILE).json()["error"]["code"], "sms_paused")

    def test_a_race_past_the_limit_check_still_pauses_and_supersedes_the_code(self):
        ch, code = otp.open_challenge(self.site, purpose="onboarding", channel="sms", target=self.member.mobile,
                                      binding_raw=BINDING, member=self.member)
        set_policy(platform_daily_limit=0)
        self.assertEqual(member_sms.deliver_code(ch, code, "otp_no_fallback"), "paused")
        ch.refresh_from_db()
        self.assertEqual(ch.delivery, "held")
        self.assertIsNotNone(ch.superseded_at)
        with self.assertRaises(Exception):
            otp.verify_challenge(self.site, purpose="onboarding", binding_raw=BINDING, code=code)

    def test_an_offline_phone_offers_the_email_only_for_signin_with_a_verified_email(self):
        SmsMessage.objects.all().delete()
        from apps.notifications.models import SmsDevice
        SmsDevice.objects.update(last_contact_at=None)
        sign, scode = otp.open_challenge(self.site, purpose="signin", channel="sms", target=self.member.mobile,
                                         binding_raw=BINDING, member=self.member)
        self.assertEqual(member_sms.deliver_code(sign, scode, "otp_no_fallback"), "sms_unavailable")  # email unverified
        Member.objects.filter(pk=self.member.pk).update(email_state="verified")
        sign, scode = otp.open_challenge(self.site, purpose="signin", channel="sms", target=self.member.mobile,
                                         binding_raw="another-binding-0123456789abcdef",
                                         member=Member.objects.get(pk=self.member.pk))
        self.assertEqual(member_sms.deliver_code(sign, scode, "otp_email_fallback"), "offer_email")
        onb, ocode = otp.open_challenge(self.site, purpose="onboarding", channel="sms", target=self.member.mobile,
                                        binding_raw="third-binding-0123456789abcdefgh",
                                        member=Member.objects.get(pk=self.member.pk))
        self.assertEqual(member_sms.deliver_code(onb, ocode, "otp_no_fallback"), "sms_unavailable")
        self.assertEqual(mail.outbox, [])  # no automatic email: only an offer
        for row in (sign, onb):
            row.refresh_from_db()
            self.assertIsNotNone(row.superseded_at)

    def test_sms_unavailable_is_a_503_for_an_onboarding_caller(self):
        error = messaging.outcome_error("sms_unavailable")
        self.assertEqual((error.status, error.code), (503, "sms_unavailable"))


class StoreNameTests(MembersTestCase):
    def body(self, name):
        self.site.display_name = name
        return messaging.otp_sms_body(self.site, "123456", "signin")

    def test_a_plain_name_stays(self):
        self.assertTrue(self.body("Santé Store").startswith("Santé Store: "))

    def test_characters_outside_gsm7_are_dropped(self):
        body = self.body("Maria ’s \U0001F600 Shop")
        self.assertEqual(sms.text_problem(body), "")
        self.assertNotIn("\U0001F600", body)

    def test_a_domain_like_name_becomes_the_neutral_one(self):
        for name in ("mystore.com", "www.mystore", "shop at mystore.ph/x"):
            with self.subTest(name=name):
                self.assertTrue(self.body(name).startswith("Your store: your sign-in code"))

    def test_a_name_that_pushes_the_text_past_160_becomes_the_neutral_one(self):
        body = self.body("A" * 120)
        self.assertTrue(body.startswith("Your store: "))
        self.assertEqual(sms.text_problem(body), "")

    def test_a_name_with_no_usable_character_becomes_the_neutral_one(self):
        self.assertTrue(self.body("\U0001F600\U0001F600").startswith("Your store: "))


class DisposalAndStateTests(MemberSmsTestCase):
    def test_a_disposed_members_number_is_never_texted(self):
        placeholder = member_retention._placeholder_mobile(self.member)
        Member.objects.filter(pk=self.member.pk).update(mobile=placeholder)
        member = Member.objects.get(pk=self.member.pk)
        ch, code = otp.open_challenge(self.site, purpose="signin", channel="sms", target=placeholder,
                                      binding_raw=BINDING, member=member)
        self.assertEqual(member_sms.deliver_code(ch, code, "otp_no_fallback"), "held")
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_a_failed_or_expired_text_marks_the_row_failed_and_supersedes_it(self):
        self.request_code(MOBILE)
        message = SmsMessage.objects.get()
        ch = MemberOtpChallenge.objects.get()
        self.assertEqual(member_sms.refresh_delivery(ch), "sending")
        SmsMessage.objects.filter(pk=message.pk).update(status="failed")
        self.assertEqual(member_sms.refresh_delivery(ch), "failed")
        ch.refresh_from_db()
        self.assertEqual(ch.delivery, "failed")
        self.assertIsNotNone(ch.superseded_at)

    def test_a_sent_text_marks_the_row_sent(self):
        self.request_code(MOBILE)
        SmsMessage.objects.update(status="sent")
        ch = MemberOtpChallenge.objects.get()
        self.assertEqual(member_sms.refresh_delivery(ch), "sent")


class CodeHashTests(MembersTestCase):
    def test_the_hash_is_keyed_with_the_secret_key(self):
        ch, code = otp.open_challenge(self.site, purpose="signin", channel="email", target=EMAIL,
                                      binding_raw=BINDING, member=self.member)
        import hashlib
        bare = hashlib.sha256(f"{self.site.pk}:signin:{ch.binding_hash}:{EMAIL}:{code}".encode()).hexdigest()
        self.assertNotEqual(ch.code_hash, bare)
        with override_settings(SECRET_KEY="a-different-secret-key-for-this-test-0123456789"):
            self.assertNotEqual(otp._code_hash(self.site.pk, "signin", ch.binding_hash, EMAIL, code), ch.code_hash)
        self.assertEqual(otp._code_hash(self.site.pk, "signin", ch.binding_hash, EMAIL, code), ch.code_hash)


class EndToEndTests(MemberSmsTestCase):
    def test_a_member_signs_in_with_the_code_texted_to_the_mobile(self):
        sent = []
        real = member_sms.send_sms

        def spy(**kwargs):
            sent.append(kwargs["text"])
            return real(**kwargs)

        with mock.patch.object(member_sms, "send_sms", spy):
            self.assertEqual(self.request_code(MOBILE).status_code, 200)
        code = sent[0].split("code is ")[1][:6]
        wrong = self.post("signin/verify/", code="000000" if code != "000000" else "111111")
        self.assertEqual(wrong.json()["error"]["code"], "wrong_code")
        right = self.post("signin/verify/", code=code)
        self.assertEqual(right.status_code, 200, right.content)
        self.assertIn("token", right.json())
