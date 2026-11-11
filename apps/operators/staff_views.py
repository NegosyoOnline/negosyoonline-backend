"""The staff routes: what the staff app on Vercel calls on the hq host, under /staff/v1/.

They are in no URL list. HqHostMiddleware hands them here, and only while HQ_STAFF_ORIGIN is
usable, so with it unset the hq host answers as if this file did not exist. An answer from
here passes back through SecurityMiddleware only: no middleware listed after HqHostMiddleware
runs for it, which is why dispatch() sets X-Frame-Options itself. Django's form token
cannot be held by a page on another host; the one allowed Origin, required on every call,
takes its place. The codes are checked by hq_auth and two_step, as on the Django pages, and
the cookies are the same two cookies.
"""
import json
import logging
import secrets

from django.core import signing
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.cache import patch_vary_headers

from apps.accounts.sessions import client_ip
from apps.common.errors import error_body
from apps.operators import audit, background, hq_auth, hq_views, two_step

logger = logging.getLogger(__name__)

PREFIX = "/staff/v1/"
MAX_BODY = 2048
ALLOW_HEADERS = "content-type"
PREFLIGHT_MAX_AGE = "600"


def _error(status, code, message):
    return JsonResponse(error_body(code, message), status=status)


def _no_content():
    return HttpResponse(status=204)


def _gate(request):
    """(session, None) for a session that passed both steps, else (None, the 401 to send)."""
    session = hq_views.load_session(request)
    if session is None:
        return None, _error(401, "unauthenticated", "Sign in to continue.")
    if not hq_views.is_verified(session):
        return None, _error(
            401, "second_step_required", "Enter your authenticator code to continue."
        )
    return session, None


def me(request):
    session, refusal = _gate(request)
    if refusal:
        return refusal
    return JsonResponse(
        {"email": session.identity.email, "expires_at": session.expires_at.isoformat()}
    )


def console(request):
    session, refusal = _gate(request)
    if refusal:
        return refusal
    events = [
        {
            "action": entry.action,
            "surface": entry.detail.get("surface"),
            "step": entry.detail.get("step"),
            "at": entry.at.isoformat(),
        }
        for entry in audit.recent_sign_in_events(session.identity)
    ]
    return JsonResponse({
        "email": session.identity.email,
        "verified_at": session.verified_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "events": events,
    })


ROUTES = {
    "me/": ("GET", me),
    "console/": ("GET", console),
}


def _answer(request, allowed):
    # The origin is looked at before the path: another origin learns nothing about the routes.
    if not allowed:
        return _error(403, "origin_not_allowed", "This origin may not call the staff routes.")
    route = ROUTES.get(request.path[len(PREFIX):])
    if route is None:
        return _error(404, "not_found", "Not found.")
    method, view = route
    if request.method == "OPTIONS":
        response = _no_content()
        response["Access-Control-Allow-Methods"] = f"{method}, OPTIONS"
        response["Access-Control-Allow-Headers"] = ALLOW_HEADERS
        response["Access-Control-Max-Age"] = PREFLIGHT_MAX_AGE
        return response
    if request.method != method:
        return _error(405, "method_not_allowed", "Method not allowed.")
    return view(request)


def dispatch(request, origin):
    """Answer one call under PREFIX. `origin` is checks.active_staff_origin(), known to be set."""
    allowed = request.headers.get("Origin") == origin
    try:
        response = _answer(request, allowed)
    except Exception:
        logger.exception("staff route failed")
        response = _error(500, "server_error", "Something went wrong.")
    if allowed:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
    patch_vary_headers(response, ["Origin"])
    # XFrameOptionsMiddleware comes after HqHostMiddleware and never sees a staff answer.
    response["X-Frame-Options"] = "DENY"
    return response
