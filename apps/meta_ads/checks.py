"""Meta events need the storefront surface and a valid encryption key."""
from django.conf import settings
from django.core.checks import Error, register

from apps.meta_ads import crypto


@register(deploy=True)
def check_meta_dependencies(app_configs, **kwargs):
    if not settings.META_EVENTS_ENABLED:
        return []
    found = []
    if not crypto.key_ready():
        found.append(Error(
            "META_EVENTS_ENABLED is on but the credential encryption key is missing or invalid.",
            hint="Set PAYMENT_CREDENTIALS_ENCRYPTION_KEY to a Fernet key on the api and worker services.",
            id="meta.E001",
        ))
    if not settings.STOREFRONT_ENABLED:
        found.append(Error(
            "META_EVENTS_ENABLED is on but STOREFRONT_ENABLED is not.",
            hint="The events route and the Pixel ID live on the storefront surface.",
            id="meta.E001",
        ))
    return found
