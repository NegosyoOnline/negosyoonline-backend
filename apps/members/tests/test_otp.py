"""The code engine: limits, single use, burning, series reuse, decoys, binding."""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.utils import timezone

from apps.common.errors import ApiError
from apps.members import messaging, otp
from apps.members.models import MemberOtpChallenge
from apps.members.tests.base import BINDING, EMAIL, OTHER_BINDING, MembersTestCase


class EngineTestCase(MembersTestCase):
    def open(self, *, target=EMAIL, binding=BINDING, member="member", site=None, **extra):
        kwargs = {"purpose": "signin", "channel": "email", **extra}
        kwargs.update(target=target, binding_raw=binding, member=self.member if member == "member" else member)
        return otp.open_challenge(site or self.site, **kwargs)

    def verify(self, code, binding=BINDING, **extra):
        return otp.verify_challenge(self.site, purpose="signin", binding_raw=binding, code=code, **extra)

    def age(self, seconds):
        for row in MemberOtpChallenge.objects.all():
            MemberOtpChallenge.objects.filter(pk=row.pk).update(created_at=row.created_at - timedelta(seconds=seconds))


class StorageTests(EngineTestCase):
    def test_a_code_is_six_digits_and_only_its_hash_is_stored(self):
        ch, code = self.open()
        self.assertRegex(code, r"^\d{6}$")
        self.assertEqual(len(ch.code_hash), 64)
        self.assertNotIn(code, ch.code_hash)
        self.assertEqual(ch.expires_at - ch.created_at, otp.CODE_TTL)
        self.assertEqual(ch.binding_hash, otp.binding_hash(BINDING))
        self.assertNotIn(BINDING, ch.binding_hash)

    def test_a_decoy_has_no_member_no_code_and_a_hash_that_never_matches(self):
        ch, code = self.open(decoy=True, member=None)
        self.assertIsNone(code)
        self.assertIsNone(ch.member)
        self.assertEqual(len(ch.code_hash), 64)
        self.assertTrue(ch.code_hash.startswith(otp.DECOY_PREFIX))
        self.assertEqual(ch.delivery, "held")
        for guess in ("000000", "123456", ch.code_hash[:6]):
            with self.assertRaises(ApiError):
                self.verify(guess)


class VerifyTests(EngineTestCase):
    def test_the_right_code_is_used_once(self):
        ch, code = self.open()
        used = self.verify(code)
        self.assertEqual(used.pk, ch.pk)
        self.assertIsNotNone(used.used_at)
        with self.assertRaises(ApiError) as caught:
            self.verify(code)
        self.assertEqual(caught.exception.code, "code_expired")

    def test_a_wrong_guess_is_counted_and_five_burn_the_code(self):
        ch, code = self.open()
        wrong = "000000" if code != "000000" else "111111"
        for count in range(1, otp.MAX_GUESSES + 1):
            with self.assertRaises(ApiError) as caught:
                self.verify(wrong)
            ch.refresh_from_db()
            self.assertEqual(ch.attempts, count)
        self.assertEqual(caught.exception.code, "code_burned")
        with self.assertRaises(ApiError) as caught:
            self.verify(code)
        self.assertEqual(caught.exception.code, "code_burned")
        ch.refresh_from_db()
        self.assertIsNone(ch.used_at)

    def test_an_expired_code_is_expired(self):
        ch, code = self.open()
        with self.assertRaises(ApiError) as caught:
            self.verify(code, now=ch.expires_at + timedelta(seconds=1))
        self.assertEqual(caught.exception.code, "code_expired")

    def test_a_code_belongs_to_one_binding_one_site_and_one_purpose(self):
        _, code = self.open()
        with self.assertRaises(ApiError):
            self.verify(code, binding=OTHER_BINDING)
        with self.assertRaises(ApiError):
            otp.verify_challenge(self.other_site, purpose="signin", binding_raw=BINDING, code=code)
        with self.assertRaises(ApiError):
            otp.verify_challenge(self.site, purpose="email_verify", binding_raw=BINDING, code=code)
        self.verify(code)

    def test_a_new_challenge_supersedes_the_open_ones_of_that_binding_only(self):
        first, _ = self.open(resend_wait=False)
        other, _ = self.open(binding=OTHER_BINDING, resend_wait=False)
        second, code = self.open(resend_wait=False)
        first.refresh_from_db(), other.refresh_from_db()
        self.assertIsNotNone(first.superseded_at)
        self.assertIsNone(other.superseded_at)
        self.assertEqual(self.verify(code).pk, second.pk)

    def test_the_code_is_compared_after_trimming(self):
        _, code = self.open()
        self.verify(f"  {code} ")


class LimitTests(EngineTestCase):
    def test_a_second_send_inside_a_minute_is_refused(self):
        self.open()
        with self.assertRaises(ApiError) as caught:
            self.open()
        self.assertEqual((caught.exception.code, caught.exception.status), ("rate_limited", 429))

    def test_the_fourth_in_ten_minutes_and_the_eleventh_in_a_day_are_refused(self):
        for _ in range(otp.SEND_LIMIT):
            self.open()
            self.age(61)
        with self.assertRaises(ApiError):
            self.open()
        MemberOtpChallenge.objects.all().delete()
        for _ in range(otp.DAY_LIMIT):
            self.open(decoy=True, member=None)
            self.age(11 * 60)
        with self.assertRaises(ApiError):
            self.open()

    def test_decoys_count_toward_the_limits(self):
        for _ in range(otp.SEND_LIMIT):
            self.open(decoy=True, member=None)
            self.age(61)
        with self.assertRaises(ApiError):
            self.open(decoy=True, member=None)

    def test_a_full_delivery_target_is_written_as_a_silent_decoy(self):
        """Real sends to one target are capped apart from what each visitor typed."""
        for number in range(otp.SEND_LIMIT):
            self.open(requested=f"typed{number}@buyer.test")
            self.age(61)
        ch, code = self.open(requested="typed9@buyer.test")
        self.assertIsNone(code)
        self.assertIsNone(ch.member)
        self.assertTrue(ch.code_hash.startswith(otp.DECOY_PREFIX))

    def test_used_codes_do_not_fill_the_silent_target_bucket(self):
        for number in range(otp.SEND_LIMIT):
            ch, code = self.open(requested=f"typed{number}@buyer.test")
            self.verify(code)
            self.age(61)
        ch, code = self.open(requested="typed9@buyer.test")
        self.assertIsNotNone(code)
        self.assertEqual(ch.member, self.member)

    def test_unused_real_codes_still_fill_the_silent_target_bucket(self):
        for number in range(otp.SEND_LIMIT - 1):
            ch, code = self.open(requested=f"typed{number}@buyer.test")
            self.verify(code)
            self.age(61)
        for number in range(2):  # one used code never counts, so two unused ones are not yet a full bucket
            self.open(requested=f"unused{number}@buyer.test")
            self.age(61)
        ch, code = self.open(requested="typed9@buyer.test")
        self.assertIsNotNone(code)
        self.open(requested="typed8@buyer.test")
        self.age(61)
        ch, code = self.open(requested="typed7@buyer.test")
        self.assertIsNone(code)

    def test_the_cap_logs_a_warning_without_the_address(self):
        for number in range(otp.SEND_LIMIT):
            self.open(requested=f"typed{number}@buyer.test")
            self.age(61)
        with self.assertLogs("apps.members.otp", level="WARNING") as logs:
            self.open(requested="typed9@buyer.test")
        text = "\n".join(logs.output)
        self.assertIn(f"site {self.site.pk}", text)
        self.assertIn("purpose signin", text)
        self.assertIn("channel email", text)
        self.assertIn(otp.hashlib.sha256(EMAIL.encode()).hexdigest()[:10], text)
        self.assertNotIn(EMAIL, text)
        self.assertNotIn("buyer.test", text)

    def test_the_visible_contact_limit_still_counts_used_codes(self):
        for _ in range(otp.SEND_LIMIT):
            ch, code = self.open()
            self.verify(code)
            self.age(61)
        with self.assertRaises(ApiError) as caught:
            self.open()
        self.assertEqual(caught.exception.code, "rate_limited")

    def test_the_limits_are_per_site(self):
        self.open()
        with self.assertRaises(ApiError):
            self.open()
        self.open(site=self.other_site, member=None, decoy=True)

    def test_check_send_allowed_reads_only_and_answers_the_same_for_every_input(self):
        self.open()
        for contact in (EMAIL, "someone@buyer.test"):
            if contact == EMAIL:
                with self.assertRaises(ApiError):
                    otp.check_send_allowed(self.site, purpose="signin", requested=contact, binding_raw=BINDING)
            else:
                otp.check_send_allowed(self.site, purpose="signin", requested=contact, binding_raw=BINDING)
        self.assertEqual(MemberOtpChallenge.objects.count(), 1)

    def test_the_visitor_address_limits_send_and_verify(self):
        for _ in range(otp.IP_SEND_LIMIT[1]):
            otp.throttle_signed_out(self.site, "203.0.113.9")
        with self.assertRaises(ApiError):
            otp.throttle_signed_out(self.site, "203.0.113.9")
        for _ in range(otp.IP_VERIFY_LIMIT[1]):
            otp.throttle_verify("203.0.113.9")
        with self.assertRaises(ApiError):
            otp.throttle_verify("203.0.113.9")
        otp.throttle_verify("")  # no address, no limit

    def test_a_signed_in_member_has_ten_sends_an_hour(self):
        for _ in range(otp.MEMBER_LIMIT[1]):
            otp.throttle_signed_in(self.member, "")
        with self.assertRaises(ApiError):
            otp.throttle_signed_in(self.member, "")

    def test_a_lock_timeout_answers_as_a_limit_never_a_server_error(self):
        from django.db import DatabaseError

        with mock.patch.object(otp, "_lock_contact", side_effect=DatabaseError("lock timeout")):
            with self.assertRaises(ApiError) as caught:
                self.open()
        self.assertEqual(caught.exception.status, 429)


class SeriesTests(EngineTestCase):
    def test_a_resend_inside_the_ten_minutes_keeps_the_same_code(self):
        _, first = self.open(reuse_code=True)
        self.age(61)
        second_row, second = self.open(reuse_code=True)
        self.assertEqual(first, second)
        self.assertEqual(self.verify(second).pk, second_row.pk)

    def test_the_guesses_carry_over_a_resend(self):
        _, code = self.open(reuse_code=True)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(3):
            with self.assertRaises(ApiError):
                self.verify(wrong)
        self.age(61)
        row, _ = self.open(reuse_code=True)
        self.assertEqual(row.attempts, 3)

    def test_with_under_two_minutes_left_a_resend_gets_a_new_code(self):
        first_row, first = self.open(reuse_code=True)
        MemberOtpChallenge.objects.filter(pk=first_row.pk).update(expires_at=timezone.now() + timedelta(minutes=1))
        self.age(61)
        new_row, new = self.open(reuse_code=True)
        self.assertGreater(new_row.expires_at, timezone.now() + timedelta(minutes=5))

    def test_a_burned_series_is_not_reused(self):
        row, code = self.open(reuse_code=True)
        MemberOtpChallenge.objects.filter(pk=row.pk).update(attempts=otp.MAX_GUESSES)
        self.age(61)
        new_row, _ = self.open(reuse_code=True)
        self.assertEqual(new_row.attempts, 0)

    def test_a_series_code_differs_between_bindings_and_sites(self):
        _, a = self.open(reuse_code=True)
        _, b = self.open(reuse_code=True, binding=OTHER_BINDING)
        self.assertEqual(len(a), 6)
        self.assertEqual(len(b), 6)

    def test_latest_open_and_latest_recent(self):
        row, _ = self.open()
        self.assertEqual(otp.latest_open(self.site, purpose="signin", binding_raw=BINDING).pk, row.pk)
        self.assertIsNone(otp.latest_open(self.site, purpose="signin", binding_raw=OTHER_BINDING))
        self.assertEqual(otp.latest_recent(self.site, purpose="signin", binding_raw=BINDING, channel="email").pk, row.pk)
        self.assertIsNone(otp.latest_recent(self.site, purpose="signin", binding_raw=BINDING, channel="sms"))


class DeliveryTests(EngineTestCase):
    def test_mark_held_supersedes_and_a_final_sms_failure_does_the_same(self):
        row, _ = self.open(channel="sms", target="+639171234567")
        otp.mark_held(row.pk)
        row.refresh_from_db()
        self.assertEqual(row.delivery, "held")
        self.assertIsNotNone(row.superseded_at)
        other, _ = self.open(channel="sms", target="+639171234567", binding=OTHER_BINDING)
        MemberOtpChallenge.objects.filter(pk=other.pk).update(sms_message_id=77)
        otp.on_sms_delivery(77, "sent")
        other.refresh_from_db()
        self.assertEqual(other.delivery, "sent")
        otp.on_sms_delivery(77, "failed")
        other.refresh_from_db()
        self.assertEqual((other.delivery, other.superseded_at is not None), ("failed", True))

    def test_start_challenge_sends_the_email_and_reports_the_outcome(self):
        from django.core import mail

        row, outcome = otp.start_challenge(
            self.site, purpose="onboarding", channel="email", target=EMAIL, binding_raw=BINDING
        )
        self.assertEqual(outcome, "emailed")
        self.assertEqual(row.purpose, "onboarding")
        self.assertIn("Confirm your email for Ana's Store", mail.outbox[0].subject)
        self.assertNotIn(row.code_hash, mail.outbox[0].body)

    def test_each_purpose_has_its_own_email_copy_naming_the_store(self):
        for purpose in messaging.EMAIL_TEXTS:
            subject, body = messaging.otp_email(self.site, "123456", purpose)
            self.assertIn("123456", body)
            self.assertNotIn("123456", subject)
        for purpose in messaging.SMS_BODIES:
            self.assertIn("Ana's Store", messaging.otp_sms_body(self.site, "123456", purpose))
