"""What ties a provider sign-in flow to one browser: a signed, short-lived, HttpOnly cookie."""
import hmac

from django.conf import settings
from django.core import signing
from django.http import HttpResponseRedirect


def _salt(name):
    # A cookie signed for one flow is never accepted as another.
    return f"accounts.flow.{name}"


def set_flow_cookie(response, name, path, payload, seconds):
    response.set_cookie(
        name,
        signing.dumps(payload, salt=_salt(name), compress=True),
        max_age=seconds,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path=path,
    )


def read_flow_cookie(request, name, seconds):
    """The payload, or None when the cookie is missing, altered or older than `seconds`."""
    try:
        return signing.loads(request.COOKIES.get(name, ""), salt=_salt(name), max_age=seconds)
    except signing.BadSignature:
        return None


def clear_flow_cookie(response, name, path):
    response.delete_cookie(name, path=path, samesite="Lax")


def same_state(expected, given):
    return hmac.compare_digest(str(expected).encode(), str(given).encode())


def finish_redirect(error=None):
    """Where a provider flow ends: the dashboard, or the site's sign-in popup with an error."""
    if error:
        return HttpResponseRedirect(f"{settings.SITE_URL}/?signin=1&error={error}")
    return HttpResponseRedirect(f"{settings.DASHBOARD_URL}/")
