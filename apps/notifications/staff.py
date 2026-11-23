"""The SMS settings routes: what the Settings page of the HQ staff app calls.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/
and calls each handler with the staff user of a session that passed both steps; the allowed
Origin, the method and the session are checked there and nowhere else. A handler answers as
if it did not exist while SMS_GATEWAY_ENABLED is off, and reads no SMS table then.

Every route is a POST with a small JSON body, the one that only reads too. The settings are
changed only through `services.change_policy`, which writes the audit entry.
"""
import json

from django.conf import settings
from django.http import JsonResponse

from apps.common.errors import ApiError, error_body
from apps.notifications import devices, services
from apps.notifications.models import SmsPolicy, store_limit_columns_exist
from apps.operators import audit, two_step

MAX_BODY = 2048


def _body(request):
    """The JSON object sent, or {} when there is none, it is too large or it is not one."""
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_BODY:
            return {}
        data = json.loads(request.body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _route(view, *, needs_mgmt=False):
    def handler(request, operator):
        try:
            if not settings.SMS_GATEWAY_ENABLED or (
                needs_mgmt and not settings.SMS_GATEWAY_MGMT_ENABLED
            ):
                raise ApiError("not_found", "Not found.", status=404)
            response = view(_body(request), operator)
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


def _state(row):
    state = {
        "sending_stopped": row.sending_stopped,
        "platform_daily_limit": row.platform_daily_limit,
        "site_daily_limit": row.site_daily_limit,
        "updated_at": row.updated_at.isoformat(),
    }
    if store_limit_columns_exist():
        state["store_daily_limit"] = row.store_daily_limit
        state["all_stores_daily_limit"] = row.all_stores_daily_limit
    if services.store_bulk_limit_column_exists():
        state["store_bulk_daily_limit"] = row.store_bulk_daily_limit
    return state


@_route
def policy_get(body, operator):
    row, _ = SmsPolicy.objects.get_or_create(pk=1)
    return JsonResponse(_state(row))


@_route
def policy_set(body, operator):
    return JsonResponse(_state(services.change_policy(operator, body)))


# The platform phone. Needs SMS_GATEWAY_MGMT_ENABLED as well. Incoming forwarding is not touched.

REAUTH_MESSAGE = "That code did not work. Enter the current code from your authenticator app."
_DEVICE_STATUS = {"invalid_name": 400, "phone_exists": 409, "name_taken": 409, "not_found": 404}


def _device_route(view):
    return _route(view, needs_mgmt=True)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except devices.DeviceError as refusal:
        raise ApiError(refusal.code, refusal.message, status=_DEVICE_STATUS.get(refusal.code, 400))


def _confirmed(body):
    if body.get("confirm") is not True:
        raise ApiError("confirm_required", "Confirm to continue.", status=400)


def _fresh_code(body, operator):
    """Refuse unless the body holds a fresh 6-digit authenticator code. One message for a wrong,
    stale, reused or locked code and for a recovery code. The failure is audited with counts only."""
    identity = operator.admin_identities.filter(is_active=True).first()
    result = (
        two_step.verify_fresh_totp(
            identity.pk, body.get("code"), surface=two_step.SURFACE_HQ
        )
        if identity is not None
        else two_step.Result(ok=False)
    )
    if result.ok:
        return
    identity = operator.admin_identities.filter(is_active=True).first()
    audit.record(
        operator,
        "sms_device_reauth_failed",
        target_type=devices.TARGET,
        detail={
            "failed_attempts": identity.failed_attempts if identity else 0,
            "locked": bool(result.locked),
        },
    )
    raise ApiError("reauth_failed", REAUTH_MESSAGE, status=403)


def _answer(device, token=None):
    answer = {"server_address": settings.SMS_GATEWAY_PUBLIC_URL, "device": devices.status(device)}
    if token is not None:
        answer["token"] = token
    return JsonResponse(answer)


def _named(body, operator):
    """The platform phone named in the body, else 404."""
    name = _call(devices.clean_name, body.get("name"))
    phone = devices.platform_phone()
    if phone is None or phone.name != name:
        raise ApiError("not_found", "No such phone.", status=404)
    return phone


@_device_route
def device_get(body, operator):
    return _answer(devices.platform_phone())


@_device_route
def device_register(body, operator):
    _confirmed(body)
    # platform_phone() is an enabled phone when there is one, so a disabled one here means the
    # new phone replaces it. That needs a fresh code, as rotate and disable do. An enabled phone
    # still ends in phone_exists (409) from register.
    old = devices.platform_phone()
    replaces = old if old is not None and not old.is_active else None
    if replaces is not None:
        _fresh_code(body, operator)
    device, token = _call(
        devices.register, operator, body.get("name"), via="hq", replaces=replaces
    )
    return _answer(device, token)


@_device_route
def device_rotate(body, operator):
    _confirmed(body)
    phone = _named(body, operator)
    if not phone.is_active:
        raise ApiError(
            "device_disabled", "This phone is disabled. Register a new phone.", status=409
        )
    _fresh_code(body, operator)
    device, token = _call(devices.rotate, operator, phone.name, via="hq")
    return _answer(device, token)


@_device_route
def device_disable(body, operator):
    _confirmed(body)
    phone = _named(body, operator)
    _fresh_code(body, operator)
    device, _changed = _call(devices.disable, operator, phone.name, via="hq")
    return _answer(device)


ROUTES = {
    "sms/policy/get/": policy_get,
    "sms/policy/set/": policy_set,
    "sms/device/get/": device_get,
    "sms/device/register/": device_register,
    "sms/device/rotate/": device_rotate,
    "sms/device/disable/": device_disable,
}
