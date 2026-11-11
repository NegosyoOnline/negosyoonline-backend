"""T-SEND: the one send function, its limits, its switches and what it keeps."""
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.core import checks, mail
from django.core.cache import cache
from django.db import models
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.notifications import sms
from apps.notifications.models import EmailMessage, SmsDevice, SmsMessage, SmsPolicy
from apps.notifications.services import send_sms
from apps.notifications.tests.base import (
    CODE_TEXT, MOBILE, STATUS_TEXT, SmsTestCase, fallback, gateway_on, online_phone, set_policy,
)
from apps.sites.tests.base import make_account, make_site


def send_code(**fields):
    fields = {"to": MOBILE, "purpose": "mobile_verify", "text": CODE_TEXT, **fields}
    return send_sms(**fields)


def send_status(site, **fields):
    fields = {
        "to": MOBILE, "purpose": "order_status", "text": STATUS_TEXT, "site": site,
        "fallback": fallback(), **fields,
    }
    return send_sms(**fields)


class SendSmsRefusesMistakesTests(SmsTestCase):
    def setUp(self):
        super().setUp()
        online_phone()
        self.site = make_site(make_account())

    def test_1_an_undeclared_purpose_is_refused(self):
        with self.assertRaises(ValueError):
            send_code(purpose="promo_blast")
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_2_a_status_text_needs_a_site_and_an_email_fallback(self):
        with self.assertRaises(ValueError):
            send_status(None)
        with self.assertRaises(ValueError):
            send_status(self.site, fallback=None)
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_3_a_long_text_or_a_link_is_refused(self):
        bad = (
            "x" * 161,
            "Track it at https://example.test/t/1",
            "Track it at www.example.test",
            "Track it at example.com/t/1",
            "Track it at http://example.test/t/1",
            "Track it at HTTPS://Example.test/t/1",
            "Track it at Http://example.test",
            "Track it at WWW.Example.test",
            "Track it at example.com",
            "Track it at EXAMPLE.COM",
            "Track it at Example.Com/t/1",
            "Track it at shop.example.ph",
            "Track it at bit.ly/abc",
            "Track it at (example.com)",
            "Track it at\nexample.com",
            # A domain next to "_" or next to an accented or Greek SMS letter.
            "see_example.com",
            "_example.com/x",
            "Track it at ñexample.com",
            "Track it at éexample.com",
            "Track it at Ωexample.com",
            "Track it at example.com_x",
            "Track it at example.comñ",
            "Track it at _www.example",
            "Track it at ñWWW.example",
            # A bare IP address.
            "Track it at 192.168.1.10/x",
            "Track it at 10.0.0.1",
            "Track it at_10.0.0.1",
            "Track it at ñ10.0.0.1:8000",
            "Salamat ❤",  # not a plain SMS character: it would no longer fit one SMS
        )
        for text in bad:
            with self.subTest(text=text[:30]):
                with self.assertRaises(ValueError):
                    send_code(text=text)
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertEqual(send_code(text="x" * 160).outcome, sms.QUEUED)

    def test_a_text_with_full_stops_and_numbers_but_no_link_is_sent(self):
        good = (
            CODE_TEXT,
            STATUS_TEXT,
            "Salamat po. Ingat!",
            "Your total is P1,250.00. Thank you.",
            "Order 12.5 kg, 3 items. Ref_no 1.2.3",
            "Señor Peña, your code is 123456.",
        )
        for text in good:
            with self.subTest(text=text[:30]):
                self.assertEqual(sms.text_problem(text), "")

    def test_a_number_that_is_not_a_philippine_mobile_is_refused(self):
        with self.assertRaises(ValueError):
            send_code(to="+14155550100")

    def test_the_number_is_stored_in_one_form(self):
        result = send_code(to="0917 123 4567")
        self.assertEqual(result.message.recipient, MOBILE)


class GatewayOffTests(TestCase):
    """T-SEND 4. With the switch off nothing reads or writes the SMS tables."""

    def setUp(self):
        cache.clear()

    def test_4_gateway_off_refuses_and_queues_nothing(self):
        with self.assertLogs("apps.notifications", level="ERROR"):
            with self.assertNumQueries(0):
                result = send_code()

        self.assertEqual((result.outcome, result.reason), (sms.REFUSED, "gateway_off"))
        self.assertIsNone(result.message)
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_4_gateway_off_emails_the_fallback_and_writes_no_sms_row(self):
        site = make_site(make_account())
        declared = {"order_status": sms.Purpose(category=sms.STATUS, priority=sms.Priority.STATUS)}
        with mock.patch.dict(sms.PURPOSES, declared):
            result = send_status(site)

        self.assertEqual((result.outcome, result.reason), (sms.EMAILED, "gateway_off"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertEqual(EmailMessage.objects.get().category, "order_status")

    def test_4_the_switch_on_writes_the_record_and_queues(self):
        with gateway_on:
            online_phone()
            result = send_code()
        self.assertEqual(result.outcome, sms.QUEUED)
        self.assertEqual(SmsMessage.objects.get().status, SmsMessage.Status.QUEUED)

    @override_settings(SMS_GATEWAY_ENABLED=True, REDIS_URL=None, DEBUG=False)
    def test_the_gateway_does_not_run_on_a_deployment_without_redis(self):
        self.assertFalse(sms.gateway_on())
        errors = [e for e in checks.run_checks() if e.id == "notifications.E001"]
        self.assertEqual(len(errors), 1)
        with self.assertLogs("apps.notifications", level="ERROR"):
            self.assertEqual(send_code().reason, "gateway_off")

    @override_settings(SMS_GATEWAY_ENABLED=True, REDIS_URL=None, DEBUG=True)
    def test_local_development_needs_no_redis(self):
        self.assertTrue(sms.gateway_on())
        self.assertEqual([e for e in checks.run_checks() if e.id == "notifications.E001"], [])

    def test_the_check_is_quiet_while_the_gateway_is_off(self):
        self.assertEqual([e for e in checks.run_checks() if e.id == "notifications.E001"], [])


class SendSmsControlsTests(SmsTestCase):
    def setUp(self):
        super().setUp()
        self.phone = online_phone()
        self.site = make_site(make_account())

    def assert_refused(self, result, reason):
        self.assertEqual((result.outcome, result.reason), (sms.REFUSED, reason))
        message = result.message
        message.refresh_from_db()
        self.assertEqual(message.status, SmsMessage.Status.REFUSED)
        self.assertEqual(message.reason, reason)
        self.assertFalse(message.emailed)
        self.assertIsNone(sms.take_text(message))

    def assert_emailed(self, result, reason):
        self.assertEqual((result.outcome, result.reason), (sms.EMAILED, reason))
        message = result.message
        message.refresh_from_db()
        self.assertEqual(message.status, SmsMessage.Status.REFUSED)
        self.assertEqual(message.reason, reason)
        self.assertTrue(message.emailed)
        self.assertEqual(mail.outbox[-1].to, ["buyer@example.test"])
        self.assertIsNone(sms.take_text(message))

    def test_a_code_is_queued(self):
        result = send_code()

        self.assertEqual((result.outcome, result.reason), (sms.QUEUED, ""))
        message = SmsMessage.objects.get()
        self.assertEqual(result.message, message)
        self.assertEqual(message.status, SmsMessage.Status.QUEUED)
        self.assertEqual(message.recipient, MOBILE)
        self.assertEqual((message.category, message.purpose), ("code", "mobile_verify"))
        self.assertTrue(message.public_id.startswith("sms_"))
        self.assertIsNone(message.site)
        self.assertIsNone(message.device)
        self.assertAlmostEqual(
            message.expires_at, message.created_at + timedelta(minutes=10),
            delta=timedelta(seconds=5),
        )
        self.assertEqual(mail.outbox, [])

    def test_5_sending_stopped_refuses(self):
        set_policy(sending_stopped=True)
        with self.assertLogs("apps.notifications", level="ERROR"):
            self.assert_refused(send_code(), "paused")
        self.assert_emailed(send_status(self.site), "paused")

    def test_6_platform_limit_refuses_a_code_and_emails_a_status_text(self):
        set_policy(platform_daily_limit=2)
        self.assertEqual(send_code().outcome, sms.QUEUED)
        self.assertEqual(send_status(self.site).outcome, sms.QUEUED)

        with self.assertLogs("apps.notifications", level="ERROR"):
            self.assert_refused(send_code(), "platform_limit")
        self.assert_emailed(send_status(self.site), "platform_limit")

    def test_6_a_refused_text_does_not_use_up_the_limit(self):
        set_policy(platform_daily_limit=1, sending_stopped=True)
        with self.assertLogs("apps.notifications", level="ERROR"):
            send_code()
        set_policy(sending_stopped=False)
        self.assertEqual(send_code().outcome, sms.QUEUED)

    def test_7_site_limit_emails_and_leaves_another_site_alone(self):
        set_policy(site_daily_limit=1)
        other = make_site(make_account("ben@example.test"), name="bens-store")
        self.assertEqual(send_status(self.site).outcome, sms.QUEUED)

        self.assert_emailed(send_status(self.site), "site_limit")
        self.assertEqual(send_status(other).outcome, sms.QUEUED)
        # A code belongs to no site, and a text refused for its site used no platform slot.
        self.assertEqual(send_code().outcome, sms.QUEUED)
        self.assertEqual(sms.platform_count(), 3)

    def test_a_fresh_policy_row_starts_at_2000_and_2000(self):
        self.assertFalse(SmsPolicy.objects.exists())
        policy = sms.policy()
        self.assertEqual((policy.platform_daily_limit, policy.site_daily_limit), (2000, 2000))
        self.assertFalse(policy.sending_stopped)
        self.assertEqual(SmsPolicy.objects.count(), 1)

    def test_8_counts_are_rebuilt_from_the_records_when_the_cache_is_empty(self):
        set_policy(platform_daily_limit=2, site_daily_limit=1)
        send_code()
        send_status(self.site)
        cache.clear()

        with self.assertLogs("apps.notifications", level="ERROR"):
            self.assert_refused(send_code(), "platform_limit")
        cache.clear()
        set_policy(platform_daily_limit=5, site_daily_limit=1)
        self.assert_emailed(send_status(self.site), "site_limit")

    def test_8_counts_follow_the_manila_day(self):
        set_policy(platform_daily_limit=1)
        late = datetime(2026, 11, 11, 15, 59, tzinfo=dt_timezone.utc)  # 23:59 in Manila
        with mock.patch("django.utils.timezone.now", return_value=late):
            SmsDevice.objects.update(last_contact_at=late)
            self.assertEqual(send_code().outcome, sms.QUEUED)
            with self.assertLogs("apps.notifications", level="ERROR"):
                self.assertEqual(send_code().reason, "platform_limit")

        after_midnight = late + timedelta(minutes=2)  # the same UTC day, the next Manila day
        with mock.patch("django.utils.timezone.now", return_value=after_midnight):
            SmsDevice.objects.update(last_contact_at=after_midnight)
            self.assertEqual(send_code().outcome, sms.QUEUED)
            cache.clear()  # the rebuilt count follows the Manila day too
            with self.assertLogs("apps.notifications", level="ERROR"):
                self.assertEqual(send_code().reason, "platform_limit")

    def test_12_phone_offline_refuses_a_code_and_emails_a_status_text(self):
        SmsDevice.objects.update(last_contact_at=timezone.now() - timedelta(seconds=61))

        with self.assertLogs("apps.notifications", level="ERROR") as logs:
            self.assert_refused(send_code(), "gateway_offline")
            self.assert_refused(send_code(), "gateway_offline")
        self.assertEqual(len([line for line in logs.output if "sms_gateway_offline" in line]), 1)
        self.assert_emailed(send_status(self.site), "gateway_offline")
        self.assertEqual(sms.platform_count(), 0)

    def test_12_no_phone_or_a_disabled_phone_is_offline(self):
        SmsDevice.objects.update(is_active=False)
        with self.assertLogs("apps.notifications", level="ERROR"):
            self.assert_refused(send_code(), "gateway_offline")
        SmsDevice.objects.all().delete()
        self.assert_refused(send_code(), "gateway_offline")

    def test_12_a_phone_that_polled_within_a_minute_is_online(self):
        SmsDevice.objects.update(last_contact_at=timezone.now() - timedelta(seconds=55))
        self.assertEqual(send_code().outcome, sms.QUEUED)
        self.assertEqual(sms.pick_device(), self.phone)

    def test_13_a_failing_fallback_email_leaves_the_record_refused_and_logs(self):
        set_policy(sending_stopped=True)
        with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("smtp down")):
            with self.assertLogs("apps.notifications", level="ERROR") as logs:
                result = send_status(self.site)

        self.assert_refused(result, "paused")
        self.assertTrue(any("sms_not_sent" in line for line in logs.output))

    def test_a_site_with_send_records_cannot_be_deleted_from_under_them(self):
        send_status(self.site)
        with self.assertRaises(ProtectedError):
            self.site.delete()
        self.assertEqual(SmsMessage.objects.count(), 1)

    def test_14_each_record_carries_the_sending_class_of_its_purpose(self):
        self.assertEqual(sms.PURPOSES["mobile_verify"].priority, sms.Priority.CODE)
        self.assertEqual(sms.PURPOSES["mobile_verify"].category, sms.CODE)
        code = send_code().message
        status = send_status(self.site).message

        self.assertEqual(code.priority, SmsMessage.Priority.CODE)
        self.assertEqual(status.priority, SmsMessage.Priority.STATUS)
        self.assertEqual(status.site, self.site)
        order = [p.label for p in sorted(SmsMessage.Priority)]
        self.assertEqual(order, ["code", "reply", "status", "marketing"])


class SendSmsKeepsNoTextTests(SmsTestCase):
    def setUp(self):
        super().setUp()
        online_phone()
        self.site = make_site(make_account())

    def test_9_the_record_has_no_text_in_any_field(self):
        message = send_code(text="Your NegosyoOnline code is 804215. Do not share it.").message
        message.refresh_from_db()

        for field in SmsMessage._meta.concrete_fields:
            self.assertNotIsInstance(field, models.TextField, field.name)
            value = str(getattr(message, field.attname))
            self.assertNotIn("804215", value, field.name)
            self.assertNotIn("NegosyoOnline code", value, field.name)
        names = {field.name for field in SmsMessage._meta.get_fields()}
        self.assertFalse(names & {"text", "body", "message", "content"})
        self.assertNotIn("804215", str(message))
        self.assertNotIn(MOBILE, str(message))

    def test_10_the_cache_holds_ciphertext_that_expires_with_the_text(self):
        with mock.patch.object(sms, "cache", wraps=cache) as spied:
            message = send_status(self.site, ttl=420).message
        held = [cache.get(key) for key in (sms.text_key(message), sms.fallback_key(message))]

        for value in held:
            self.assertIsInstance(value, str)
            for plain in (STATUS_TEXT, "1042", MOBILE, "buyer@example.test", "shipped"):
                self.assertNotIn(plain, value)
        lifetimes = {call.args[0]: call.args[2] for call in spied.set.call_args_list}
        self.assertEqual(lifetimes[sms.text_key(message)], 420)
        # The fallback outlives the text by the wait for the phone's report, and no longer.
        self.assertEqual(lifetimes[sms.fallback_key(message)], 420 + sms.NO_REPORT_AFTER + 60)

    def test_10_an_expired_text_is_gone(self):
        message = send_code(ttl=1).message
        time.sleep(1.2)
        self.assertIsNone(cache.get(sms.text_key(message)))
        self.assertIsNone(sms.take_text(message))

    def test_10_the_text_is_taken_once(self):
        message = send_status(self.site).message

        self.assertEqual(sms.take_text(message), STATUS_TEXT)
        self.assertIsNone(sms.take_text(message))
        self.assertEqual(sms.take_fallback(message), fallback())
        self.assertIsNone(sms.take_fallback(message))

    def test_10_a_code_holds_no_fallback(self):
        message = send_code().message
        self.assertIsNone(cache.get(sms.fallback_key(message)))
        self.assertEqual(sms.take_text(message), CODE_TEXT)

    def test_10_a_value_that_was_not_encrypted_here_is_not_handed_out(self):
        message = send_code().message
        cache.set(sms.text_key(message), "not-a-ciphertext", 60)
        self.assertIsNone(sms.take_text(message))

    def test_11_no_log_line_holds_a_number_or_a_text(self):
        with self.assertLogs(level="DEBUG") as logs:
            send_code()
            send_status(self.site)
            set_policy(sending_stopped=True)
            send_code()
            send_status(self.site)
            set_policy(sending_stopped=False, platform_daily_limit=0)
            send_code()
            set_policy(platform_daily_limit=2000)
            SmsDevice.objects.update(last_contact_at=None)
            send_code()
            with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("down")):
                send_status(self.site)
            with override_settings(SMS_GATEWAY_ENABLED=False):
                send_code()

        everything = "\n".join(logs.output)
        self.assertIn("sms_not_sent", everything)
        for private in (MOBILE, "9171234567", "123456", "1042", "buyer@example.test", "on its way"):
            self.assertNotIn(private, everything)


class PurposeListTests(TestCase):
    def test_15_no_marketing_purpose_is_declared(self):
        # The one bulk purpose is the announcement of a Bulk SMS batch; it is the only purpose
        # in the lowest, paced sending class, and the list is closed.
        self.assertEqual(set(sms.PURPOSES), {"mobile_verify", "inbox_reply", "inbox_compose", "inbox_bulk"})
        for name, purpose in sms.PURPOSES.items():
            with self.subTest(purpose=name):
                self.assertIn(purpose.priority, (sms.Priority.CODE, sms.Priority.REPLY, sms.Priority.MARKETING))
                self.assertIn(purpose.category, (sms.CODE, sms.INBOX))
                if name != "inbox_bulk":
                    self.assertNotEqual(purpose.priority, sms.Priority.MARKETING)

    def test_30_the_two_staff_purposes_are_declared(self):
        for name in ("inbox_reply", "inbox_compose"):
            with self.subTest(purpose=name):
                purpose = sms.PURPOSES[name]
                self.assertEqual(purpose.category, "inbox")
                self.assertEqual(purpose.priority, sms.Priority.REPLY)
                self.assertTrue(purpose.paced)
        self.assertFalse(sms.PURPOSES["mobile_verify"].paced)
        self.assertEqual(sorted(sms.paced_purposes()), ["inbox_bulk", "inbox_compose", "inbox_reply"])


class StaffTextTests(SmsTestCase):
    def setUp(self):
        super().setUp()
        online_phone()

    def staff(self, **fields):
        fields = {
            "to": MOBILE, "purpose": "inbox_reply", "text": "Yes, we are open.",
            "ttl": sms.STAFF_TEXT_LIFETIME, **fields,
        }
        return send_sms(**fields)

    def test_31_a_staff_text_makes_a_send_record_without_a_site_or_a_fallback(self):
        result = self.staff()

        self.assertEqual((result.outcome, result.reason), (sms.QUEUED, ""))
        message = SmsMessage.objects.get()
        self.assertEqual((message.category, message.purpose), ("inbox", "inbox_reply"))
        self.assertEqual(message.priority, SmsMessage.Priority.REPLY)
        self.assertIsNone(message.site)
        self.assertIsNone(sms.take_fallback(message))
        self.assertAlmostEqual(
            message.expires_at, message.created_at + timedelta(hours=1), delta=timedelta(seconds=5)
        )
        self.assertEqual(sms.platform_count(), 1)

    def test_32_the_reserve_refuses_a_staff_text_and_not_a_code(self):
        set_policy(platform_daily_limit=305)
        for _ in range(5):
            self.assertEqual(self.staff().outcome, sms.QUEUED)

        with self.assertLogs("apps.notifications", level="ERROR"):
            result = self.staff()
        self.assertEqual((result.outcome, result.reason), (sms.REFUSED, "staff_limit"))
        self.assertEqual(result.message.status, SmsMessage.Status.REFUSED)
        self.assertEqual(sms.platform_count(), 5)
        self.assertEqual(send_code().outcome, sms.QUEUED)
        self.assertEqual(sms.platform_count(), 6)

        for limit in (300, 100):
            with self.subTest(limit=limit):
                cache.clear()
                SmsMessage.objects.all().delete()
                set_policy(platform_daily_limit=limit)
                with self.assertLogs("apps.notifications", level="ERROR"):
                    self.assertEqual(self.staff().reason, "staff_limit")

    def test_33_a_refused_staff_text_gives_back_its_slot(self):
        set_policy(platform_daily_limit=301)
        self.assertEqual(self.staff().outcome, sms.QUEUED)
        with self.assertLogs("apps.notifications", level="ERROR"):
            self.assertEqual(self.staff().reason, "staff_limit")
        self.assertEqual(sms.platform_count(), 1)

        set_policy(platform_daily_limit=2000)
        SmsDevice.objects.update(last_contact_at=timezone.now() - timedelta(minutes=5))
        with self.assertLogs("apps.notifications", level="ERROR"):
            self.assertEqual(self.staff().reason, "gateway_offline")
        self.assertEqual(sms.platform_count(), 1)

    def test_34_the_helpers_other_apps_call(self):
        from apps.notifications import services

        for text in ("fine", "", "x" * 161, "see example.com"):
            self.assertEqual(services.sms_text_problem(text), sms.text_problem(text))
        self.assertEqual(services.sms_line(), sms.pick_device())
        self.assertEqual(
            services.sms_status(),
            {"phone_online": True, "used_today": 0, "cap": 2000, "reserved_for_codes": 300},
        )
        self.assertEqual(services.staff_send_records(MOBILE).count(), 0)
        self.staff()
        send_code()
        self.assertEqual(services.staff_send_records(MOBILE).count(), 1)

    def test_34_with_the_gateway_off_the_status_is_offline_and_reads_no_table(self):
        from apps.notifications import services

        with override_settings(SMS_GATEWAY_ENABLED=False), self.assertNumQueries(0):
            self.assertEqual(
                services.sms_status(),
                {"phone_online": False, "used_today": None, "cap": None, "reserved_for_codes": None},
            )
