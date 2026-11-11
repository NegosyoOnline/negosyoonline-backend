from django.apps import AppConfig


class StorefrontConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.storefront"

    def ready(self):
        from apps.storefront import checks  # noqa: F401  registers the surface's check
