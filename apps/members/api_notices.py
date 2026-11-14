"""The Subscriber's member notices (announcements and promos), under /v1/sites/{site_id}/member-notices/:

    GET    .../member-notices/                       -> {notices: [notice]}
    POST   .../member-notices/                       {kind, title, body?, starts_at?, ends_at?, active?} -> 201 notice
    PATCH  .../member-notices/{notice_id}/           any of the same keys -> notice
    DELETE .../member-notices/{notice_id}/           -> {status: "deleted"}
    POST   .../member-notices/{notice_id}/image/     multipart `file` (JPEG, PNG, WebP, 2 MB) -> notice
    DELETE .../member-notices/{notice_id}/image/     -> notice

Same gates as the recovery routes (apps/members/api_owner.py): the owner session, the site found
first through site_for (404 for a missing, foreign or non-Santé site), the Billing-only rule
(403), 404 everywhere while MEMBERS_ENABLED is off. A notice is named by its public id and is
always read under the caller's own site. Every answer is Cache-Control: no-store.
"""
from datetime import datetime

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from ninja import Router, Schema
from pydantic import ConfigDict

from . import notices
from .api_owner import member_site

router = Router(tags=["member-notices"], auth=member_site)


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid")


class NoticeIn(StrictSchema):
    kind: str
    title: str
    body: str = ""
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    active: bool = True


class NoticePatch(StrictSchema):
    """Only the keys sent are changed."""

    kind: str | None = None
    title: str | None = None
    body: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    active: bool | None = None


def _json(data, status=200):
    response = JsonResponse(data, status=status, encoder=DjangoJSONEncoder)
    response["Cache-Control"] = "no-store"
    return response


@router.get("/sites/{site_id}/member-notices/")
def notice_list(request, site_id: str):
    return _json({"notices": [notices.notice_out(n) for n in notices.list_for_owner(request.member_site)]})


@router.post("/sites/{site_id}/member-notices/")
def notice_create(request, site_id: str, payload: NoticeIn):
    notice = notices.create_notice(request.member_site, payload.dict())
    return _json(notices.notice_out(notice), 201)


@router.patch("/sites/{site_id}/member-notices/{notice_id}/")
def notice_update(request, site_id: str, notice_id: str, payload: NoticePatch):
    data = payload.dict(exclude_unset=True)
    for name in ("kind", "title", "body", "active"):  # a null here is a wrong value, not "leave it"
        if name in data and data[name] is None:
            data[name] = ""
    notice = notices.update_notice(request.member_site, notice_id, data)
    return _json(notices.notice_out(notice))


@router.delete("/sites/{site_id}/member-notices/{notice_id}/")
def notice_delete(request, site_id: str, notice_id: str):
    notices.delete_notice(request.member_site, notice_id)
    return _json({"status": "deleted"})


@router.post("/sites/{site_id}/member-notices/{notice_id}/image/")
def notice_image_set(request, site_id: str, notice_id: str):
    """A form post (multipart): `file` is a JPEG, PNG or WebP of at most 2 MB."""
    notice = notices.set_image(request.member_site, notice_id, request.FILES.get("file"))
    return _json(notices.notice_out(notice))


@router.delete("/sites/{site_id}/member-notices/{notice_id}/image/")
def notice_image_clear(request, site_id: str, notice_id: str):
    return _json(notices.notice_out(notices.clear_image(request.member_site, notice_id)))
