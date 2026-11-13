from django.apps import AppConfig


class FileBackupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.file_backup"
    verbose_name = "Private file backup"

    def ready(self):
        from apps.file_backup import checks, tasks  # noqa: F401  register the check and the task
