"""Dispose of one account on the person's request.

    manage.py dispose_account ACCOUNT --actor USERNAME --request-ref REF --dry-run
    manage.py dispose_account ACCOUNT --actor USERNAME --request-ref REF

ACCOUNT is the account's public id or its email. The dry run counts what would be blanked and
deleted and changes nothing. A real run needs RETENTION_ENABLED=1. The output is the public id
and counts only. See docs/runbooks/retention.md.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts import retention
from apps.accounts.management.commands import _shared


class Command(BaseCommand):
    help = "Dispose of one account on request, or count what that would take with --dry-run."

    def add_arguments(self, parser):
        _shared.add_arguments(parser)
        parser.add_argument("--dry-run", action="store_true", help="count only; change nothing")

    def handle(self, *args, account, actor, request_ref, dry_run, **options):
        actor = _shared.staff_user(actor)
        account = _shared.account_for(account)
        public_id = account.public_id
        try:
            counts = retention.dispose_account(
                account, now=timezone.now(), rule=retention.REQUEST, actor=actor,
                request_ref=request_ref, dry_run=dry_run,
            )
        except retention.SwitchedOff:
            raise CommandError(
                "RETENTION_ENABLED is off in this environment, so nothing was changed. "
                "Only --dry-run works until Coach Ed approves switching it on."
            ) from None
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        if counts is None:
            self.stdout.write(f"Account {public_id} is already disposed of. Nothing was changed.")
            return
        if dry_run:
            self.stdout.write(f"Dry run: nothing was changed. Account {public_id} would lose:")
        else:
            self.stdout.write(f"Disposed of account {public_id}.")
        for name, count in counts.items():
            self.stdout.write(f"{name}: {count}")
