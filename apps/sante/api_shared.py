"""Copy the content source store's shared courses and media, under /v1/sites/{site_id}/shared-content/.

    GET  .../shared-content/        -> {is_source, source_name, courses: [...], media: [...]}
    POST .../shared-content/copy/   {kind: course|media, ids: [...] | all: true, again?: true}
                                    -> 202 {queued: [...], skipped: [...]}

Details and shapes: apps/sante/content_copy.py and docs/branch-notes/z8uq9mhbhc-cp1-copy-be.md.
Same owner gates as the training editor: the signed-in owner, the site found first (404 for a
missing or foreign site), the Billing-only rule (403), 404 everywhere while MEMBERS_ENABLED is
off. A site that is not a Santé site, or any site while no source is set, reads empty lists.
Every answer is Cache-Control: no-store.
"""
from django.conf import settings
from django.http import JsonResponse
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.common.errors import ApiError
from apps.sante import content_copy
from apps.sites.api import _owner_site

__all__ = ["router"]


def shared_site(request):
    if not settings.MEMBERS_ENABLED:
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        request.member_site = _owner_site(request, request.resolver_match.kwargs["site_id"])
    return account


router = Router(tags=["shared-content"], auth=shared_site)


class CopyIn(Schema):
    model_config = ConfigDict(extra="forbid")
    kind: str
    ids: list[str] | None = None
    all: bool | None = None
    again: bool | None = None


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


@router.get("/sites/{site_id}/shared-content/")
def shared_list(request, site_id: str):
    return _json(content_copy.list_for(request.member_site))


@router.post("/sites/{site_id}/shared-content/copy/")
def shared_copy(request, site_id: str, payload: CopyIn):
    result = content_copy.request_copy(
        request.member_site, kind=payload.kind, ids=payload.ids,
        every=payload.all is True, again=payload.again is True,
    )
    return _json(result, 202)
