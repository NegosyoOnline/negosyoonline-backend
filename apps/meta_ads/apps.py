from django.apps import AppConfig


class MetaAdsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.meta_ads"
    verbose_name = "Meta Pixel and Conversions API"

    def ready(self):
        from apps.meta_ads import checks, retention  # noqa: F401  register the check and the rule
