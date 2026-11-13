from django.apps import AppConfig


class InquiriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inquiries"
    verbose_name = "Customer inquiries"

    def ready(self):
        from apps.inquiries import checks, retention  # noqa: F401  register the check and the rule
