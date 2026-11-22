"""The Subscriber's own SMS phone, under /v1/sites/{site_id}/sms-gateway/:

    GET   .../sms-gateway/          -> {server_address, device: {...} | null}
    POST  .../sms-gateway/register/ -> {server_address, device, token}  (the token, once)
    POST  .../sms-gateway/rotate/   {step_up_code}  -> the same, with a new token
    POST  .../sms-gateway/disable/  -> {server_address, device: {...}}

Every answer is Cache-Control: no-store. The token is in the register and rotate answers only;
only its hash is stored, and neither the audit event, a log line nor any other answer holds
it. The phone is always the Site's own: no route takes a device id or name.

Every route answers 404, never 403, while SMS_GATEWAY_ENABLED or SMS_SITE_LINES_ENABLED is off,
and for a missing, removed, foreign or non-Santé site, and for a Site that has no line now
(`sms.site_has_line`: billing serving and not disabled by HQ), so none of them shows the
feature exists. Rotate needs a step-up code (purpose `sms_line_rotate`) from the account's
email or verified mobile. Incoming forwarding is never touched here.
"""
from django.conf import settings
from django.http import JsonResponse
from ninja import Router, Schema
from pydantic import ConfigDict, Field

from apps.accounts import services as accounts
from apps.accounts.api import signed_in
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.notifications import devices, sms
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for

PURPOSE = "sms_line_rotate"
WRITE_LIMIT, WINDOW = 20, 3600  # per account per hour


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def line_site(request):
    """The router's sign-in step: the switches, then the Site, before any body is read."""
    if not (sms.gateway_on() and sms.site_lines_on()):
        raise _not_found()
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site = site_for(account, site_id, template=templates.SANTE)
        if not sms.site_has_line(site):  # also covers a Billing-only Site: 404, never 403
            raise _not_found()
        request.line_site = _owner_site(request, site_id)
    return account


router = Router(tags=["sms-gateway"], auth=line_site)


class RotateIn(Schema):
    model_config = ConfigDict(extra="forbid")
    step_up_code: str = Field("", max_length=20)


def _json(request, device, token=None, status=200):
    # Never `status(None)`: with no phone it would show the platform phone.
    shown = devices.status(device) if device is not None else None
    answer = {"server_address": settings.SMS_GATEWAY_PUBLIC_URL, "device": shown}
    if token is not None:
        answer["token"] = token
    response = JsonResponse(answer, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _refuse(error):
    return ApiError(error.code, error.message, status=409)


@router.get("/sites/{site_id}/sms-gateway/")
def line_status(request, site_id: str):
    return _json(request, devices.site_phone(request.line_site))


@router.post("/sites/{site_id}/sms-gateway/register/")
def line_register(request, site_id: str):
    hit("sms-line-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    try:
        device, token = devices.register_site(request.account, request.line_site)
    except devices.DeviceError as error:
        raise _refuse(error) from None
    return _json(request, device, token, status=201)


@router.post("/sites/{site_id}/sms-gateway/rotate/")
def line_rotate(request, site_id: str, payload: RotateIn):
    hit("sms-line-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    accounts.require_step_up(request.account, payload.step_up_code.strip(), PURPOSE)
    try:
        device, token = devices.rotate_site(request.account, request.line_site)
    except devices.DeviceError as error:
        raise _refuse(error) from None
    accounts.clear_step_up(request.account, PURPOSE)
    return _json(request, device, token)


@router.post("/sites/{site_id}/sms-gateway/disable/")
def line_disable(request, site_id: str):
    hit("sms-line-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    try:
        device = devices.disable_site(request.account, request.line_site)
    except devices.DeviceError as error:
        raise _refuse(error) from None
    return _json(request, device)
