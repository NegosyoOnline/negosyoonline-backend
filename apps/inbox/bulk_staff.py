"""The Bulk SMS routes: what the Bulk SMS page of the HQ staff app calls.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps; the allowed Origin,
the method and the session are checked there and nowhere else. A handler answers as if it did
not exist while a switch is off, while the tables are not there and for a staff user without
the send and bulk permissions.

Every route is a POST with a small JSON body: no number, text or id is ever in an address. Only
the three that take a source (`check`, `left-out`, `confirm`) take a large body.
"""
import json

from django.http import HttpResponse, JsonResponse

from apps.common.errors import ApiError, error_body
from apps.inbox import bulk
from apps.inbox.bulk import bulk_scope

MAX_BODY = 4096
MAX_SOURCE_BODY = 2_400_000  # a 1 MB file with every character escaped, under Django's 2.5 MB


def _body(request, limit):
    """The JSON object sent, or {} when there is none, it is too large or it is not one."""
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > limit:
            return {}
        data = json.loads(request.body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _route(view, *, limit=MAX_BODY):
    def handler(request, operator):
        try:
            scope = bulk_scope(operator)
            if scope is None:
                raise _not_found()
            response = view(scope, _body(request, limit))
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


def status(scope, body):
    return JsonResponse(bulk.status(scope))


def listing(scope, body):
    rows, following = bulk.list_batches(scope, cursor=_cursor(body), limit=_limit(body))
    return JsonResponse({"results": rows, "next": following})


def check(scope, body):
    return JsonResponse(bulk.check_answer(scope, body))


def left_out(scope, body):
    return HttpResponse(bulk.left_out(scope, body), content_type="text/csv; charset=utf-8")


def confirm(scope, body):
    row, created = bulk.confirm(scope, body)
    return JsonResponse(row, status=201 if created else 200)


def open_one(scope, body):
    return JsonResponse(bulk.open_batch(scope, body.get("batch"), cursor=_cursor(body), limit=_limit(body)))


def stop(scope, body):
    return JsonResponse(bulk.stop(scope, body.get("batch")))


ROUTES = {
    "bulk/status/": _route(status),
    "bulk/list/": _route(listing),
    "bulk/check/": _route(check, limit=MAX_SOURCE_BODY),
    "bulk/left-out/": _route(left_out, limit=MAX_SOURCE_BODY),
    "bulk/confirm/": _route(confirm, limit=MAX_SOURCE_BODY),
    "bulk/open/": _route(open_one),
    "bulk/stop/": _route(stop),
}
