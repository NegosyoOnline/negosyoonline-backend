"""The storefront surface needs its server secret. With the switch on and no secret of at
least 32 characters, a running service keeps the surface off; `manage.py check --deploy`
says so. It reports the fact, never the value.
"""
from django.conf import settings
from django.core.checks import Warning, register

from apps.storefront import auth


@register(deploy=True)
def check_storefront_secret(app_configs, **kwargs):
    if not settings.STOREFRONT_ENABLED or auth.secret_is_set():
        return []
    return [
        Warning(
            "STOREFRONT_ENABLED is on but STOREFRONT_SERVER_SECRET is not set, or is shorter "
            f"than {auth.SECRET_MIN_LENGTH} characters.",
            hint="The storefront surface stays off until the secret is set.",
            id="storefront.W001",
        )
    ]
