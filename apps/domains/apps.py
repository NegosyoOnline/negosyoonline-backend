from django.apps import AppConfig


class DomainsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains"
    verbose_name = "Custom domains"

    def ready(self):
        from apps.domains import checks, context, tasks  # noqa: F401  register the checks and the tasks

        context.connect()
