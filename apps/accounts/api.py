"""Sign-in and account endpoints. Shapes here are the contract the frontend builds against."""
import secrets
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from ninja import Field, Router, Schema

from apps.accounts import google, mobile, services
from apps.accounts.auth import SessionCookieAuth
from apps.accounts.flow import (
    clear_flow_cookie,
    connect_redirect,
    finish_redirect,
    read_flow_cookie,
    same_state,
    set_flow_cookie,
)
from apps.accounts.models import ExternalIdentity, Session
from apps.accounts.sessions import (
    clear_session_cookie,
    client_ip,
    find_session,
    revoke_session,
    revoke_sessions,
    set_session_cookie,
)
from apps.common.errors import ApiError
from apps.common.middleware import require_allowed_origin

router = Router(tags=["accounts"])
signed_in = SessionCookieAuth()


class EmailIn(Schema):
    email: str


class EmailChangeIn(Schema):
    email: str
    step_up_code: str = ""


class VerifyIn(Schema):
    email: str
    code: str
    accept_policies: bool = False


class AccountOut(Schema):
    id: str
    email: str
    name: str
    email_verified: bool
    created_at: datetime


class ProviderOut(Schema):
    provider: str
    connected: bool


class DeviceOut(Schema):
    name: str
    current: bool
    signed_in_at: datetime
    last_used_at: datetime
    ip: str


class MobileOut(Schema):
    """The account's verified mobile. The full number is never returned."""

    verified: bool
    mobile_masked: str


class AccountSettingsOut(AccountOut):
    """What the Account Settings page reads. Every write on that page answers with it."""

    writes_open: bool
    email_change_step_up: bool
    email_changed_at: datetime | None
    email_change_available_at: datetime | None
    providers: list[ProviderOut]
    devices: list[DeviceOut]
    signed_in_count: int
    mobile: MobileOut


class VerifyOut(Schema):
    account: AccountOut
    created: bool


class MeOut(Schema):
    account: AccountOut


class AccountPatch(Schema):
    name: str = Field(max_length=120)


class MobileStartIn(Schema):
    mobile: str = Field(max_length=20)


class MobileCodeIn(Schema):
    code: str = Field(max_length=20)


class QueuedOut(Schema):
    queued: bool


class EmailConfirmIn(Schema):
    email: str
    code: str


class SessionOut(Schema):
    id: str
    device_label: str
    masked_ip: str
    created_at: datetime
    last_used_at: datetime
    current: bool


class SessionListOut(Schema):
    items: list[SessionOut]
    next_cursor: str | None = None


class RevokedOut(Schema):
    revoked: int
    signed_in_count: int


def account_out(account):
    return {
        "id": account.public_id,
        "email": account.email,
        "name": account.name,
        "email_verified": account.email_verified_at is not None,
        "created_at": account.created_at,
    }


def _live_sessions(account):
    return Session.objects.filter(
        account=account, revoked_at__isnull=True, expires_at__gt=timezone.now()
    ).order_by("-last_used_at", "-id")


def settings_out(request):
    """The signed-in account as the Account Settings page reads it."""
    account = request.account
    changed_at, available_at = services.email_change_pause(account)
    connected = set(account.identities.values_list("provider", flat=True))
    devices = [
        {
            "name": session.device_label,
            "current": session.pk == request.account_session.pk,
            "signed_in_at": session.created_at,
            "last_used_at": session.last_used_at,
            "ip": session.masked_ip,
        }
        for session in _live_sessions(account)
    ]
    return {
        **account_out(account),
        "writes_open": True,  # there is no launch gate in this backend
        "email_change_step_up": settings.ACCOUNT_EMAIL_CHANGE_HARDENED,
        "email_changed_at": changed_at,
        "email_change_available_at": available_at,
        "providers": [
            {"provider": provider, "connected": provider in connected}
            for provider in (ExternalIdentity.Provider.FACEBOOK, ExternalIdentity.Provider.GOOGLE)
        ],
        "devices": devices,
        "signed_in_count": len(devices),
        "mobile": mobile.mobile_out(account),
    }


@router.post("/auth/email-code/request/", response={204: None})
def email_code_request(request, payload: EmailIn):
    require_allowed_origin(request)
    services.request_signin_code(payload.email, client_ip(request))
    return 204, None


@router.post("/auth/email-code/verify/", response=VerifyOut)
def email_code_verify(request, response: HttpResponse, payload: VerifyIn):
    require_allowed_origin(request)
    account, _session, token, created = services.verify_signin_code(
        payload.email, payload.code, payload.accept_policies, request
    )
    set_session_cookie(response, token)
    return {"account": account_out(account), "created": created}


@router.post("/auth/logout/", response={204: None}, auth=signed_in)
def logout(request, response: HttpResponse):
    revoke_session(request.account_session)
    clear_session_cookie(response)
    return 204, None


@router.get("/me/", response=MeOut, auth=signed_in)
def me(request):
    return {"account": account_out(request.account)}


@router.get("/account/", response=AccountSettingsOut, auth=signed_in)
def account_detail(request):
    return settings_out(request)


@router.patch("/account/", response=AccountSettingsOut, auth=signed_in)
def account_update(request, payload: AccountPatch):
    services.update_account(request.account, name=payload.name)
    return settings_out(request)


@router.post("/account/email/start/", response={204: None}, auth=signed_in)
def email_change_start(request, payload: EmailChangeIn):
    services.start_email_change(
        request.account, payload.email, client_ip(request), payload.step_up_code
    )
    return 204, None


@router.post("/account/email/step-up/", response={204: None}, auth=signed_in)
def email_change_step_up(request):
    services.send_email_change_step_up(request.account, client_ip(request))
    return 204, None


@router.post("/account/email/confirm/", response=AccountSettingsOut, auth=signed_in)
def email_change_confirm(request, payload: EmailConfirmIn):
    services.confirm_email_change(
        request.account, payload.email, payload.code, request.account_session
    )
    return settings_out(request)


@router.post("/account/mobile/start/", response=QueuedOut, auth=signed_in)
def mobile_start(request, payload: MobileStartIn):
    mobile.start(request.account, payload.mobile, client_ip(request))
    return {"queued": True}


@router.post("/account/mobile/verify/", response=MobileOut, auth=signed_in)
def mobile_verify(request, payload: MobileCodeIn):
    return mobile.verify(request.account, payload.code)


@router.get("/account/sessions/", response=SessionListOut, auth=signed_in)
def session_list(request):
    live = Session.objects.filter(
        account=request.account, revoked_at__isnull=True
    ).order_by("-last_used_at", "-id")
    return {
        "items": [
            {
                "id": session.public_id,
                "device_label": session.device_label,
                "masked_ip": session.masked_ip,
                "created_at": session.created_at,
                "last_used_at": session.last_used_at,
                "current": session.pk == request.account_session.pk,
            }
            for session in live
        ],
        "next_cursor": None,
    }


@router.post("/account/sessions/sign-out-others/", response=RevokedOut, auth=signed_in)
def sign_out_others(request):
    revoked = revoke_sessions(request.account, keep=request.account_session)
    services.record_event(request.account, "sessions_revoked", count=revoked)
    return {"revoked": revoked, "signed_in_count": _live_sessions(request.account).count()}


def finish_connect(request, started, provider, state, code, error, fetch_user_id):
    """End a provider flow that Account Settings started. Returns the redirect to the page.

    `fetch_user_id` swaps the one-time code for the provider's user id, or returns None when
    the provider refuses. It is called only once the browser is known to be the one that
    started, still signed in to the same account.
    """
    if not same_state(started["state"], state):
        return connect_redirect(provider, "invalid_state")
    if error or not code:
        return connect_redirect(provider, "cancelled")
    session = find_session(request.COOKIES.get(settings.ACCOUNT_SESSION_COOKIE))
    if (
        session is None
        or session.public_id != started.get("session")
        or session.account.public_id != started.get("account")
    ):
        return connect_redirect(provider, "failed")
    user_id = fetch_user_id()
    if not user_id:
        return connect_redirect(provider, "failed")
    return connect_redirect(provider, services.connect_identity(session.account, provider, user_id))


# Google sign-in. These two are browser navigations: they answer with redirects, not JSON.
GOOGLE_STATE_COOKIE = "no_google_state"
GOOGLE_STATE_PATH = "/v1/auth/google/"
GOOGLE_STATE_SECONDS = 600


def _require_google():
    if not google.is_configured():
        raise ApiError("not_found", "No such endpoint.", status=404)


def _google_callback_url(request):
    return request.build_absolute_uri("/v1/auth/google/callback/")


def _end_google_flow(error=None):
    response = finish_redirect(error)
    clear_flow_cookie(response, GOOGLE_STATE_COOKIE, GOOGLE_STATE_PATH)
    return response


@router.get("/auth/google/start/", response={302: None})
def google_start(request, accept_policies: bool = False):
    _require_google()
    state = secrets.token_urlsafe(32)
    response = HttpResponseRedirect(google.authorization_url(_google_callback_url(request), state))
    set_flow_cookie(
        response,
        GOOGLE_STATE_COOKIE,
        GOOGLE_STATE_PATH,
        {"state": state, "accept_policies": accept_policies},
        GOOGLE_STATE_SECONDS,
    )
    return response


@router.get("/auth/google/callback/", response={302: None})
def google_callback(request, code: str = "", state: str = "", error: str = ""):
    _require_google()
    started = read_flow_cookie(request, GOOGLE_STATE_COOKIE, GOOGLE_STATE_SECONDS)
    if started is not None and started.get("mode") == "connect":

        def google_user_id():
            try:
                return google.fetch_profile(code, _google_callback_url(request)).user_id
            except google.GoogleSignInFailed:
                return None

        response = finish_connect(request, started, "google", state, code, error, google_user_id)
        clear_flow_cookie(response, GOOGLE_STATE_COOKIE, GOOGLE_STATE_PATH)
        return response
    if started is None or not same_state(started["state"], state):
        return _end_google_flow("google_state_invalid")
    if error or not code:
        return _end_google_flow("google_cancelled")
    try:
        profile = google.fetch_profile(code, _google_callback_url(request))
        _account, _session, token, _created = services.sign_in_with_google(
            profile, started["accept_policies"], request
        )
    except google.GoogleSignInFailed:
        return _end_google_flow("google_failed")
    except ApiError as refused:
        return _end_google_flow(refused.code)
    response = _end_google_flow()
    set_session_cookie(response, token)
    return response
