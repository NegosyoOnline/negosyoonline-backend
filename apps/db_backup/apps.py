from django.apps import AppConfig


class DbBackupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.db_backup"
    verbose_name = "Database backup"

    def ready(self):
        from apps.db_backup import checks, tasks  # noqa: F401  register the check and the task
