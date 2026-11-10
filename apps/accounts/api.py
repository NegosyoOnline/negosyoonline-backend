"""Sign-in and account endpoints. Shapes here are the contract the frontend builds against."""
from datetime import datetime

from django.http import HttpResponse
from ninja import Field, Router, Schema

from apps.accounts import services
from apps.accounts.auth import SessionCookieAuth
from apps.accounts.models import Session
from apps.accounts.sessions import (
    clear_session_cookie,
    client_ip,
    revoke_session,
    revoke_sessions,
    set_session_cookie,
)
from apps.common.middleware import require_allowed_origin

router = Router(tags=["accounts"])
signed_in = SessionCookieAuth()


class EmailIn(Schema):
    email: str


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


class VerifyOut(Schema):
    account: AccountOut
    created: bool


class MeOut(Schema):
    account: AccountOut


class AccountPatch(Schema):
    name: str = Field(max_length=120)


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


def account_out(account):
    return {
        "id": account.public_id,
        "email": account.email,
        "name": account.name,
        "email_verified": account.email_verified_at is not None,
        "created_at": account.created_at,
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


@router.get("/account/", response=AccountOut, auth=signed_in)
def account_detail(request):
    return account_out(request.account)


@router.patch("/account/", response=AccountOut, auth=signed_in)
def account_update(request, payload: AccountPatch):
    return account_out(services.update_account(request.account, name=payload.name))


@router.post("/account/email/start/", response={204: None}, auth=signed_in)
def email_change_start(request, payload: EmailIn):
    services.start_email_change(request.account, payload.email, client_ip(request))
    return 204, None


@router.post("/account/email/confirm/", response=AccountOut, auth=signed_in)
def email_change_confirm(request, payload: EmailConfirmIn):
    account = services.confirm_email_change(
        request.account, payload.email, payload.code, request.account_session
    )
    return account_out(account)


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
    return {"revoked": revoke_sessions(request.account, keep=request.account_session)}
