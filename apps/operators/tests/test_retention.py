from datetime import timedelta

from django.contrib.sessions.models import Session as DjangoSession
from django.test import override_settings
from django.utils import timezone

from apps.common import retention
from apps.operators.models import (
    AdminIdentity,
    AdminRecoveryCode,
    AdminSession,
    AdminSignInChallenge,
)
from apps.operators.tests.base import TwoStepTestCase

CODES, SESSIONS, DJANGO = (
    "expired_admin_sign_in_codes",
    "expired_admin_sessions",
    "expired_django_sessions",
)
RULES = [CODES, SESSIONS, DJANGO]
JUST_UNDER = timedelta(days=30, minutes=-1)
THIRTY = timedelta(days=30)


@override_settings(RETENTION_ENABLED=True)
class AdminSignInRetentionTests(TwoStepTestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.identity = self.enrolled()

    def challenge(self, nonce, expires_ago):
        return AdminSignInChallenge.objects.create(
            identity=self.identity, code_hmac="h", nonce=nonce, expires_at=self.now - expires_ago
        )

    def admin_session(self, name, expires_ago, revoked_ago=None):
        return AdminSession.objects.create(
            identity=self.identity,
            token_hash=name,
            credential_version=1,
            expires_at=self.now - expires_ago,
            revoked_at=None if revoked_ago is None else self.now - revoked_ago,
        )

    def django_session(self, key, expires_ago):
        return DjangoSession.objects.create(
            session_key=key, session_data="x", expire_date=self.now - expires_ago
        )

    def build(self):
        self.challenge("old", THIRTY)
        self.challenge("recent", JUST_UNDER)
        self.challenge("live", -timedelta(minutes=5))
        self.admin_session("old", THIRTY)
        self.admin_session("recent", JUST_UNDER)
        self.admin_session("revoked-old", -timedelta(hours=1), revoked_ago=THIRTY)
        self.admin_session("live", -timedelta(hours=1))
        self.django_session("old", THIRTY)
        self.django_session("recent", JUST_UNDER)

    def state(self):
        return (
            sorted(AdminSignInChallenge.objects.values_list("nonce", flat=True)),
            sorted(AdminSession.objects.values_list("token_hash", flat=True)),
            sorted(DjangoSession.objects.values_list("session_key", flat=True)),
            AdminRecoveryCode.objects.count(),
            AdminIdentity.objects.count(),
        )

    def test_each_rule_deletes_exactly_its_own_rows(self):
        self.build()
        before = self.state()

        report = retention.run_all(now=self.now, only=[CODES])
        self.assertEqual(report.counts, {CODES: 1})
        self.assertEqual(self.state(), (["live", "recent"], *before[1:]))

        report = retention.run_all(now=self.now, only=[SESSIONS])
        self.assertEqual(report.counts, {SESSIONS: 2})
        self.assertEqual(self.state(), (["live", "recent"], ["live", "recent"], *before[2:]))

        report = retention.run_all(now=self.now, only=[DJANGO])
        self.assertEqual(report.counts, {DJANGO: 1})
        self.assertEqual(
            self.state(), (["live", "recent"], ["live", "recent"], ["recent"], *before[3:])
        )
        self.assertEqual(before[3], 10)  # the recovery codes are never touched

    def test_the_dry_run_counts_the_same_and_deletes_nothing(self):
        self.build()
        before = self.state()
        report = retention.run_all(now=self.now, dry_run=True, only=RULES)
        self.assertEqual(report.counts, {CODES: 1, SESSIONS: 2, DJANGO: 1})
        self.assertEqual(self.state(), before)

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_nothing_is_deleted(self):
        self.build()
        before = self.state()
        retention.run_all(now=self.now, only=RULES)
        self.assertEqual(self.state(), before)
