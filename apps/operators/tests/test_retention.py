from datetime import timedelta
from io import StringIO

from django.contrib.sessions.models import Session as DjangoSession
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.common import retention
from apps.operators import audit
from apps.operators.models import (
    AdminIdentity,
    AdminRecoveryCode,
    AdminSession,
    AdminSignInChallenge,
    OperatorAuditEntry,
)
from apps.operators.retention import admin_audit_personal_values
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


AUDIT = "admin_audit_personal_values"
YEAR = timedelta(days=365)


@override_settings(RETENTION_ENABLED=True)
class AdminAuditPersonalValuesTests(TwoStepTestCase):
    """D4: the rows are kept; their personal values are blanked 1 year after they were written."""

    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.operator = self.staff()

    def entry(self, action, age, personal=None):
        row = audit.record(
            self.operator, action, target_type="admin_identity", target_id=7,
            reason="why", detail={"surface": "hq", "step": "code"}, personal=personal,
        )
        OperatorAuditEntry.objects.filter(pk=row.pk).update(at=self.now - age)
        return row

    def build(self):
        self.old = self.entry("admin_sign_in_ok", YEAR, {"email": "ana@example.test"})
        self.old_empty = self.entry("admin_sign_in_failed", YEAR + timedelta(days=40))
        self.recent = self.entry(
            "admin_locked", YEAR - timedelta(minutes=1), {"email": "ana@example.test"}
        )
        self.kept = self.entry("admin_identity_reset", YEAR)

    def rows(self):
        return list(OperatorAuditEntry.objects.order_by("pk").values())

    def test_old_personal_rows_are_blanked_and_stamped_and_every_row_stays(self):
        self.build()
        before = self.rows()

        report = retention.run_all(now=self.now, only=[AUDIT])

        self.assertEqual(report.counts, {AUDIT: 2})
        after = {row["id"]: row for row in self.rows()}
        for row in before:
            now_row = after[row["id"]]
            if row["id"] in (self.old.pk, self.old_empty.pk):
                self.assertEqual(now_row["personal"], {})
                self.assertEqual(now_row["personal_blanked_at"], self.now)
                kept = set(row) - {"personal", "personal_blanked_at"}
                self.assertEqual({k: now_row[k] for k in kept}, {k: row[k] for k in kept})
            else:
                self.assertEqual(now_row, row)
        self.assertEqual(after[self.recent.pk]["personal"], {"email": "ana@example.test"})
        # Nothing was deleted: the four rows and the run log.
        self.assertEqual(len(after), 5)
        log = OperatorAuditEntry.objects.get(action="retention_run")
        self.assertEqual(log.detail["counts"], {AUDIT: 2})
        self.assertNotIn("example.test", str(log.detail))

    def test_a_second_run_counts_zero(self):
        self.build()
        retention.run_all(now=self.now, only=[AUDIT])
        report = retention.run_all(now=self.now + timedelta(seconds=1), only=[AUDIT])
        self.assertEqual(report.counts, {AUDIT: 0})
        self.old.refresh_from_db()
        self.assertEqual(self.old.personal_blanked_at, self.now)

    def test_the_dry_run_counts_the_same_and_changes_nothing(self):
        self.build()
        before = self.rows()
        report = retention.run_all(now=self.now, dry_run=True, only=[AUDIT])
        self.assertEqual(report.counts, {AUDIT: 2})
        self.assertEqual(self.rows(), before)  # no run log either

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_nothing_is_blanked(self):
        self.build()
        before = self.rows()
        out = StringIO()
        call_command("run_retention", "--only", AUDIT, stdout=out)
        self.assertIn(f"{AUDIT}: switched off", out.getvalue())
        self.assertEqual(self.rows()[:4], before)
        self.assertEqual(admin_audit_personal_values(self.now, False), 0)
        self.assertEqual(self.rows()[:4], before)

    def test_the_rule_is_registered(self):
        self.assertIn(AUDIT, retention.names())
