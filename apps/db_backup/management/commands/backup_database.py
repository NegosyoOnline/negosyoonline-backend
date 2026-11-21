"""Take one encrypted database backup now (for example right before a cutover).

    manage.py backup_database

Runs with DB_BACKUP_ENABLED off too. Needs DB_BACKUP_ENCRYPTION_KEY and the private store token,
and refuses without them before it dumps anything. Output is counts only.
See docs/runbooks/backups-and-restore.md, section 8.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.db_backup import clients, dump, service, tasks
from apps.file_backup import crypto
from apps.file_backup.source import SourceError


class Command(BaseCommand):
    help = "Dump the default database, encrypt it and upload it to the private Blob store."

    def handle(self, *args, **options):
        try:
            clients.build_key()
            clients.build_store()
        except (crypto.CryptoError, SourceError) as error:
            raise CommandError(f"Refusing to run: {error}") from None
        try:
            result = tasks.execute()
        except (dump.DumpError, service.BackupError, SourceError, crypto.CryptoError) as error:
            raise CommandError(f"The backup failed: {error}") from None
        self.stdout.write(f"Backup stored: {result['bytes']} bytes, {result['deleted']} old copies removed.")
