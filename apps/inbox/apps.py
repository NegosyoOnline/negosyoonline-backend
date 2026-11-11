from django.apps import AppConfig


class InboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inbox"

    def ready(self):
        from apps.inbox import retention  # noqa: F401  registers the retention rule
