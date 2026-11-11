"""Who may call the storefront surface: the storefronts server, and nobody else.

The surface is on only while STOREFRONT_ENABLED is on and STOREFRONT_SERVER_SECRET holds at
least 32 characters. Off, every path answers the same 404 before any credential is looked at.
On, a call must carry `Authorization: Bearer <secret>`. Missing, malformed and wrong all get
one and the same 401. The secret is never logged and never put in an answer.
"""
import hashlib
import hmac
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.sessions import client_ip
from apps.common.errors import error_body

SECRET_MIN_LENGTH = 32
FAILED_TRIES = 10  # bad credentials from one IP address ...
FAILED_TRIES_WINDOW = 15 * 60  # ... in this many seconds


def _error(status, code, message):
    return JsonResponse(error_body(code, message), status=status)


def secret_is_set():
    """A shorter value counts as not set: a half-configured service stays closed."""
    return len(settings.STOREFRONT_SERVER_SECRET) >= SECRET_MIN_LENGTH


def surface_on():
    return settings.STOREFRONT_ENABLED and secret_is_set()


def _digest(value):
    return hashlib.sha256(value.encode()).digest()


def _has_secret(request):
    scheme, _, given = request.headers.get("Authorization", "").partition(" ")
    if scheme != "Bearer" or not given:
        return False
    return hmac.compare_digest(_digest(given), _digest(settings.STOREFRONT_SERVER_SECRET))


def _tries_key(request):
    return f"storefront:failed-tries:{client_ip(request) or 'unknown'}"


def refusal(request):
    """The answer for a call that may not pass, or None for the storefronts server."""
    if not surface_on():
        return _error(404, "not_found", "No such endpoint.")
    if _has_secret(request):
        return None
    # Only a bad or missing credential is counted and limited. A good one is always served:
    # every visitor's page view arrives from the storefronts server's few addresses.
    key = _tries_key(request)
    if (cache.get(key) or 0) >= FAILED_TRIES:
        return _error(429, "rate_limited", "Too many attempts. Please wait and try again.")
    cache.add(key, 0, FAILED_TRIES_WINDOW)
    try:
        cache.incr(key)
    except ValueError:  # the entry expired between the add and the incr
        cache.set(key, 1, FAILED_TRIES_WINDOW)
    return _error(401, "unauthenticated", "Unknown caller.")


def server_only(view):
    """Wraps a view of the surface: the switch, then the credential, then the view. Nothing
    under the surface may be stored by a cache."""

    @csrf_exempt
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = refusal(request) or view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    return wrapped
