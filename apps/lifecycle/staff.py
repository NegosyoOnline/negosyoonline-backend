"""The Lifecycle staff route: the list of ended sites due for erasure, read only.

It is in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and calls
each handler with the staff user of a session that passed both steps. A handler answers as if
it did not exist (404) while SITE_ERASURE_ENABLED is off. It is a POST, like the other staff
routes, writes nothing and returns ids, dates, counts and codes only. The propose, approve,
hold and run routes are not part of this change.
"""
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from apps.common.errors import ApiError, error_body
from apps.lifecycle import dry_run


def _route(view):
    def handler(request, operator):
        try:
            if not settings.SITE_ERASURE_ENABLED:
                raise ApiError("not_found", "Not found.", status=404)
            response = JsonResponse(view())
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


@_route
def erasure_list():
    return dry_run.erasure_dry_run(timezone.now())


ROUTES = {"lifecycle/erasure/list/": erasure_list}
