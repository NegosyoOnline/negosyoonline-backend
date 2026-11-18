"""The Xendit switch needs a valid credential encryption key."""
import os

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


@register(deploy=True)
def check_xendit_fake_is_local(app_configs, **kwargs):
    """The fake Xendit is for a local run only: the variable must not be set without DEBUG, and
    never on Railway (settings.py then ignores it, but it should not be there)."""
    if os.environ.get("XENDIT_FAKE", "").strip() != "1":
        return []
    if settings.DEBUG and not settings.RAILWAY_ENVIRONMENT_NAME:
        return []
    return [Error(
        "XENDIT_FAKE is on outside a local DEBUG run.",
        hint="Remove XENDIT_FAKE from this environment; the fake Xendit is for local previews only.",
        id="payments.E002",
    )]
