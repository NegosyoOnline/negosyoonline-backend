from django.apps import AppConfig


class MembersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.members"
    verbose_name = "Santé members"

    def ready(self):
        from apps.members import checks, retention  # noqa: F401  register the check and the rule
        from apps.members import sms

        sms.register()
