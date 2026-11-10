import re
from contextlib import nullcontext
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.operators import totp, two_step
from apps.operators.management.commands.admin_identity import Command
from apps.operators.models import AdminIdentity, AdminSession, OperatorAuditEntry
from apps.operators.tests.base import EMAIL, TwoStepTestCase


class AdminIdentityCommandTests(TwoStepTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.staff("coach")
        self.user = self.staff("operator")
        patcher = mock.patch.object(Command, "_interactive", return_value=True)
        self.interactive = patcher.start()
        self.addCleanup(patcher.stop)

    def run_command(self, *args, **options):
        out = StringIO()
        logs = options.pop("logs", False)
        with self.assertLogs("apps.operators", "ERROR") if logs else nullcontext():
            call_command("admin_identity", *args, stdout=out, **options)
        return out.getvalue()

    def add(self, email=EMAIL, user="operator"):
        return self.run_command("add", email, user=user, actor="coach", logs=True)

    def refused(self, message, *args, **options):
        with self.assertRaisesMessage(CommandError, message):
            self.run_command(*args, **options)

    def audit(self):
        return [
            (e.action, e.operator, e.target_type, e.target_id)
            for e in OperatorAuditEntry.objects.order_by("pk")
        ]

    def test_add_creates_an_unenrolled_identity_and_audits_it(self):
        self.add("  Admin@Example.TEST ")
        identity = AdminIdentity.objects.get()
        self.assertEqual((identity.email, identity.user), (EMAIL, self.user))
        self.assertIsNone(identity.enrolled_at)
        self.assertEqual(identity.totp_secret_ciphertext, "")
        self.assertEqual(
            self.audit(), [("admin_identity_add", self.actor, "admin_identity", str(identity.pk))]
        )

    def test_add_needs_an_actor_and_a_user_who_are_active_staff(self):
        self.refused("--actor is required.", "add", EMAIL, user="operator")
        self.refused("--user is required.", "add", EMAIL, actor="coach")
        self.refused("--actor must be an active staff user.", "add", EMAIL, user="operator", actor="nobody")
        self.user.is_staff = False
        self.user.save()
        self.refused("--user must be an active staff user.", "add", EMAIL, user="operator", actor="coach")
        self.actor.is_active = False
        self.actor.save()
        self.refused("--actor must be an active staff user.", "add", EMAIL, user="operator", actor="coach")
        self.assertFalse(AdminIdentity.objects.exists())
        self.assertEqual(self.audit(), [])

    def test_add_refuses_a_bad_or_over_long_address(self):
        self.refused("That is not a valid email address.", "add", "not-an-email", user="operator", actor="coach")
        self.refused("That is not a valid email address.", "add", user="operator", actor="coach")
        long = "a" * 250 + "@example.test"
        self.refused("at most 254 characters", "add", long, user="operator", actor="coach")

    def test_add_refuses_an_address_that_exists_even_when_disabled(self):
        self.add()
        self.refused("already exists", "add", EMAIL, user="coach", actor="coach")
        self.run_command("disable", EMAIL, actor="coach", logs=True)
        self.refused("already exists", "add", EMAIL, user="coach", actor="coach")

    def test_a_user_has_one_active_identity(self):
        self.add()
        self.refused(
            "already has an active admin identity", "add", "second@example.test",
            user="operator", actor="coach",
        )
        self.run_command("disable", EMAIL, actor="coach", logs=True)
        self.add("second@example.test")

    def test_enrol_shows_the_key_and_ten_codes_once_and_stores_neither(self):
        self.add()
        output = self.run_command("enrol", EMAIL, actor="coach", logs=True)
        identity = AdminIdentity.objects.get()
        secret = re.search(r"Setup key: ([A-Z2-7 ]+)", output).group(1).replace(" ", "")
        codes = re.findall(r"^  ([A-Z2-9]{5}-[A-Z2-9]{5})$", output, re.M)
        self.assertEqual(len(codes), 10)
        self.assertIn(f"secret={secret}", output)
        self.assertIn("otpauth://totp/", output)
        self.assertIsNotNone(identity.enrolled_at)
        self.assertEqual(identity.credential_version, 2)
        self.assertNotIn(secret, identity.totp_secret_ciphertext)
        self.assertEqual(identity.recovery_codes.count(), 10)
        # The shown key and codes are the ones that work.
        code = totp.code_at(secret, totp.current_step(timezone.now()))
        self.assertTrue(two_step.verify_second_step(identity.pk, code, surface="hq").ok)
        self.assertTrue(two_step.verify_second_step(identity.pk, codes[0], surface="hq").ok)
        self.assertEqual(self.audit()[1][0], "admin_identity_enrol")

    def test_enrol_is_refused_with_an_empty_or_invalid_secrets_key(self):
        self.add()
        for key in ("", "not-a-key"):
            with override_settings(ADMIN_SECRETS_KEY=key):
                self.refused("ADMIN_SECRETS_KEY is not set", "enrol", EMAIL, actor="coach")
        identity = AdminIdentity.objects.get()
        self.assertIsNone(identity.enrolled_at)
        self.assertEqual(identity.recovery_codes.count(), 0)
        self.assertEqual(len(self.audit()), 1)

    def test_enrol_is_refused_without_an_interactive_terminal(self):
        self.add()
        self.interactive.return_value = False
        self.refused("needs an interactive terminal", "enrol", EMAIL, actor="coach")
        self.assertIsNone(AdminIdentity.objects.get().enrolled_at)

    def test_enrol_is_refused_for_unknown_disabled_and_enrolled_identities(self):
        self.refused("No such admin identity", "enrol", EMAIL, actor="coach")
        self.add()
        self.run_command("enrol", EMAIL, actor="coach", logs=True)
        self.refused("already enrolled", "enrol", EMAIL, actor="coach")
        self.run_command("disable", EMAIL, actor="coach", logs=True)
        self.refused("disabled", "enrol", EMAIL, actor="coach")

    def session(self, identity):
        return AdminSession.objects.create(
            identity=identity, token_hash="b" * 64, credential_version=identity.credential_version,
            expires_at=timezone.now() + timedelta(hours=12),
        )

    def test_reset_clears_the_secret_and_codes_and_ends_sessions(self):
        identity = self.enrolled(user=self.user)
        session = self.session(identity)
        version = identity.credential_version
        self.run_command("reset", EMAIL, actor="coach", logs=True)
        identity.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(identity.totp_secret_ciphertext, "")
        self.assertIsNone(identity.enrolled_at)
        self.assertEqual(identity.recovery_codes.count(), 0)
        self.assertEqual(identity.credential_version, version + 1)
        self.assertIsNotNone(session.revoked_at)
        self.assertEqual(self.audit()[-1][0], "admin_identity_reset")
        self.run_command("enrol", EMAIL, actor="coach", logs=True)  # enrol works again

    def test_disable_keeps_the_row_and_ends_sessions(self):
        identity = self.enrolled(user=self.user)
        session = self.session(identity)
        self.run_command("disable", EMAIL, actor="coach", logs=True)
        identity.refresh_from_db()
        session.refresh_from_db()
        self.assertFalse(identity.is_active)
        self.assertIsNotNone(session.revoked_at)
        self.assertFalse(two_step.verify_second_step(identity.pk, self.code(), surface="hq").ok)
        self.assertEqual(self.audit()[-1][0], "admin_identity_disable")
        self.assertIn("Already disabled", self.run_command("disable", EMAIL, actor="coach"))
        self.assertEqual(len(self.audit()), 1)

    def test_list_writes_nothing_and_shows_who_is_not_covered(self):
        self.enrolled(user=self.user)
        output = self.run_command("list")
        self.assertIn(f"{EMAIL}\tuser=operator\tactive=True\tenrolled=True\tlocked=False", output)
        self.assertIn("Staff users with no enrolled identity", output)
        self.assertIn("  coach", output)
        self.assertNotIn("  operator", output)
        self.assertIn("(they cannot open the admin lists)", output)
        self.assertNotIn("ADMIN_TWO_STEP_REQUIRED", output)
        self.assertNotIn("Warning", output)
        self.assertEqual(self.audit(), [])

    def test_every_change_writes_one_error_log_line_without_the_address(self):
        with self.assertLogs("apps.operators", "ERROR") as logs:
            call_command("admin_identity", "add", EMAIL, user="operator", actor="coach", stdout=StringIO())
        self.assertEqual(len(logs.output), 1)
        self.assertIn("admin-identity-change: admin_identity_add", logs.output[0])
        self.assertNotIn(EMAIL, logs.output[0])
