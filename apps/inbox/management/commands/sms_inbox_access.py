"""Give or take away a staff user's access to the SMS inbox. This is the only way to do so:
the access is a row of its own, not a Django permission, and a superuser has none without it.

    manage.py sms_inbox_access grant --user USERNAME [--send] --actor USERNAME
    manage.py sms_inbox_access revoke --user USERNAME --actor USERNAME
    manage.py sms_inbox_access list
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.inbox.models import InboxAccess
from apps.inbox.switch import SMS, inbox_on
from apps.operators import audit

TARGET = "inbox_access"
LEVELS = {False: "read", True: "send"}
WORDS = {False: "read", True: "read and send"}


class Command(BaseCommand):
    help = "Grant, revoke or list access to the SMS inbox."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("grant", "revoke", "list"))
        parser.add_argument("--user", help="the staff user the access is for")
        parser.add_argument("--send", action="store_true", help="grant: may also reply and compose")
        parser.add_argument("--actor", help="the staff user running this command, for the audit record")

    def handle(self, *args, action, user, send, actor, **options):
        if not inbox_on():
            raise CommandError("SMS_INBOX_ENABLED is off.")
        if action == "list":
            return self._list()
        actor = self._staff_user(actor, "--actor")
        user = self._staff_user(user, "--user")
        with transaction.atomic():
            getattr(self, f"_{action}")(user, actor, send)

    def _staff_user(self, username, option):
        found = get_user_model().objects.filter(
            username=username or "", is_active=True, is_staff=True
        ).first()
        if found is None:
            raise CommandError(f"{option} must be an active staff user.")
        return found

    def _note(self, actor, user, change, level):
        audit.record(
            actor, "sms_inbox_access", target_type=TARGET, target_id=user.pk,
            detail={"platform": SMS, "change": change, "level": level},
        )

    def _grant(self, user, actor, send):
        row = InboxAccess.objects.select_for_update().filter(operator=user, platform=SMS).first()
        if row is not None and row.can_send == send:
            self.stdout.write(f"Already granted: {WORDS[send]}.")
            return
        if row is None:
            InboxAccess.objects.create(operator=user, platform=SMS, can_send=send, granted_by=actor)
        else:
            row.can_send, row.granted_by = send, actor
            row.save(update_fields=["can_send", "granted_by"])
        self._note(actor, user, "grant", LEVELS[send])
        self.stdout.write(f"Granted: {WORDS[send]}.")

    def _revoke(self, user, actor, _send):
        deleted, _ = InboxAccess.objects.filter(operator=user, platform=SMS).delete()
        if not deleted:
            self.stdout.write("No access to revoke.")
            return
        self._note(actor, user, "revoke", "none")
        self.stdout.write("Revoked.")

    def _list(self):
        rows = list(InboxAccess.objects.select_related("operator", "granted_by").order_by("operator__username"))
        for row in rows:
            self.stdout.write(
                f"{row.operator.username}\t{row.platform}\t{WORDS[row.can_send]}"
                f"\tgranted_by={row.granted_by.username}\t{row.granted_at:%Y-%m-%d}"
            )
        if not rows:
            self.stdout.write("No one has access.")
