from django.apps import AppConfig


class StatementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.statements"

    def ready(self):
        from apps.statements import checks  # noqa: F401  registers the store check
