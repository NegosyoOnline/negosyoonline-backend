"""Connect and disconnect Google and Facebook from Account Settings.

Connecting reuses each provider's sign-in callback, so no second redirect URI is registered:
the start below marks the signed state cookie with mode "connect", and the callback then
links the identity to the account that started instead of signing anyone in.
"""
import secrets

from django.http import HttpResponse
from ninja import Router, Schema

from apps.accounts import api, api_facebook, facebook, google, services
from apps.accounts.api import AccountSettingsOut, settings_out, signed_in
from apps.accounts.flow import set_flow_cookie
from apps.common.errors import ApiError

router = Router(tags=["accounts"])

# provider: (module, label, state cookie, cookie path, cookie seconds, callback path)
FLOWS = {
    "google": (
        google,
        "Google",
        api.GOOGLE_STATE_COOKIE,
        api.GOOGLE_STATE_PATH,
        api.GOOGLE_STATE_SECONDS,
        "/v1/auth/google/callback/",
    ),
    "facebook": (
        facebook,
        "Facebook",
        api_facebook.STATE_COOKIE,
        api_facebook.COOKIE_PATH,
        api_facebook.STATE_SECONDS,
        "/v1/auth/facebook/callback/",
    ),
}


class ConnectStartOut(Schema):
    url: str


def _flow(provider):
    if provider not in FLOWS:
        raise ApiError("not_found", "No such endpoint.", status=404)
    return FLOWS[provider]


@router.post("/account/connections/{provider}/start/", response=ConnectStartOut, auth=signed_in)
def connect_start(request, response: HttpResponse, provider: str):
    module, label, cookie, path, seconds, callback = _flow(provider)
    if not module.is_configured():
        raise ApiError("not_configured", f"{label} sign-in is not available right now.")
    if request.account.identities.filter(provider=provider).exists():
        raise ApiError(
            "already_connected", f"A {label} account is already connected.", status=409
        )
    state = secrets.token_urlsafe(32)
    set_flow_cookie(
        response,
        cookie,
        path,
        {
            "state": state,
            "mode": "connect",
            "account": request.account.public_id,
            "session": request.account_session.public_id,
        },
        seconds,
    )
    return {"url": module.authorization_url(request.build_absolute_uri(callback), state)}


@router.post(
    "/account/connections/{provider}/disconnect/", response=AccountSettingsOut, auth=signed_in
)
def disconnect(request, provider: str):
    _flow(provider)
    services.disconnect_identity(request.account, provider)
    return settings_out(request)
