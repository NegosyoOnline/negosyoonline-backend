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


def _body(request):
    """The JSON object sent, or {} when there is none or it is not one."""
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_BODY:
            return {}
        data = json.loads(request.body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(request, key):
    value = _body(request).get(key)
    return value if isinstance(value, str) else ""


def _already_signed_in(request):
    """The 409 for a session that passed both steps, else None."""
    if hq_views.is_verified(hq_views.load_session(request)):
        return _error(409, "already_signed_in", "You are already signed in.")
    return None


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


def sign_in(request):
    refusal = _already_signed_in(request)
    if refusal:
        return refusal
    email = hq_auth.normalize_email(_text(request, "email"))
    if email is None:
        return _error(400, "invalid_email", "Enter a valid email address.")
    # From here every address gets the same answer. The lookup and the email happen later.
    nonce = secrets.token_urlsafe(16)
    if hq_auth.within_limits(email, client_ip(request)):
        try:
            background.run_later(hq_auth.issue_email_code, email, nonce)
        except Exception:
            logger.exception("hq sign-in code could not be handed over")
    response = _no_content()
    value = signing.dumps({"email": email, "nonce": nonce}, salt=hq_views.PRE_SALT)
    hq_views._set_cookie(response, hq_views._cookie_names()[1], value, hq_views.PRE_SECONDS)
    return response


def code(request):
    refusal = _already_signed_in(request)
    if refusal:
        return refusal
    pre = hq_views._pre(request)
    typed = "".join(_text(request, "code").split())
    token = None
    if pre is not None and len(typed) == 6 and typed.isdigit():
        token = hq_auth.check_email_code(pre["email"], pre["nonce"], typed)
    if token is None:
        # One answer for a wrong code, a used one, a stale one and a browser that never asked.
        return _error(400, "invalid_code", "That code did not work.")
    session_name, pre_name = hq_views._cookie_names()
    response = hq_views._drop_cookies(_no_content(), pre_name)
    hq_views._set_cookie(response, session_name, token, hq_auth.SESSION_HOURS * 3600)
    return response


def verify(request):
    session = hq_views.load_session(request)
    if session is None:
        return _error(401, "unauthenticated", "Sign in to continue.")
    if hq_views.is_verified(session):
        return _error(409, "already_signed_in", "You are already signed in.")
    result = two_step.verify_second_step(
        session.identity_id, _text(request, "code"), surface=two_step.SURFACE_HQ
    )
    if result.locked:
        paused = _error(
            403, "sign_in_paused", "Too many wrong codes. Sign-in is paused for 15 minutes."
        )
        return hq_views._drop_cookies(paused, *hq_views._cookie_names())
    if not result.ok:
        return _error(400, "invalid_code", "That code did not work.")
    token = hq_auth.mark_verified(session, result.credential_version)
    response = _no_content()
    remaining = int((session.expires_at - timezone.now()).total_seconds())
    hq_views._set_cookie(response, hq_views._cookie_names()[0], token, max(remaining, 0))
    return response


def sign_out(request):
    session = hq_views.load_session(request)
    if session is not None:
        hq_auth.sign_out(session)
    return hq_views._drop_cookies(_no_content(), *hq_views._cookie_names())


ROUTES = {
    "sign-in/": ("POST", sign_in),
    "sign-in/code/": ("POST", code),
    "sign-in/verify/": ("POST", verify),
    "sign-out/": ("POST", sign_out),
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
