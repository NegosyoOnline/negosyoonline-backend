"""The Lifecycle staff routes: the list of ended sites due for erasure, the batches, and the
propose, approve, cancel and hold routes (plan z8uq9mb22v, sections 5 and 6).

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps. A handler answers as
if it did not exist (404) while SITE_ERASURE_ENABLED is off, before any lifecycle table is read.
Every route is a POST with a small JSON body, like the other staff routes, and returns ids, dates,
counts and codes only. There is no run route: an approved batch is run by the Celery task
`lifecycle.run_approved` (every 5 minutes) or by `manage.py erasure_run`, never by a request.
"""
import json

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from apps.common.errors import ApiError, error_body
from apps.lifecycle import dry_run, workflow

MAX_BODY = 4096


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
            if not settings.SITE_ERASURE_ENABLED:
                raise ApiError("not_found", "Not found.", status=404)
            response = JsonResponse(view(_body(request), operator))
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


@_route
def erasure_list(body, operator):
    return dry_run.erasure_dry_run(timezone.now())


@_route
def batches(body, operator):
    return workflow.overview()


@_route
def propose(body, operator):
    batch = workflow.propose(operator, body.get("sites"), timezone.now())
    return workflow.batch_as_dict(batch)


@_route
def approve(body, operator):
    batch = workflow.approve(
        operator, body.get("batch"), confirmation=body.get("confirmation"),
        exclude=body.get("exclude"), reason=body.get("reason"), now=timezone.now(),
    )
    return workflow.batch_as_dict(batch)


@_route
def cancel(body, operator):
    batch = workflow.cancel(operator, body.get("batch"), reason=body.get("reason"), now=timezone.now())
    return workflow.batch_as_dict(batch)


@_route
def hold(body, operator):
    workflow.place_hold(
        operator, body.get("site"), reason_code=body.get("reason_code"),
        reference=body.get("reference"), now=timezone.now(),
    )
    return workflow.overview()


@_route
def release(body, operator):
    workflow.release_hold(operator, body.get("site"), now=timezone.now())
    return workflow.overview()


ROUTES = {
    "lifecycle/erasure/list/": erasure_list,
    "lifecycle/erasure/batches/": batches,
    "lifecycle/erasure/propose/": propose,
    "lifecycle/erasure/approve/": approve,
    "lifecycle/erasure/cancel/": cancel,
    "lifecycle/erasure/hold/": hold,
    "lifecycle/erasure/hold/release/": release,
}
