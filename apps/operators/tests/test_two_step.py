from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.operators import two_step
from apps.operators.models import AdminIdentity, AdminSession, OperatorAuditEntry
from apps.operators.tests.base import TwoStepTestCase

HQ, DJANGO_ADMIN = two_step.SURFACE_HQ, two_step.SURFACE_DJANGO_ADMIN


class VerifySecondStepTests(TwoStepTestCase):
    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()

    def verify(self, code, surface=HQ):
        with self.committed():
            return two_step.verify_second_step(self.identity.pk, code, surface=surface)

    def actions(self):
        return list(OperatorAuditEntry.objects.order_by("pk").values_list("action", flat=True))

    def live_session(self):
        return AdminSession.objects.create(
            identity=self.identity, token_hash="a" * 64,
            credential_version=self.identity.credential_version,
            expires_at=timezone.now() + timedelta(hours=12),
        )

    def later(self, **delta):
        return mock.patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(**delta))

    def test_the_right_code_passes_and_is_audited(self):
        result = self.verify(self.code())
        self.assertTrue(result.ok)
        self.assertEqual(result.credential_version, self.identity.credential_version)
        entry = OperatorAuditEntry.objects.get()
        self.assertEqual(entry.action, "admin_sign_in_ok")
        self.assertEqual(entry.operator, self.identity.user)
        self.assertEqual(entry.target_id, str(self.identity.pk))
        self.assertEqual(entry.detail, {"surface": "hq", "step": "totp"})
        self.assertEqual(entry.personal, {})

    def test_spaces_in_the_code_are_ignored(self):
        code = self.code()
        self.assertTrue(self.verify(f" {code[:3]} {code[3:]} ").ok)

    def test_a_wrong_code_fails_and_is_counted(self):
        result = self.verify(self.wrong_code())
        self.assertFalse(result.ok)
        self.assertFalse(result.locked)
        self.assertIsNone(result.credential_version)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.failed_attempts, 1)
        self.assertEqual(self.actions(), ["admin_sign_in_failed"])

    def test_a_code_cannot_be_used_twice_on_either_surface(self):
        code = self.code()
        self.assertTrue(self.verify(code, HQ).ok)
        self.assertFalse(self.verify(code, DJANGO_ADMIN).ok)
        self.assertFalse(self.verify(code, HQ).ok)

    def test_a_code_from_an_earlier_step_than_the_last_used_is_refused(self):
        self.assertTrue(self.verify(self.code(0)).ok)
        self.assertFalse(self.verify(self.code(-1)).ok)
        self.assertTrue(self.verify(self.code(1)).ok)

    def test_a_recovery_code_works_once_however_it_is_typed(self):
        code = self.recovery_codes[0]
        typed = f" {code[:5].lower()}-{code[5:].lower()} "
        result = self.verify(typed, DJANGO_ADMIN)
        self.assertTrue(result.ok)
        self.assertEqual(
            OperatorAuditEntry.objects.get().detail, {"surface": "django_admin", "step": "recovery"}
        )
        self.assertFalse(self.verify(code, HQ).ok)
        self.assertTrue(self.verify(self.recovery_codes[1], HQ).ok)

    def test_an_unknown_recovery_code_fails(self):
        self.assertFalse(self.verify("ABCDEFGHJK").ok)
        self.assertFalse(self.verify("").ok)
        self.assertFalse(self.verify("too-short").ok)

    def test_inactive_unenrolled_and_unknown_identities_are_refused_without_a_count(self):
        code = self.code()
        self.identity.is_active = False
        self.identity.save()
        self.assertFalse(self.verify(code).ok)
        self.identity.is_active = True
        self.identity.enrolled_at = None
        self.identity.save()
        self.assertFalse(self.verify(code).ok)
        self.assertFalse(two_step.verify_second_step(987654, code, surface=HQ).ok)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.failed_attempts, 0)
        self.assertEqual(self.actions(), [])

    def test_an_identity_whose_user_is_no_longer_active_staff_is_refused(self):
        for field in ("is_active", "is_staff"):
            user = self.identity.user
            setattr(user, field, False)
            user.save()
            self.assertFalse(self.verify(self.code()).ok)
            setattr(user, field, True)
            user.save()
        self.assertTrue(self.verify(self.code()).ok)

    def test_the_fifth_wrong_code_locks_for_15_minutes_and_ends_sessions(self):
        session = self.live_session()
        version = self.identity.credential_version
        for _ in range(4):
            self.assertFalse(self.verify(self.wrong_code()).locked)
        result = self.verify(self.wrong_code(), DJANGO_ADMIN)
        self.assertFalse(result.ok)
        self.assertTrue(result.locked)
        self.identity.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(self.identity.credential_version, version + 1)
        self.assertEqual(self.identity.failed_attempts, 0)
        self.assertIsNotNone(session.revoked_at)
        remaining = self.identity.locked_until - timezone.now()
        self.assertTrue(timedelta(minutes=14) < remaining <= timedelta(minutes=15))
        self.assertEqual(self.actions(), ["admin_sign_in_failed"] * 5 + ["admin_locked"])

    def test_while_locked_even_the_right_code_is_refused_on_both_surfaces(self):
        for _ in range(5):
            self.verify(self.wrong_code())
        for surface in (HQ, DJANGO_ADMIN):
            result = self.verify(self.code(), surface)
            self.assertFalse(result.ok)
            self.assertTrue(result.locked)
        self.assertFalse(self.verify(self.recovery_codes[0]).ok)

    def test_after_the_lock_the_admin_can_sign_in_again_but_old_sessions_stay_dead(self):
        session = self.live_session()
        for _ in range(5):
            self.verify(self.wrong_code())
        with self.later(minutes=16):
            result = self.verify(self.code())
            self.assertTrue(result.ok)
        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)
        self.assertEqual(result.credential_version, session.credential_version + 1)

    def test_the_lockout_notice_is_emailed_once_after_the_commit(self):
        for _ in range(4):
            self.verify(self.wrong_code())
        self.assertEqual(mail.outbox, [])
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            two_step.verify_second_step(self.identity.pk, self.wrong_code(), surface=HQ)
        self.assertEqual(mail.outbox, [])  # nothing is sent while the row is locked
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.identity.email])
        self.assertEqual(message.subject, "NegosyoOnline admin sign-in paused")
        self.assertEqual(
            message.body,
            "Admin sign-in for this address is paused for 15 minutes after too many wrong codes.",
        )
        self.verify(self.code())  # still locked: no second notice
        self.assertEqual(len(mail.outbox), 1)

    def test_a_notice_that_cannot_be_queued_does_not_break_the_check(self):
        self.run_later.side_effect = RuntimeError("no thread")
        for _ in range(4):
            self.verify(self.wrong_code())
        self.assertTrue(self.verify(self.wrong_code()).locked)

    def test_success_resets_the_failure_count(self):
        for _ in range(4):
            self.verify(self.wrong_code())
        self.assertTrue(self.verify(self.code()).ok)
        for _ in range(4):
            self.assertFalse(self.verify(self.wrong_code()).locked)

    def test_an_empty_or_wrong_secrets_key_fails_without_counting_or_locking(self):
        from cryptography.fernet import Fernet

        for key in ("", Fernet.generate_key().decode()):
            with override_settings(ADMIN_SECRETS_KEY=key), self.assertLogs(
                "apps.operators.two_step", "ERROR"
            ) as logs:
                from django.core.cache import cache

                cache.clear()
                for _ in range(6):
                    result = self.verify(self.code())
                    self.assertFalse(result.ok)
                    self.assertFalse(result.locked)
                self.assertEqual(len(logs.output), 1)  # one line an hour, not one per try
                self.assertIn("admin-secrets-key-empty", logs.output[0])
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.failed_attempts, 0)
        self.assertIsNone(self.identity.locked_until)
        self.assertEqual(self.actions(), [])

    def test_nothing_secret_is_stored_in_the_clear(self):
        self.identity.refresh_from_db()
        self.assertNotIn(self.secret, self.identity.totp_secret_ciphertext)
        stored = set(self.identity.recovery_codes.values_list("code_hash", flat=True))
        self.assertEqual(len(stored), 10)
        self.assertFalse(stored & set(self.recovery_codes))

    def test_reset_then_enrol_does_not_revive_an_old_session(self):
        session = self.live_session()
        identity = AdminIdentity.objects.get(pk=self.identity.pk)
        two_step.clear_enrolment(identity, timezone.now())
        self.assertFalse(self.verify(self.code()).ok)
        self.identity = self.enrolled_again(identity)
        result = self.verify(self.code())
        self.assertTrue(result.ok)
        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)
        self.assertEqual(result.credential_version, session.credential_version + 2)

    def enrolled_again(self, identity):
        from apps.operators import totp

        self.secret = totp.new_secret()
        self.recovery_codes = two_step.new_recovery_codes()
        two_step.store_enrolment(identity, self.secret, self.recovery_codes, timezone.now())
        return identity
