from django.apps import AppConfig


class OperatorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.operators"

    def ready(self):
        from apps.operators import checks  # noqa: F401  registers the HQ_HOST check
        from apps.operators import retention  # noqa: F401  registers the retention rules
