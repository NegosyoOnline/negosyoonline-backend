"""Sign-in codes and their limits live in the Django cache. Without REDIS_URL that cache is
per process, so a code requested from one worker is unknown to the next."""
from django.conf import settings
from django.core.checks import Warning, register


@register(deploy=True)
def check_members_cache(app_configs, **kwargs):
    if not settings.MEMBERS_ENABLED or settings.REDIS_URL:
        return []
    return [
        Warning(
            "MEMBERS_ENABLED is on but REDIS_URL is not set.",
            hint="Member sign-in codes and limits would live in each process's own memory.",
            id="members.W001",
        )
    ]
