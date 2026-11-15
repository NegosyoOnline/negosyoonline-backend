"""The inquiries switch needs a valid credential encryption key. Website chat needs the
inquiries and storefront switches."""
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


@register(deploy=True)
def check_webchat_dependencies(app_configs, **kwargs):
    if not settings.INQUIRY_WEBCHAT_ENABLED:
        return []
    missing = [
        name for name in ("INQUIRIES_ENABLED", "STOREFRONT_ENABLED") if not getattr(settings, name)
    ]
    if not missing:
        return []
    return [Error(
        f"INQUIRY_WEBCHAT_ENABLED is on but {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} off.",
        hint="Website chat is reached through the storefront surface and stored by the inquiries app: turn both on, or turn chat off.",
        id="inquiries.E002",
    )]
