"""The backup needs the store token, the bucket and the encryption key once it is switched on."""
from django.conf import settings
from django.core.checks import Warning, register

from apps.file_backup import crypto


@register()
def check_file_backup(app_configs, **kwargs):
    if not settings.FILE_BACKUP_ENABLED:
        return []
    found = []
    needs = (
        ("PRIVATE_BLOB_READ_WRITE_TOKEN", "file_backup.W001", "The backup reads the private store."),
        ("FILE_BACKUP_BUCKET", "file_backup.W002", "The bucket to copy to."),
        ("FILE_BACKUP_ENDPOINT", "file_backup.W002", "The bucket's S3 endpoint."),
        ("FILE_BACKUP_ACCESS_KEY_ID", "file_backup.W002", "The bucket's access key id."),
        ("FILE_BACKUP_SECRET_ACCESS_KEY", "file_backup.W002", "The bucket's secret access key."),
    )
    for name, code, hint in needs:
        if not getattr(settings, name):
            found.append(Warning(f"FILE_BACKUP_ENABLED is on but {name} is not set.", hint=hint, id=code))
    try:
        crypto.load_key(settings.FILE_BACKUP_ENCRYPTION_KEY)
    except crypto.CryptoError:
        found.append(Warning(
            "FILE_BACKUP_ENABLED is on but FILE_BACKUP_ENCRYPTION_KEY is not base64 of 32 bytes.",
            hint="Set it to base64 of 32 random bytes, and keep a copy of it.", id="file_backup.W003",
        ))
    return found
