from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Account, AccountAuditEvent, Session
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


EVENTS = "account_audit_events"


@override_settings(RETENTION_ENABLED=True)
class AccountAuditEventsTests(TestCase):
    """Security records are kept 1 year. An email change is kept 2 years, like the send
    records of the address it names, so a disposal can still find that address."""

    def setUp(self):
        self.now = timezone.now()
        self.account = Account.objects.create(email="ana@example.test")
        self.other = Account.objects.create(email="ben@example.test")

    def event(self, name, age, account=None, **detail):
        row = AccountAuditEvent.objects.create(
            account=account or self.account, event=name, detail=detail
        )
        AccountAuditEvent.objects.filter(pk=row.pk).update(created_at=self.now - age)
        return row.pk

    def build(self):
        self.gone = [
            self.event("signed_in", days(365), method="email_code"),
            self.event("account_created", days(400), method="google"),
            self.event("site_created", days(365), site="sit_1"),
            self.event("signed_in", days(365), account=self.other),
            self.event("email_changed", days(730), old="a@example.test", new="b@example.test"),
        ]
        self.stay = [
            self.event("signed_in", days(365, minutes=-1)),
            self.event("sessions_revoked", days(10), count=2),
            self.event("email_changed", days(365), old="b@example.test", new="c@example.test"),
            self.event("email_changed", days(730, minutes=-1), old="c@example.test", new="d@example.test"),
            self.event("signed_in", days(1), account=self.other),
        ]

    def left(self):
        return sorted(AccountAuditEvent.objects.values_list("pk", flat=True))

    def test_it_is_registered(self):
        self.assertIn(EVENTS, retention.names())

    def test_deletes_events_after_1_year_and_email_changes_after_2(self):
        self.build()
        report = retention.run_all(now=self.now, only=[EVENTS])
        self.assertEqual(report.counts, {EVENTS: 5})
        self.assertEqual(self.left(), sorted(self.stay))
        self.assertEqual(Account.objects.count(), 2)

    def test_the_dry_run_counts_the_same_and_deletes_nothing(self):
        self.build()
        before = list(AccountAuditEvent.objects.order_by("pk").values())
        report = retention.run_all(now=self.now, dry_run=True, only=[EVENTS])
        self.assertEqual(report.counts, {EVENTS: 5})
        self.assertEqual(list(AccountAuditEvent.objects.order_by("pk").values()), before)

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_nothing_is_deleted(self):
        self.build()
        report = retention.run_all(now=self.now, only=[EVENTS])
        self.assertEqual(report.switched_off, [EVENTS])
        self.assertEqual(len(self.left()), 10)

    def test_the_pause_after_an_email_change_still_holds(self):
        self.build()
        AccountAuditEvent.objects.create(
            account=self.account, event="email_changed",
            detail={"old": "d@example.test", "new": "ana@example.test"},
        )
        retention.run_all(only=[EVENTS])
        self.assertIsNotNone(services.email_change_pause(self.account)[1])
