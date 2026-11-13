"""Add, enrol, reset, disable and list admin identities. This is the only way to do so.

    manage.py admin_identity add EMAIL --user USERNAME --actor USERNAME
    manage.py admin_identity enrol EMAIL --actor USERNAME
    manage.py admin_identity reset EMAIL --actor USERNAME
    manage.py admin_identity disable EMAIL --actor USERNAME
    manage.py admin_identity list

`enrol` shows the setup key and the recovery codes once, so it needs an interactive terminal.
"""
import logging
import sys

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.operators import audit, crypto, totp, two_step
from apps.operators.models import AdminIdentity

logger = logging.getLogger(__name__)

ACTIONS = ("add", "enrol", "reset", "disable", "list")
MAX_EMAIL = 254


class Command(BaseCommand):
    help = "Add, enrol, reset, disable or list admin identities for two-step sign-in."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=ACTIONS)
        parser.add_argument("email", nargs="?")
        parser.add_argument("--user", help="add: the staff user this identity belongs to")
        parser.add_argument("--actor", help="the staff user running this command, for the audit record")

    def handle(self, *args, action, email, user, actor, **options):
        if action == "list":
            return self._list()
        actor = self._staff_user(actor, "--actor")
        email = self._email(email)
        getattr(self, f"_{action}")(email, actor, user)

    # Checks

    def _staff_user(self, username, flag):
        if not username:
            raise CommandError(f"{flag} is required.")
        found = get_user_model().objects.filter(
            username=username, is_active=True, is_staff=True
        ).first()
        if found is None:
            raise CommandError(f"{flag} must be an active staff user.")
        return found

    def _email(self, raw):
        email = (raw or "").strip().lower()
        if len(email) > MAX_EMAIL:
            raise CommandError(f"The email may be at most {MAX_EMAIL} characters.")
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError("That is not a valid email address.") from None
        return email

    def _locked_identity(self, email):
        identity = AdminIdentity.objects.select_for_update().filter(email=email).first()
        if identity is None:
            raise CommandError("No such admin identity. Use `add` first.")
        return identity

    def _interactive(self):
        return sys.stdin.isatty() and sys.stdout.isatty()

    def _record(self, actor, action, identity):
        audit.record(actor, action, target_type=audit.IDENTITY_TARGET, target_id=identity.pk)
        logger.error(
            "admin-identity-change: %s on admin identity %s by operator %s",
            action, identity.pk, actor.pk,
        )

    # Actions

    def _add(self, email, actor, username):
        user = self._staff_user(username, "--user")
        with transaction.atomic():
            if AdminIdentity.objects.filter(email=email).exists():
                raise CommandError("That admin identity already exists.")
            if AdminIdentity.objects.filter(user=user, is_active=True).exists():
                raise CommandError("That user already has an active admin identity.")
            identity = AdminIdentity.objects.create(user=user, email=email)
            self._record(actor, "admin_identity_add", identity)
        self.stdout.write("Added. Run `enrol` next.")

    def _enrol(self, email, actor, _user):
        if not crypto.key_ready():
            raise CommandError("ADMIN_SECRETS_KEY is not set or is not a valid key. Refusing to enrol.")
        if not self._interactive():
            raise CommandError(
                "Enrolment shows a setup key and recovery codes once and needs an interactive terminal."
            )
        secret = totp.new_secret()
        codes = two_step.new_recovery_codes()
        with transaction.atomic():
            identity = self._locked_identity(email)
            if not identity.is_active:
                raise CommandError("That identity is disabled.")
            if identity.enrolled_at is not None:
                raise CommandError("That identity is already enrolled. Use `reset`, then `enrol`.")
            two_step.store_enrolment(identity, secret, codes, timezone.now())
            self._record(actor, "admin_identity_enrol", identity)
        grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
        self.stdout.write("Add this account to your authenticator app (time-based, 6 digits).")
        self.stdout.write(f"\nSetup key: {grouped}")
        self.stdout.write(f"Setup link: {totp.setup_link(secret, email)}")
        self.stdout.write("\nRecovery codes (each works once; shown only now):")
        for code in codes:
            self.stdout.write(f"  {code[:5]}-{code[5:]}")
        self.stdout.write(
            "\nStore the recovery codes away from your phone now. They are not shown again."
        )

    def _reset(self, email, actor, _user):
        with transaction.atomic():
            identity = self._locked_identity(email)
            two_step.clear_enrolment(identity, timezone.now())
            self._record(actor, "admin_identity_reset", identity)
        self.stdout.write("Reset. Sessions ended. Run `enrol` again.")

    def _disable(self, email, actor, _user):
        with transaction.atomic():
            identity = self._locked_identity(email)
            if not identity.is_active:
                self.stdout.write("Already disabled.")
                return
            identity.is_active = False
            two_step.bump_credentials(identity, timezone.now())
            identity.save()
            self._record(actor, "admin_identity_disable", identity)
        self.stdout.write("Disabled. Sessions ended.")

    def _list(self):
        now = timezone.now()
        identities = list(AdminIdentity.objects.select_related("user").order_by("email"))
        for identity in identities:
            self.stdout.write(
                f"{identity.email}\tuser={identity.user.username}\tactive={identity.is_active}"
                f"\tenrolled={identity.enrolled_at is not None}"
                f"\tlocked={two_step.is_locked(identity, now)}"
            )
        if not identities:
            self.stdout.write("No admin identities.")
        covered = {i.user_id for i in identities if i.is_active and i.enrolled_at is not None}
        uncovered = (
            get_user_model().objects.filter(is_active=True, is_staff=True)
            .exclude(pk__in=covered).order_by("username")
        )
        if uncovered:
            self.stdout.write(
                "\nStaff users with no enrolled identity (they cannot sign in to hq):"
            )
            for user in uncovered:
                self.stdout.write(f"  {user.username}")
