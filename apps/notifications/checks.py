"""The SMS gateway needs Redis on a deployment: a text waits in the cache, and a cache kept per
process cannot be read by the process the phone reaches.

`manage.py check` reports it as an error. A running service treats the gateway as off.
"""
from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.notifications import sms, store_email


@register()
def check_sms_gateway_cache(app_configs, **kwargs):
    if not settings.SMS_GATEWAY_ENABLED or not sms.redis_missing():
        return []
    return [
        Error(
            "SMS_GATEWAY_ENABLED is on but REDIS_URL is not set.",
            hint="The gateway stays off until REDIS_URL is set.",
            id="notifications.E001",
        )
    ]


@register()
def check_store_senders(app_configs, **kwargs):
    """A malformed STORE_SENDERS pair is skipped when mail is sent; say so at startup."""
    _, problems = store_email.parse_senders(settings.STORE_SENDERS)
    return [
        Warning(
            f"STORE_SENDERS has a pair that is not key=address and is ignored: {key!r}.",
            id="notifications.W001",
        )
        for key in problems
    ]
