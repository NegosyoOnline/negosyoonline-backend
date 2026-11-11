"""The inbox routes: what the Inbox pages of the HQ staff app call.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/
and calls each handler with the staff user of a session that passed both steps; the allowed
Origin, the method and the session are checked there and nowhere else. A handler answers as
if it did not exist while SMS_INBOX_ENABLED is off, for any platform but SMS, and for a
staff user without inbox access; it reads no inbox table in the first two cases.

Every route is a POST with a small JSON body, the ones that only read too: no number, text
or id is ever in an address. Every read and send starts from `scope_for`.
"""
import json

from django.http import JsonResponse

from apps.common.errors import ApiError, error_body
from apps.inbox import reading, sending
from apps.inbox.access import scope_for
from apps.inbox.switch import inbox_on
from apps.notifications import services

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


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _route(view):
    def handler(request, operator):
        try:
            if not inbox_on():
                raise _not_found()
            body = _body(request)
            scope = scope_for(operator, body.get("platform"))
            if scope is None:
                raise _not_found()
            response = view(scope, body)
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


def _limit(body):
    limit = body.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"limit": ["Enter a whole number."]},
        )
    return limit


def _cursor(body):
    cursor = body.get("cursor") or ""
    if not isinstance(cursor, str):
        raise ApiError("invalid_cursor", "That page marker is not valid.")
    return cursor


@_route
def status(scope, body):
    unread = scope.conversations.filter(unread_count__gt=0).count()
    return JsonResponse({
        "platform": scope.platform, "read": True, "send": scope.can_send,
        **services.sms_status(), "unread": unread,
    })


@_route
def listing(scope, body):
    rows, following = reading.conversations(
        scope, state=body.get("state") or "all", cursor=_cursor(body), limit=_limit(body)
    )
    return JsonResponse({"results": rows, "next": following})


@_route
def find(scope, body):
    return JsonResponse({"results": reading.find(scope, body.get("number")), "next": None})


@_route
def open_one(scope, body):
    opened = reading.open_conversation(
        scope, body.get("conversation"), cursor=_cursor(body), limit=_limit(body)
    )
    if opened is None:
        raise _not_found()
    return JsonResponse(opened)


def _sent(answer, created):
    return JsonResponse(answer, status=201 if created else 200)


@_route
def reply(scope, body):
    return _sent(*sending.reply(scope, body.get("conversation"), body.get("text"), body.get("request_id")))


@_route
def compose(scope, body):
    return _sent(*sending.compose(scope, body.get("to"), body.get("text"), body.get("request_id")))


ROUTES = {
    "inbox/status/": status,
    "inbox/conversations/list/": listing,
    "inbox/conversations/find/": find,
    "inbox/conversations/open/": open_one,
    "inbox/conversations/reply/": reply,
    "inbox/compose/": compose,
}
