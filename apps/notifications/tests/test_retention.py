from datetime import datetime, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, MobileOtpChallenge
from apps.common import retention
from apps.notifications import retention as notifications_retention
from apps.notifications.models import EmailMessage, SmsMessage
from apps.notifications.tests.base import gateway_on
from apps.operators.models import OperatorAuditEntry

SECOND = timedelta(seconds=1)

RULE = "email_messages"


@override_settings(RETENTION_ENABLED=True)
class EmailMessagesRetentionTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def record(self, category, age):
        message = EmailMessage.objects.create(recipient="ana@example.test", category=category)
        EmailMessage.objects.filter(pk=message.pk).update(created_at=self.now - age)

    def build(self):
        self.record("two-years", timedelta(days=730))
        self.record("older", timedelta(days=900))
        self.record("day-729", timedelta(days=729))
        self.record("new", timedelta(0))
        SmsMessage.objects.create(
            recipient="+639171234567", category="code", purpose="test", priority=10,
            expires_at=self.now,
        )
        SmsMessage.objects.update(created_at=self.now - timedelta(days=900))

    def left(self):
        return sorted(EmailMessage.objects.values_list("category", flat=True))

    def test_deletes_email_records_after_2_years_and_nothing_else(self):
        self.build()
        report = retention.run_all(now=self.now, only=[RULE])
        self.assertEqual(report.counts, {RULE: 2})
        self.assertEqual(self.left(), ["day-729", "new"])
        self.assertEqual(SmsMessage.objects.count(), 1)  # SMS records are not this build's

    def test_the_dry_run_counts_the_same_and_deletes_nothing(self):
        self.build()
        report = retention.run_all(now=self.now, dry_run=True, only=[RULE])
        self.assertEqual(report.counts, {RULE: 2})
        self.assertEqual(EmailMessage.objects.count(), 4)

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_nothing_is_deleted(self):
        self.build()
        retention.run_all(now=self.now, only=[RULE])
        self.assertEqual(EmailMessage.objects.count(), 4)


@override_settings(RETENTION_ENABLED=True)
class BillingEmailRecordsRetentionTests(TestCase):
    """Records of billing emails follow the Privacy billing period, not the 2-year rule."""

    def record(self, category, created_at):
        message = EmailMessage.objects.create(recipient="ana@example.test", category=category)
        EmailMessage.objects.filter(pk=message.pk).update(created_at=created_at)

    def at(self, year, month, day):
        return datetime(year, month, day, 12, tzinfo=notifications_retention.MANILA)

    def left(self):
        return sorted(EmailMessage.objects.values_list("category", flat=True))

    def test_a_billing_record_older_than_2_years_is_kept(self):
        now = self.at(2031, 6, 1)
        self.record("billing_payment_approved", self.at(2028, 1, 10))
        self.record("signin_code", self.at(2028, 1, 10))
        retention.run_all(now=now, only=[RULE])
        self.assertEqual(self.left(), ["billing_payment_approved"])

    def test_every_named_billing_category_is_kept_at_2_years(self):
        now = self.at(2031, 6, 1)
        for category in notifications_retention.BILLING_DOCUMENT_CATEGORIES:
            self.record(category, self.at(2028, 1, 10))
        retention.run_all(now=now, only=[RULE])
        self.assertEqual(
            self.left(), sorted(notifications_retention.BILLING_DOCUMENT_CATEGORIES)
        )

    def test_a_billing_notice_that_is_not_a_payment_email_is_deleted_at_2_years(self):
        now = self.at(2031, 6, 1)
        for code in ("trial_reminder_1d", "renewal_reminder_7d", "trial_ended",
                     "service_paused", "export_notice"):
            self.record(f"billing_{code}", self.at(2028, 1, 10))
        retention.run_all(now=now, only=[RULE])
        self.assertEqual(self.left(), [])

    def test_a_non_billing_record_is_still_deleted_at_2_years(self):
        now = self.at(2031, 6, 1)
        self.record("signin_code", now - timedelta(days=731))
        self.record("billing_staff_proof_waiting", now - timedelta(days=731))
        self.record("signin_code", now - timedelta(days=729))
        retention.run_all(now=now, only=[RULE])
        self.assertEqual(len(self.left()), 1)

    def test_a_billing_record_past_its_period_is_deleted(self):
        # Made in 2025: the 2025 return is due in April 2026; 5 years run to 1 May 2031.
        self.record("billing_payment_approved", self.at(2025, 12, 31))
        self.record("billing_payment_approved", self.at(2026, 1, 1))
        before = self.at(2031, 4, 30)
        retention.run_all(now=before, only=[RULE])
        self.assertEqual(EmailMessage.objects.count(), 2)  # the 2025 record is still inside
        retention.run_all(now=self.at(2031, 5, 1), only=[RULE])
        self.assertEqual(EmailMessage.objects.count(), 1)  # the 2025 record went, 2026 stays
        retention.run_all(now=self.at(2032, 5, 1), only=[RULE])
        self.assertEqual(EmailMessage.objects.count(), 0)

    def test_the_dry_run_counts_the_same_as_a_real_run(self):
        now = self.at(2031, 6, 1)
        self.record("signin_code", self.at(2028, 1, 10))
        self.record("billing_payment_approved", self.at(2028, 1, 10))
        self.record("billing_payment_approved", self.at(2025, 3, 1))
        dry = retention.run_all(now=now, dry_run=True, only=[RULE])
        self.assertEqual(EmailMessage.objects.count(), 3)
        real = retention.run_all(now=now, only=[RULE])
        self.assertEqual(dry.counts, {RULE: 2})
        self.assertEqual(real.counts, dry.counts)
        self.assertEqual(self.left(), ["billing_payment_approved"])


class EarlierAddressRecordsTests(TestCase):
    """The send records of an address an account used before an email change (called only by
    account disposal)."""

    def setUp(self):
        self.now = timezone.now()
        self.changed_at = self.now - timedelta(days=10)
        self.periods = [("old@example.test", self.changed_at)]

    def record(self, category, offset, recipient="old@example.test"):
        message = EmailMessage.objects.create(recipient=recipient, category=category)
        EmailMessage.objects.filter(pk=message.pk).update(created_at=self.changed_at + offset)
        return message.pk

    def test_records_up_to_the_change_and_its_notice_are_deleted_and_no_others(self):
        gone = [
            self.record("signin_code", timedelta(0)),
            self.record("signin_code", -timedelta(days=300), recipient="OLD@example.test"),
            self.record("email_changed_notice", timedelta(hours=24)),
        ]
        stay = [
            self.record("signin_code", SECOND),
            self.record("email_changed_notice", timedelta(hours=24) + SECOND),
            self.record("admin_signin_code", -timedelta(days=1)),
            self.record("admin_lockout", -timedelta(days=1)),
            self.record("signin_code", -timedelta(days=1), recipient="other@example.test"),
        ]
        dry = notifications_retention.delete_earlier_records_for(self.periods, dry_run=True)
        self.assertEqual((dry, EmailMessage.objects.count()), (3, 8))
        self.assertEqual(notifications_retention.delete_earlier_records_for(self.periods), 3)
        self.assertFalse(EmailMessage.objects.filter(pk__in=gone).exists())
        self.assertEqual(EmailMessage.objects.filter(pk__in=stay).count(), 5)

    def test_no_earlier_address_deletes_nothing(self):
        self.record("signin_code", timedelta(0))
        self.assertEqual(notifications_retention.delete_earlier_records_for([]), 0)
        self.assertEqual(notifications_retention.delete_earlier_records_for([], dry_run=True), 0)
        self.assertEqual(EmailMessage.objects.count(), 1)


@gateway_on
class SmsRetentionTests(TestCase):
    """T-RET: the two SMS rules. They are not behind RETENTION_ENABLED, which is off here."""

    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.account = Account.objects.create(email="ana@example.test")

    def text(self, purpose, age):
        message = SmsMessage.objects.create(
            recipient="+639171234567", category="code", purpose=purpose, priority=10,
            expires_at=self.now,
        )
        SmsMessage.objects.filter(pk=message.pk).update(created_at=self.now - age)

    def challenge(self, name, expired_ago):
        return MobileOtpChallenge.objects.create(
            account=self.account, number="+639171234567", code_hash="0" * 64,
            expires_at=self.now - expired_ago, sms_public_id=name,
        )

    def build(self):
        self.text("year-and-a-second", timedelta(days=365, seconds=1))
        self.text("year", timedelta(days=365))
        self.text("second-short", timedelta(days=365) - SECOND)
        self.text("day-364", timedelta(days=364))
        self.text("new", timedelta(0))
        self.challenge("day-and-a-second", timedelta(hours=24) + SECOND)
        self.challenge("day", timedelta(hours=24))
        self.challenge("second-short", timedelta(hours=24) - SECOND)
        self.challenge("open", -timedelta(minutes=10))
        EmailMessage.objects.create(recipient="ana@example.test", category="old")
        EmailMessage.objects.update(created_at=self.now - timedelta(days=900))

    def texts_left(self):
        return sorted(SmsMessage.objects.values_list("purpose", flat=True))

    def challenges_left(self):
        return sorted(MobileOtpChallenge.objects.values_list("sms_public_id", flat=True))

    def test_both_rules_are_registered(self):
        self.assertIn("sms_messages", retention.names())
        self.assertIn("mobile_challenges", retention.names())
        with self.assertRaises(ValueError):  # T-RET-1: a rule that is not registered cannot run
            retention.run_all(now=self.now, only=["sms_text_bodies"])

    def test_3_sms_messages_deletes_a_year_after_the_record_was_written(self):
        self.build()

        report = retention.run_all(now=self.now, only=["sms_messages"])

        self.assertEqual(report.counts, {"sms_messages": 2})
        self.assertEqual(report.switched_off, [])
        self.assertEqual(self.texts_left(), ["day-364", "new", "second-short"])
        self.assertEqual(MobileOtpChallenge.objects.count(), 4)
        self.assertEqual(EmailMessage.objects.count(), 1)

    def test_4_mobile_challenges_deletes_24_hours_after_expiry(self):
        self.build()

        report = retention.run_all(now=self.now, only=["mobile_challenges"])

        self.assertEqual(report.counts, {"mobile_challenges": 2})
        self.assertEqual(self.challenges_left(), ["open", "second-short"])
        self.assertEqual(SmsMessage.objects.count(), 5)
        self.assertTrue(Account.objects.filter(pk=self.account.pk).exists())

    def test_2_the_dry_run_counts_the_same_and_deletes_nothing(self):
        self.build()

        report = retention.run_all(
            now=self.now, dry_run=True, only=["sms_messages", "mobile_challenges"]
        )

        self.assertEqual(report.counts, {"sms_messages": 2, "mobile_challenges": 2})
        self.assertEqual(SmsMessage.objects.count(), 5)
        self.assertEqual(MobileOtpChallenge.objects.count(), 4)
        self.assertFalse(OperatorAuditEntry.objects.filter(action="retention_run").exists())

    def test_5_the_run_writes_one_audit_row_with_the_counts(self):
        self.build()

        retention.run_all(now=self.now)

        entry = OperatorAuditEntry.objects.get(action="retention_run")
        self.assertEqual(entry.detail["counts"]["sms_messages"], 2)
        self.assertEqual(entry.detail["counts"]["mobile_challenges"], 2)
        self.assertNotIn("sms_messages", entry.detail["switched_off"])
        self.assertNotIn("mobile_challenges", entry.detail["switched_off"])
        self.assertIn("email_messages", entry.detail["switched_off"])  # RETENTION_ENABLED is off
        self.assertEqual(EmailMessage.objects.count(), 1)

    def test_gateway_off_the_rules_touch_no_table(self):
        self.build()

        with override_settings(SMS_GATEWAY_ENABLED=False):
            for rule in (notifications_retention.sms_messages,
                         notifications_retention.mobile_challenges):
                for dry_run in (False, True):
                    with self.subTest(rule=rule.__name__, dry_run=dry_run):
                        with self.assertNumQueries(0):
                            self.assertEqual(rule(self.now, dry_run), 0)
            report = retention.run_all(now=self.now, only=["sms_messages", "mobile_challenges"])

        self.assertEqual(report.counts, {"sms_messages": 0, "mobile_challenges": 0})
        self.assertEqual(SmsMessage.objects.count(), 5)
        self.assertEqual(MobileOtpChallenge.objects.count(), 4)


class DailyStartTests(TestCase):
    """T-RET-6: the once-a-day start."""

    def setUp(self):
        cache.clear()
        patcher = mock.patch("apps.operators.background.run_later")
        self.run_later = patcher.start()
        self.addCleanup(patcher.stop)

    def test_6_it_runs_once_however_many_calls_arrive(self):
        started = [notifications_retention.start_daily() for _ in range(5)]

        self.assertEqual(started, [True, False, False, False, False])
        self.run_later.assert_called_once_with(notifications_retention.run_daily_rules)

    def test_6_it_runs_the_sms_rules_and_no_other_registered_rule(self):
        ran = []
        other = mock.patch.dict(
            retention._rules, {"another_builds_rule": lambda now, dry_run: ran.append(1) or 0}
        )
        with other, override_settings(RETENTION_ENABLED=True):
            self.assertIn("another_builds_rule", retention.names())
            report = notifications_retention.run_daily_rules()

        self.assertEqual(sorted(report.counts), ["bulk_batches", "inbox_messages", "mobile_challenges", "sms_messages"])
        self.assertEqual(ran, [])
        entry = OperatorAuditEntry.objects.get(action="retention_run")
        self.assertEqual(sorted(entry.detail["counts"]), ["bulk_batches", "inbox_messages", "mobile_challenges", "sms_messages"])
        self.assertEqual(entry.detail["switched_off"], [])

    def test_6_it_runs_again_the_next_manila_day(self):
        today = timezone.localdate()
        notifications_retention.start_daily()

        with mock.patch.object(timezone, "localdate", return_value=today + timedelta(days=1)):
            self.assertTrue(notifications_retention.start_daily())
            self.assertFalse(notifications_retention.start_daily())

        self.assertEqual(self.run_later.call_count, 2)
