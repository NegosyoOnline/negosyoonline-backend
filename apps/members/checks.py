"""Sign-in codes live in the database; the request throttles and the flood monitor live in the
Django cache. Without REDIS_URL that cache is per process, so the limits are per worker."""
from django.conf import settings
from django.core.checks import Warning, register


@register(deploy=True)
def check_members_cache(app_configs, **kwargs):
    if not settings.MEMBERS_ENABLED or settings.REDIS_URL:
        return []
    return [
        Warning(
            "MEMBERS_ENABLED is on but REDIS_URL is not set.",
            hint="Member sign-in throttles would be counted in each process's own memory.",
            id="members.W001",
        )
    ]
