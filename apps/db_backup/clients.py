"""Real clients from settings, built only when the switch is on or a command runs."""
from django.conf import settings

from apps.file_backup import crypto
from apps.file_backup.source import BlobSource


def build_store():
    return BlobSource(settings.PRIVATE_BLOB_READ_WRITE_TOKEN)


def build_key():
    return crypto.load_key(settings.DB_BACKUP_ENCRYPTION_KEY)
