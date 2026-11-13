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
from apps.notifications import services
from apps.notifications.models import SmsPolicy

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


def _route(view):
    def handler(request, operator):
        try:
            if not settings.SMS_GATEWAY_ENABLED:
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
    return {
        "sending_stopped": row.sending_stopped,
        "platform_daily_limit": row.platform_daily_limit,
        "site_daily_limit": row.site_daily_limit,
        "updated_at": row.updated_at.isoformat(),
    }


@_route
def policy_get(body, operator):
    row, _ = SmsPolicy.objects.get_or_create(pk=1)
    return JsonResponse(_state(row))


@_route
def policy_set(body, operator):
    return JsonResponse(_state(services.change_policy(operator, body)))


ROUTES = {
    "sms/policy/get/": policy_get,
    "sms/policy/set/": policy_set,
}
