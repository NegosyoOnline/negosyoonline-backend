"""The contacts routes: what the Contacts pages of the HQ staff app call.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps; the allowed Origin,
the method and the session are checked there and nowhere else. A handler answers as if it did
not exist while either switch is off, while the tables are not there and for a staff user
without inbox access.

Every route is a POST with a small JSON body: no number, name or id is ever in an address.
Only the two upload routes take a large body (a CSV file's text).
"""
import json

from django.http import HttpResponse, JsonResponse

from apps.common.errors import ApiError, error_body
from apps.inbox import contacts
from apps.inbox.contacts import contact_scope

MAX_BODY = 4096
MAX_UPLOAD_BODY = 2_400_000  # a 1 MB file with every character escaped, under Django's 2.5 MB


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
            scope = contact_scope(operator)
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


def _no_content():
    return HttpResponse(status=204)


def status(scope, body):
    return JsonResponse({"read": True, "change": scope.can_change})


def listing(scope, body):
    group = body.get("group")
    if group is not None and not isinstance(group, str):
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"group": ["Choose a group."]},
        )
    rows, following = contacts.list_contacts(
        scope, group=group or None, query=body.get("query"), cursor=_cursor(body), limit=_limit(body)
    )
    return JsonResponse({"results": rows, "next": following})


def open_one(scope, body):
    opened = contacts.open_contact(scope, body.get("contact"))
    if opened is None:
        raise _not_found()
    return JsonResponse(opened)


def add(scope, body):
    return JsonResponse(contacts.add_contact(scope, body), status=201)


def edit(scope, body):
    changed = contacts.edit_contact(scope, body.get("contact"), body)
    if changed is None:
        raise _not_found()
    return JsonResponse(changed)


def delete(scope, body):
    if contacts.delete_contact(scope, body.get("contact")) is None:
        raise _not_found()
    return _no_content()


def groups_list(scope, body):
    return JsonResponse({"results": contacts.list_groups(scope)})


def groups_add(scope, body):
    return JsonResponse(contacts.add_group(scope, body), status=201)


def groups_rename(scope, body):
    return JsonResponse(contacts.rename_group(scope, body.get("group"), body))


def groups_remove(scope, body):
    contacts.remove_group(scope, body.get("group"))
    return _no_content()


def upload_check(scope, body):
    return JsonResponse(contacts.check_upload(scope, body.get("csv"), body.get("group")))


def upload_save(scope, body):
    answer = contacts.save_upload(scope, body.get("csv"), body.get("group"), body.get("expected_new"))
    return JsonResponse(answer, status=201)


ROUTES = {
    "contacts/status/": _route(status),
    "contacts/list/": _route(listing),
    "contacts/open/": _route(open_one),
    "contacts/add/": _route(add),
    "contacts/edit/": _route(edit),
    "contacts/delete/": _route(delete),
    "contacts/groups/list/": _route(groups_list),
    "contacts/groups/add/": _route(groups_add),
    "contacts/groups/rename/": _route(groups_rename),
    "contacts/groups/remove/": _route(groups_remove),
    "contacts/upload/check/": _route(upload_check, limit=MAX_UPLOAD_BODY),
    "contacts/upload/": _route(upload_save, limit=MAX_UPLOAD_BODY),
}
