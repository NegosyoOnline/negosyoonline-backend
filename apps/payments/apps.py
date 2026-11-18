from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payments"
    verbose_name = "Site payment methods"

    def ready(self):
        from apps.payments import checks  # noqa: F401  register the deploy check
