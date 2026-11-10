from django.apps import AppConfig


class OperatorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.operators"

    def ready(self):
        from django.contrib.auth.signals import user_logged_out

        from apps.operators import checks  # noqa: F401  registers the HQ_HOST check
        from apps.operators.admin_site import audit_sign_out

        user_logged_out.connect(audit_sign_out, dispatch_uid="operators.audit_sign_out")
