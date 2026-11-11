from django.apps import AppConfig


class LedgerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ledger"
    label = "ledger"

    def ready(self):
        from apps.ledger import checks  # noqa: F401  registers the startup checks
