from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, Session
from apps.common import retention

RULE = "expired_subscriber_sessions"


def days(n, minutes=0):
    return timedelta(days=n, minutes=minutes)


@override_settings(RETENTION_ENABLED=True)
class ExpiredSubscriberSessionsTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.account = Account.objects.create(email="ana@example.test")

    def session(self, name, expires_ago, revoked_ago=None):
        return Session.objects.create(
            account=self.account,
            token_hash=name,
            device_label=name,
            last_used_at=self.now,
            expires_at=self.now - expires_ago,
            revoked_at=None if revoked_ago is None else self.now - revoked_ago,
        )

    def build(self):
        self.session("expired-30", days(30))
        self.session("expired-29", days(30, minutes=-1))
        self.session("live", -days(10))
        self.session("revoked-30", -days(10), revoked_ago=days(30))
        self.session("revoked-29", -days(10), revoked_ago=days(29))

    def left(self):
        return sorted(Session.objects.values_list("device_label", flat=True))

    def test_deletes_sessions_30_days_after_expiry_or_revocation_and_no_others(self):
        self.build()
        report = retention.run_all(now=self.now, only=[RULE])
        self.assertEqual(report.counts, {RULE: 2})
        self.assertEqual(self.left(), ["expired-29", "live", "revoked-29"])
        self.assertTrue(Account.objects.filter(pk=self.account.pk).exists())

    def test_the_dry_run_counts_the_same_and_deletes_nothing(self):
        self.build()
        report = retention.run_all(now=self.now, dry_run=True, only=[RULE])
        self.assertEqual(report.counts, {RULE: 2})
        self.assertEqual(Session.objects.count(), 5)

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_nothing_is_deleted(self):
        self.build()
        retention.run_all(now=self.now, only=[RULE])
        self.assertEqual(Session.objects.count(), 5)
