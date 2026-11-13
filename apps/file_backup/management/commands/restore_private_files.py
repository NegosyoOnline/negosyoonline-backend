"""Restore backed-up private files into the live private store. Dry run unless --apply.

    manage.py restore_private_files --list [--prefix P]        counts and bytes per folder
    manage.py restore_private_files --missing [--apply]        every backed-up file the store lacks
    manage.py restore_private_files --prefix P [--apply]       the files under a prefix
    manage.py restore_private_files --key K [--key K2] [--apply]

It never overwrites a file the live store has. Files marked deleted at the source are skipped
unless --include-deleted (then run the re-erasure step of the backups runbook). Output is counts
only, never a pathname. See docs/runbooks/backups-and-restore.md, section 7.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.file_backup import clients, crypto, restore
from apps.file_backup.destination import DestinationError
from apps.file_backup.source import SourceError


class Command(BaseCommand):
    help = "Restore private files from the backup, or list what it holds."

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", dest="list_only")
        parser.add_argument("--missing", action="store_true")
        parser.add_argument("--prefix", default="")
        parser.add_argument("--key", action="append", default=[])
        parser.add_argument("--include-deleted", action="store_true")
        parser.add_argument("--apply", action="store_true", help="write for real (default: count only)")

    def handle(self, *args, list_only, missing, prefix, key, include_deleted, apply, **options):
        try:
            cfg = clients.build_config()
            dest = clients.build_destination()
            if list_only:
                for folder, (count, nbytes, deleted) in sorted(restore.listing(dest, cfg, prefix).items()):
                    self.stdout.write(f"{folder}: {count} objects, {nbytes} bytes, {deleted} marked deleted")
                return
            if not (missing or prefix or key):
                raise CommandError("Choose --list, --missing, --prefix or --key.")
            counts = restore.restore(
                clients.build_source(), dest, cfg, prefix=prefix, keys=key,
                include_deleted=include_deleted, apply=apply,
            )
        except (SourceError, DestinationError, crypto.CryptoError) as error:
            raise CommandError(str(error)) from None
        self.stdout.write(("Restore: " if apply else "Dry run, nothing written: ") + str(counts))
        if counts["mismatch"] or counts["failed"]:
            raise CommandError("Some files could not be restored.")
