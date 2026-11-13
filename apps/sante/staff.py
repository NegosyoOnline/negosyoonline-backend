"""The Santé catalog owner routes: what the Settings page of the HQ staff app calls.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/
and calls each handler with the staff user of a session that passed both steps; the allowed
Origin, the method and the session are checked there and nowhere else. A handler answers as
if it did not exist while SANTE_SETTINGS_ENABLED is off, and reads no Santé table then.

Every route is a POST with a small JSON body, the one that only reads too. The owner is
changed only through `services.set_catalog_owner`, which keeps the lock order and writes the
audit entry.
"""
import json

from django.http import JsonResponse

from apps.common.errors import ApiError, error_body
from apps.sante import services
from apps.sites import templates
from apps.sites.models import Site

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
            if not services.enabled():
                raise ApiError("not_found", "Not found.", status=404)
            response = view(_body(request), operator)
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


def _invalid(message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={"site": [message]}
    )


def _site(site):
    return {"id": site.public_id, "name": site.display_name}


def _live_sante_sites():
    # The same rule as the candidate list of the admin form: live Santé sites only.
    return Site.objects.filter(template=templates.SANTE, deleted_at__isnull=True)


def _state():
    owner = services.catalog_owner_site()
    return {
        "owner": _site(owner) if owner else None,
        "candidates": [_site(site) for site in _live_sante_sites().order_by("display_name", "pk")],
    }


@_route
def owner_get(body, operator):
    return JsonResponse(_state())


@_route
def owner_set(body, operator):
    if "site" not in body:
        raise _invalid("Choose a site, or send null to clear the catalog owner.")
    public_id = body["site"]
    site = None
    if public_id is not None:
        if not isinstance(public_id, str) or not public_id:
            raise _invalid("Choose a site from the list.")
        site = _live_sante_sites().filter(public_id=public_id).first()
        if site is None:
            raise _invalid(services.NOT_LIVE_SANTE)
    try:
        services.set_catalog_owner(site, operator=operator)
    except ValueError as refusal:
        # The site closed between the lookup and the lock.
        raise _invalid(str(refusal))
    return JsonResponse(_state())


ROUTES = {
    "sante/owner/get/": owner_get,
    "sante/owner/set/": owner_set,
}
