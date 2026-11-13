"""Facebook sign-in endpoints and Meta's data deletion callback."""
import secrets

from django.conf import settings
from django.core import signing
from django.http import HttpResponse, HttpResponseRedirect
from ninja import Router, Schema

from apps.accounts import facebook, services
from apps.accounts.api import VerifyOut, account_out, finish_connect
from apps.accounts.flow import (
    clear_flow_cookie,
    finish_redirect,
    read_flow_cookie,
    same_state,
    set_flow_cookie,
)
from apps.accounts.sessions import client_ip, set_session_cookie
from apps.common.errors import ApiError
from apps.common.middleware import require_allowed_origin

router = Router(tags=["accounts"])

COOKIE_PATH = "/v1/auth/facebook/"
STATE_COOKIE = "no_facebook_state"
STATE_SECONDS = 600
# Holds a first-time Facebook user until they confirm an email. Signed and HttpOnly.
PENDING_COOKIE = "no_facebook_pending"
PENDING_SECONDS = 900


class FacebookConfirmIn(Schema):
    email: str = ""
    code: str = ""
    accept_policies: bool = False


class CodeSentOut(Schema):
    email: str


def _require_facebook():
    if not facebook.is_configured():
        raise ApiError("not_found", "No such endpoint.", status=404)


def _callback_url(request):
    return request.build_absolute_uri("/v1/auth/facebook/callback/")


def _end_flow(error=None):
    response = finish_redirect(error)
    clear_flow_cookie(response, STATE_COOKIE, COOKIE_PATH)
    return response


def _usable_email(value):
    """Facebook's email in our stored form, or "" when it gave none or a broken one."""
    try:
        return services.normalise_email(value)
    except ApiError:
        return ""


@router.get("/auth/facebook/start/", response={302: None})
def facebook_start(request, accept_policies: bool = False):
    _require_facebook()
    state = secrets.token_urlsafe(32)
    response = HttpResponseRedirect(facebook.authorization_url(_callback_url(request), state))
    set_flow_cookie(
        response,
        STATE_COOKIE,
        COOKIE_PATH,
        {"state": state, "accept_policies": accept_policies},
        STATE_SECONDS,
    )
    return response


@router.get("/auth/facebook/callback/", response={302: None})
def facebook_callback(request, code: str = "", state: str = "", error: str = ""):
    _require_facebook()
    started = read_flow_cookie(request, STATE_COOKIE, STATE_SECONDS)
    if started is not None and started.get("mode") == "connect":

        def facebook_user_id():
            try:
                return facebook.fetch_profile(code, _callback_url(request)).user_id
            except facebook.FacebookSignInFailed:
                return None

        response = finish_connect(
            request, started, "facebook", state, code, error, facebook_user_id
        )
        clear_flow_cookie(response, STATE_COOKIE, COOKIE_PATH)
        return response
    if started is None or not same_state(started["state"], state):
        return _end_flow("facebook_state_invalid")
    if error or not code:
        return _end_flow("facebook_cancelled")
    try:
        profile = facebook.fetch_profile(code, _callback_url(request))
        signed_in = services.sign_in_with_facebook(profile, request)
    except facebook.FacebookSignInFailed:
        return _end_flow("facebook_failed")
    except ApiError as refused:
        return _end_flow(refused.code)
    if signed_in is not None:
        _account, _session, token = signed_in
        response = _end_flow()
        set_session_cookie(response, token)
        return response
    # First time: nobody is signed in until an email is confirmed by code.
    email = _usable_email(profile.email)
    response = HttpResponseRedirect(f"{settings.SITE_URL}/?signin=1&facebook=confirm")
    clear_flow_cookie(response, STATE_COOKIE, COOKIE_PATH)
    set_flow_cookie(
        response,
        PENDING_COOKIE,
        COOKIE_PATH,
        {
            "user_id": profile.user_id,
            "name": profile.name,
            "email": email,
            "email_from_facebook": bool(email),
            "accept_policies": started["accept_policies"],
        },
        PENDING_SECONDS,
    )
    return response


@router.post("/auth/facebook/confirm-email/", response={200: VerifyOut, 202: CodeSentOut})
def facebook_confirm_email(request, response: HttpResponse, payload: FacebookConfirmIn):
    require_allowed_origin(request)
    _require_facebook()
    pending = read_flow_cookie(request, PENDING_COOKIE, PENDING_SECONDS)
    if pending is None:
        raise ApiError("facebook_not_started", "Start again with Facebook.")
    if not payload.code:
        # Send the code. The typed email counts only when Facebook supplied none.
        email = pending["email"] if pending["email_from_facebook"] else payload.email
        email = services.send_facebook_email_code(pending["user_id"], email, client_ip(request))
        set_flow_cookie(
            response, PENDING_COOKIE, COOKIE_PATH, {**pending, "email": email}, PENDING_SECONDS
        )
        return 202, {"email": email}
    account, _session, token, created = services.confirm_facebook_email(
        pending["user_id"],
        pending["name"],
        pending["email"],
        payload.code,
        pending["accept_policies"] or payload.accept_policies,
        request,
    )
    set_session_cookie(response, token)
    clear_flow_cookie(response, PENDING_COOKIE, COOKIE_PATH)
    return 200, {"account": account_out(account), "created": created}


# Meta's data deletion callback. Meta's servers call it; the signature is the proof.
DELETION_PATH = "/v1/webhooks/facebook/data-deletion/"
DELETION_SALT = "accounts.facebook.data-deletion"
DELETION_MAX_BYTES = 16_384


class DeletionOut(Schema):
    url: str
    confirmation_code: str


class DeletionStatusOut(Schema):
    status: str


@router.post("/webhooks/facebook/data-deletion/", response=DeletionOut)
def facebook_data_deletion(request):
    _require_facebook()
    if len(request.body) > DELETION_MAX_BYTES:
        raise ApiError("payload_too_large", "The request is too large.", status=413)
    try:
        user_id = facebook.parse_signed_request(request.POST.get("signed_request", ""))
    except facebook.StaleSignedRequest:
        raise ApiError("stale_request", "The request is too old or from the future.") from None
    except facebook.BadSignedRequest:
        raise ApiError("invalid_signature", "The request is not signed by Meta.") from None
    # The same answer whether or not we knew this Facebook user, so the call reveals nothing.
    services.remove_facebook_identity(user_id)
    # The unlink is already done, so there is nothing to track: the code is a value we signed,
    # and the status endpoint only has to recognise its own signature.
    code = signing.dumps(secrets.token_hex(8), salt=DELETION_SALT)
    return {"url": request.build_absolute_uri(f"{DELETION_PATH}{code}/"), "confirmation_code": code}


@router.get("/webhooks/facebook/data-deletion/{code}/", response=DeletionStatusOut)
def facebook_data_deletion_status(request, code: str):
    try:
        signing.loads(code, salt=DELETION_SALT)
    except signing.BadSignature:
        raise ApiError("not_found", "No such request.", status=404) from None
    return {"status": "completed"}
