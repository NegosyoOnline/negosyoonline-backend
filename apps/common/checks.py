"""Deploy checks for settings that weaken protection when they are missing."""
from django.conf import settings
from django.core.checks import Error, register


@register(deploy=True)
def throttles_need_a_shared_cache(app_configs, **kwargs):
    """Without REDIS_URL each Gunicorn worker counts throttles alone, multiplying every limit."""
    if settings.DEBUG or settings.REDIS_URL:
        return []
    return [
        Error(
            "throttles need a shared cache: set REDIS_URL",
            hint="Without REDIS_URL the code and OTP limits are counted per worker.",
            id="common.E001",
        )
    ]
