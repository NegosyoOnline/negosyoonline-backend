"""Export one account and its sites on the person's request.

    manage.py export_account ACCOUNT --actor USERNAME --request-ref REF --out-dir DIR
    manage.py export_account ACCOUNT --actor USERNAME --request-ref REF --stdout

ACCOUNT is the account's public id or its email. It changes nothing except one audit entry,
and it does not need RETENTION_ENABLED. The file holds personal data: it is readable by its
owner only and never replaces an existing file. See docs/runbooks/retention.md.
"""
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts import retention
from apps.accounts.management.commands import _shared


class Command(BaseCommand):
    help = "Export one account and its sites as JSON, to a file or to the terminal."

    def add_arguments(self, parser):
        _shared.add_arguments(parser)
        where = parser.add_mutually_exclusive_group(required=True)
        where.add_argument("--out-dir", metavar="DIR", help="write one JSON file in this folder")
        # Not dest="stdout": that name is the command's own output stream.
        where.add_argument(
            "--stdout", dest="to_terminal", action="store_true", help="print the JSON instead"
        )

    def handle(self, *args, account, actor, request_ref, out_dir, to_terminal, **options):
        if bool(out_dir) == bool(to_terminal):  # call_command skips the parser's own check
            raise CommandError("Give exactly one of --out-dir and --stdout.")
        actor = _shared.staff_user(actor)
        account = _shared.account_for(account)
        if out_dir and not Path(out_dir).is_dir():
            raise CommandError(f"{out_dir} is not a folder.")
        try:
            with transaction.atomic():  # no audit entry unless the export was handed over
                data = retention.export_account_data(
                    account, actor=actor, request_ref=request_ref
                )
                text = json.dumps(data, indent=2, ensure_ascii=False)
                if to_terminal:
                    self.stdout.write(text)
                else:
                    path = self._write(Path(out_dir), account.public_id, text)
        except retention.AlreadyDisposed:
            raise CommandError("That account is disposed of. There is nothing to export.") from None
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        if to_terminal:
            self.stderr.write(f"Exported account {account.public_id}.")
        else:
            self.stdout.write(f"Exported account {account.public_id} to {path}")

    def _write(self, folder, public_id, text):
        path = folder / f"account-export-{public_id}-{timezone.now():%Y%m%dT%H%M%SZ}.json"
        try:
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise CommandError(f"{path} already exists. Nothing was written.") from None
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text + "\n")
        return path
