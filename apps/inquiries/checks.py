"""The inquiries switch needs a valid credential encryption key."""
from django.conf import settings
from django.core.checks import Error, register

from apps.inquiries import crypto


@register(deploy=True)
def check_inquiries_dependencies(app_configs, **kwargs):
    if not settings.INQUIRIES_ENABLED or crypto.key_ready():
        return []
    return [Error(
        "INQUIRIES_ENABLED is on but the credential encryption key is missing or invalid.",
        hint="Set PAYMENT_CREDENTIALS_ENCRYPTION_KEY to a Fernet key on the api service.",
        id="inquiries.E001",
    )]
