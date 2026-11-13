"""Builds the real clients from settings. Called only when the switch is on or a command runs,
so a switched-off deployment never builds, imports or calls any of them."""
from django.conf import settings

from apps.file_backup import crypto
from apps.file_backup.destination import S3Destination
from apps.file_backup.service import Config
from apps.file_backup.source import BlobSource


def build_source():
    return BlobSource(settings.PRIVATE_BLOB_READ_WRITE_TOKEN)


def build_destination():
    return S3Destination(
        bucket=settings.FILE_BACKUP_BUCKET,
        endpoint=settings.FILE_BACKUP_ENDPOINT,
        region=settings.FILE_BACKUP_REGION,
        access_key_id=settings.FILE_BACKUP_ACCESS_KEY_ID,
        secret_access_key=settings.FILE_BACKUP_SECRET_ACCESS_KEY,
    )


def build_config(**overrides):
    return Config(
        key=crypto.load_key(settings.FILE_BACKUP_ENCRYPTION_KEY),
        keep_days=settings.FILE_BACKUP_DELETED_KEEP_DAYS,
        **overrides,
    )
