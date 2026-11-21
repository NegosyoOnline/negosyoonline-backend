from django.conf import settings
from django.core.checks import Warning, register

from apps.file_backup import crypto


@register()
def check_db_backup(app_configs, **kwargs):
    if not settings.DB_BACKUP_ENABLED:
        return []
    found = []
    if not settings.PRIVATE_BLOB_READ_WRITE_TOKEN:
        found.append(Warning("DB_BACKUP_ENABLED is on but PRIVATE_BLOB_READ_WRITE_TOKEN is not set.",
                             hint="The copies go to the private store.", id="db_backup.W001"))
    try:
        crypto.load_key(settings.DB_BACKUP_ENCRYPTION_KEY)
    except crypto.CryptoError:
        found.append(Warning(
            "DB_BACKUP_ENABLED is on but DB_BACKUP_ENCRYPTION_KEY is not base64 of 32 bytes.",
            hint="Set it to base64 of 32 random bytes, and keep a copy of it.", id="db_backup.W002",
        ))
    return found
