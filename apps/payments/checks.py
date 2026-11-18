"""The Xendit switch needs a valid credential encryption key."""
from django.conf import settings
from django.core.checks import Error, register

from apps.payments import crypto


@register(deploy=True)
def check_xendit_dependencies(app_configs, **kwargs):
    if not settings.XENDIT_ENABLED or crypto.key_ready():
        return []
    return [Error(
        "XENDIT_ENABLED is on but the credential encryption key is missing or invalid.",
        hint="Set PAYMENT_CREDENTIALS_ENCRYPTION_KEY to a Fernet key on the api and worker services.",
        id="payments.E001",
    )]
