from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common import retention
from apps.notifications.models import EmailMessage, SmsMessage

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
