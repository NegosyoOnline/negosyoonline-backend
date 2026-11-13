"""The hq pages: sign-in by emailed code, the second step, the signed-in page and sign-out."""
import logging
import secrets

from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.sessions import client_ip
from apps.operators import audit, background, hq_auth, two_step
from apps.operators.models import AdminSession
from apps.operators.pages import SeeOther, page

logger = logging.getLogger(__name__)

PRE_SALT = "hq-pre"
PRE_SECONDS = 600
SIGN_IN, CODE, VERIFY, HOME = "/sign-in/", "/code/", "/verify/", "/"

EVENT_LABELS = {
    "admin_sign_in_ok": "Signed in",
    "admin_sign_in_failed": "Wrong code",
    "admin_locked": "Sign-in paused",
    "admin_sign_out": "Signed out",
}
SURFACE_LABELS = {two_step.SURFACE_HQ: "Admin console", two_step.SURFACE_DJANGO_ADMIN: "Operator admin"}
STEP_LABELS = {
    two_step.STEP_EMAIL: "email code",
    two_step.STEP_TOTP: "authenticator code",
    two_step.STEP_RECOVERY: "recovery code",
}


def _cookie_names():
    """(session, pre-sign-in). The __Host- prefix needs https, which local development lacks."""
    return ("no_hq", "no_hq_pre") if settings.DEBUG else ("__Host-no_hq", "__Host-no_hq_pre")


def _set_cookie(response, name, value, max_age):
    response.set_cookie(
        name, value, max_age=max_age, path="/", secure=not settings.DEBUG, httponly=True,
        samesite="Strict",
    )


def _drop_cookies(response, *names):
    for name in names:
        response.delete_cookie(name, path="/", samesite="Strict")
    return response


def load_session(request):
    return hq_auth.load_session(request.COOKIES.get(_cookie_names()[0]))


def _session(request):
    """The session HqUserMiddleware read for this request."""
    if hasattr(request, "hq_session"):
        return request.hq_session
    return load_session(request)


def is_verified(session):
    return session is not None and session.stage == AdminSession.Stage.VERIFIED



def _pre(request):
    """The {email, nonce, typed} this browser asked a code for in the last 10 minutes, or None."""
    try:
        return signing.loads(
            request.COOKIES.get(_cookie_names()[1], ""), salt=PRE_SALT, max_age=PRE_SECONDS
        )
    except signing.BadSignature:
        return None


@require_http_methods(["GET", "POST"])
def sign_in(request):
    if is_verified(_session(request)):
        return SeeOther(HOME)
    if request.method == "GET":
        return page(request, "hq/sign_in.html")
    typed = request.POST.get("email", "")
    email = hq_auth.normalize_email(typed)
    if email is None:
        return page(request, "hq/sign_in.html", {"error": True, "typed": typed[:254]})
    # From here every address gets the same answer. The lookup and the email happen later.
    nonce = secrets.token_urlsafe(16)
    if hq_auth.within_limits(email, client_ip(request)):
        try:
            background.run_later(hq_auth.issue_email_code, email, nonce)
        except Exception:
            logger.exception("hq sign-in code could not be handed over")
    response = SeeOther(CODE)
    # "typed" is only shown back on the code page. The lookup, limits and code use "email".
    value = signing.dumps({"email": email, "nonce": nonce, "typed": typed.strip()}, salt=PRE_SALT)
    _set_cookie(response, _cookie_names()[1], value, PRE_SECONDS)
    return response


@require_http_methods(["GET", "POST"])
def code(request):
    if is_verified(_session(request)):
        return SeeOther(HOME)
    pre = _pre(request)
    if pre is None:
        return SeeOther(SIGN_IN)
    shown = pre.get("typed") or pre["email"]
    if request.method == "GET":
        return page(request, "hq/code.html", {"email": shown, "notice": True})
    typed = "".join(request.POST.get("code", "").split())
    token = None
    if len(typed) == 6 and typed.isdigit():
        token = hq_auth.check_email_code(pre["email"], pre["nonce"], typed)
    if token is None:
        return page(request, "hq/code.html", {"email": shown, "error": True})
    session_name, pre_name = _cookie_names()
    response = _drop_cookies(SeeOther(VERIFY), pre_name)
    _set_cookie(response, session_name, token, hq_auth.SESSION_HOURS * 3600)
    return response


@require_http_methods(["GET", "POST"])
def verify(request):
    session = _session(request)
    if session is None:
        return SeeOther(SIGN_IN)
    if is_verified(session):
        return SeeOther(HOME)
    context = {"recovery": request.GET.get("method") == "recovery"}
    if request.method == "GET":
        return page(request, "hq/verify.html", context)
    result = two_step.verify_second_step(
        session.identity_id, request.POST.get("code", ""), surface=two_step.SURFACE_HQ
    )
    if result.locked:
        return _drop_cookies(page(request, "hq/paused.html"), *_cookie_names())
    if not result.ok:
        return page(request, "hq/verify.html", {**context, "error": True})
    token = hq_auth.mark_verified(session, result.credential_version)
    response = SeeOther(HOME)
    remaining = int((session.expires_at - timezone.now()).total_seconds())
    _set_cookie(response, _cookie_names()[0], token, max(remaining, 0))
    return response


def _events(identity):
    rows = []
    for entry in audit.recent_sign_in_events(identity):
        where = [
            SURFACE_LABELS.get(entry.detail.get("surface")), STEP_LABELS.get(entry.detail.get("step"))
        ]
        rows.append({
            "label": EVENT_LABELS[entry.action],
            "where": " · ".join(part for part in where if part),
            "at": entry.at,
        })
    return rows


@require_http_methods(["GET"])
def home(request):
    session = _session(request)
    if session is None:
        return SeeOther(SIGN_IN)
    if not is_verified(session):
        return SeeOther(VERIFY)
    return page(request, "hq/home.html", {
        "email": session.identity.email,
        "verified_at": session.verified_at,
        "expires_at": session.expires_at,
        "events": _events(session.identity),
    })


@require_POST
def sign_out(request):
    session = _session(request)
    if session is not None:
        hq_auth.sign_out(session)
    return _drop_cookies(SeeOther(SIGN_IN), *_cookie_names())


def _neutral(status, title, message):
    html = render_to_string("hq/neutral.html", {"title": title, "message": message})
    return HttpResponse(html, status=status)


def not_found(request, exception=None):
    return _neutral(404, "Page not found", "Page not found.")


def bad_request(request, exception=None):
    return _neutral(400, "Not available", "This page is not available right now.")


def forbidden(request, exception=None):
    return _neutral(403, "Not available", "This page is not available right now.")


def server_error(request):
    return _neutral(500, "Not available", "This page is not available right now.")
