"""A fixed-window counter in the cache. One place for every per-account limit."""
from django.core.cache import cache

from apps.common.errors import ApiError


def hit(scope, key, *, limit, window):
    """Count one attempt. Raises ApiError 429 once `limit` attempts were made in the window."""
    cache_key = f"throttle:{scope}:{key}"
    cache.add(cache_key, 0, window)
    try:
        count = cache.incr(cache_key)
    except ValueError:  # the key expired between add and incr
        cache.set(cache_key, 1, window)
        count = 1
    if count > limit:
        raise ApiError("rate_limited", "Too many attempts. Please wait and try again.", status=429)
