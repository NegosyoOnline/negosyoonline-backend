"""List the database backups, or restore one named copy into an explicit target database.

    manage.py restore_database --list
    manage.py restore_database --name db-backups/<time>.dump.enc --target-url postgresql://... [--summary]

With --summary, after the restore it counts the tables and the rows of each in the target and
writes that count summary (numbers and table names, no row content) to the private Blob store as
`ops/restore-summary-<time>.json`. This is the restore job of the Bunny data app
(deploy/bunny/data/restore-job.sh).

The target must be given every time and must not be the database this deployment runs on; it
must already exist and be empty. The key is DB_BACKUP_ENCRYPTION_KEY. Output never shows the
target URL, the copy's content or the tools' output.
See docs/runbooks/backups-and-restore.md, section 8.
"""
import psycopg
from django.core.management.base import BaseCommand, CommandError

from apps.db_backup import clients, dump, service
from apps.db_backup import summary as summary_module
from apps.file_backup import crypto
from apps.file_backup.source import SourceError


class Command(BaseCommand):
    help = "List database backups, or restore a named copy into a target database URL."

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true")
        parser.add_argument("--name", help="the copy's pathname, as --list prints it")
        parser.add_argument("--target-url", help="PostgreSQL URL of the database to restore into")
        parser.add_argument("--summary", action="store_true",
                            help="then write the table and row counts to ops/ in the private Blob store")

    def handle(self, *args, list, name, target_url, summary=False, **options):
        try:
            store = clients.build_store()
            if list:
                for pathname, moment in service.list_copies(store):
                    self.stdout.write(f"{pathname}  {moment:%Y-%m-%d %H:%M} UTC")
                return
            if not name or not target_url:
                raise CommandError("Give --list, or both --name and --target-url.")
            key = clients.build_key()
            target = dump.connection_from_url(target_url)
            try:
                running = dump.connection_from_settings()
            except dump.DumpError:
                running = None
            if running and dump.same_database(target, running):
                raise CommandError("Refusing: the target is the database this deployment runs on.")
            plain = service.fetch_plain(store, key, name)
            dump.restore_database(plain, target)
            counted = None
            if summary:
                counts = summary_module.table_counts(target)
                summary_module.write(store, counts, source=name)
                counted = (len(counts), sum(counts.values()))
        except (crypto.CryptoError, SourceError, dump.DumpError, service.BackupError) as error:
            raise CommandError(f"Not done: {error}") from None
        except psycopg.Error as error:
            raise CommandError(f"Not done: the count summary could not be made ({type(error).__name__}).") from None
        self.stdout.write("Restored into the target database.")
        if counted:
            self.stdout.write(f"Count summary written to ops/: {counted[0]} tables, {counted[1]} rows.")
