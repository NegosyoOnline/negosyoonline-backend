from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shop"
    verbose_name = "Santé shop"

    def ready(self):
        from apps.shop import checks, retention  # noqa: F401  register the check and the rules
