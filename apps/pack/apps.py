from django.apps import AppConfig


class PackConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pack"
    verbose_name = "Santé Membership Pack"

    def ready(self):
        from apps.pack import checks, retention  # noqa: F401  register the check and the rules
        from apps.pack import recovery_item

        recovery_item.register()
