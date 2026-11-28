"""The Subscriber's media library editor, under /v1/sites/{site_id}/media/. Ids are public ids.

    GET    .../media/                       -> {assets: [asset]}   (drafts and switched-off ones too)
    POST   .../media/                       {title, asset_type: image|video, category?, product_category?,
                                             crop_ratio?, caption?, width?, height?, duration_seconds?,
                                             order?, active?} -> 201 asset (a draft, with no file yet)
    POST   .../media/reorder/               {ids: [every asset id]} -> {assets}
    PATCH  .../media/{asset_id}/            any of title, category, product_category, crop_ratio, caption,
                                             width, height, duration_seconds, order, active -> asset
    DELETE .../media/{asset_id}/            -> {status: "deleted"} (its files are removed from the store)
    POST   .../media/{asset_id}/publish/    {published_at?} -> asset (now, or at the time sent)
    POST   .../media/{asset_id}/unpublish/  -> asset
    POST   .../media/{asset_id}/image/      multipart `file` (JPEG, PNG or WebP, 10 MB) and `kind`:
                                             file (an image asset's picture, the default), poster (a
                                             video's) or thumb (optional) -> asset
    DELETE .../media/{asset_id}/image/      removes the thumbnail -> asset
    POST   .../media/{asset_id}/video/token/   -> what the browser needs to PUT one MP4 straight to the
                                             store: {pathname, client_token, upload_url, method, headers,
                                             max_bytes, content_type, expires_at, public_url}; or, while
                                             MEDIA_VIDEO_BUNNY_ENABLED is on and the store has its own video
                                             library, {host: "bunny", upload: {endpoint, library_id,
                                             video_id, signature, expires}} for a TUS upload (as the lesson
                                             video upload)
    POST   .../media/{asset_id}/video/attach/  {url} -> asset (the stored video is checked by a HEAD); or
                                             {video_id} for a Bunny upload -> asset (its `embed_url` is the
                                             player, `file_url` is null); 409 video_processing while Bunny is
                                             still encoding: ask again

A video file never passes through this server (50 MB, MP4, a client token of 10 minutes for one
random path under media-library/<site id>/<asset id>/). An asset is published only when it has its
file, and a video also its poster.

Same gates as the training editor (apps/training/api_owner.py): the owner session, the site found
first through site_for (404 for a missing, foreign or non-Santé site), the Billing-only rule (403),
404 everywhere while MEMBERS_ENABLED is off. Every row is read under the caller's own site, so a
foreign id is 404. Errors: 400 invalid_request with `fields`, 404 not_found, 409 media_library_full
(creating an item when the library already holds its most items, services.MAX_ITEMS), 429 (more than
30 image uploads and video tokens by one account in 10 minutes), 502 image_not_saved /
video_not_confirmed, 503 image_store_not_configured. Every answer is Cache-Control: no-store.
"""
from datetime import datetime

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.common.throttle import hit
from apps.members.api_owner import member_site

from . import services as svc

router = Router(tags=["media-library"], auth=member_site)


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid")


class AssetIn(StrictSchema):
    title: str
    asset_type: str
    category: str | None = None
    product_category: str | None = None
    crop_ratio: str | None = None
    caption: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    order: int | None = None
    active: bool | None = None


class AssetPatch(StrictSchema):
    """Only the keys sent are changed. The type of an asset cannot change."""

    title: str | None = None
    category: str | None = None
    product_category: str | None = None
    crop_ratio: str | None = None
    caption: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    order: int | None = None
    active: bool | None = None
    shared: bool | None = None  # only the content source store may send this


class PublishIn(StrictSchema):
    published_at: datetime | None = None


class ReorderIn(StrictSchema):
    ids: list[str]


class AttachIn(StrictSchema):
    """Send `url` (a video stored in the public store) or `video_id` (one uploaded to the store's own
    video library)."""

    url: str | None = None
    video_id: str | None = None


UPLOAD_LIMIT, UPLOAD_WINDOW = 30, 600  # per account: 30 image uploads and video tokens in 10 minutes


def _upload_limit(request):
    hit("media_upload", request.account.pk, limit=UPLOAD_LIMIT, window=UPLOAD_WINDOW)


def _json(data, status=200):
    response = JsonResponse(data, status=status, encoder=DjangoJSONEncoder, safe=False)
    response["Cache-Control"] = "no-store"
    return response


def _sent(payload):
    """The keys the caller sent. A null is kept: the service refuses it as a wrong value (or, for
    product_category, reads it as clear)."""
    return payload.dict(exclude_unset=True)


DELETED = {"status": "deleted"}


@router.get("/sites/{site_id}/media/")
def asset_list(request, site_id: str):
    body = {"assets": [svc.owner_out(a) for a in svc.list_for_owner(request.member_site)]}
    if svc.uses_bunny(request.member_site):
        body["video_host"] = "bunny"  # a new video goes to the store's own library (as the token route)
    return _json(body)


@router.post("/sites/{site_id}/media/")
def asset_create(request, site_id: str, payload: AssetIn):
    return _json(svc.owner_out(svc.create_asset(request.member_site, _sent(payload))), 201)


# Registered before media/{asset_id}/ so "reorder" is never read as an id.
@router.post("/sites/{site_id}/media/reorder/")
def asset_reorder(request, site_id: str, payload: ReorderIn):
    rows = svc.reorder(request.member_site, payload.ids)
    return _json({"assets": [svc.owner_out(a) for a in rows]})


@router.patch("/sites/{site_id}/media/{asset_id}/")
def asset_update(request, site_id: str, asset_id: str, payload: AssetPatch):
    return _json(svc.owner_out(svc.update_asset(request.member_site, asset_id, _sent(payload))))


@router.delete("/sites/{site_id}/media/{asset_id}/")
def asset_delete(request, site_id: str, asset_id: str):
    svc.delete_asset(request.member_site, asset_id)
    return _json(DELETED)


@router.post("/sites/{site_id}/media/{asset_id}/publish/")
def asset_publish(request, site_id: str, asset_id: str, payload: PublishIn):
    return _json(svc.owner_out(svc.publish(request.member_site, asset_id, payload.published_at)))


@router.post("/sites/{site_id}/media/{asset_id}/unpublish/")
def asset_unpublish(request, site_id: str, asset_id: str):
    return _json(svc.owner_out(svc.unpublish(request.member_site, asset_id)))


@router.post("/sites/{site_id}/media/{asset_id}/image/")
def asset_image_set(request, site_id: str, asset_id: str):
    """A form post (multipart): `file` is a JPEG, PNG or WebP of at most 10 MB; `kind` is file
    (default), poster or thumb."""
    _upload_limit(request)
    kind = request.POST.get("kind") or "file"
    row = svc.set_image(request.member_site, asset_id, kind, request.FILES.get("file"))
    return _json(svc.owner_out(row))


@router.delete("/sites/{site_id}/media/{asset_id}/image/")
def asset_thumb_clear(request, site_id: str, asset_id: str):
    return _json(svc.owner_out(svc.clear_thumb(request.member_site, asset_id)))


@router.post("/sites/{site_id}/media/{asset_id}/video/token/")
def video_token(request, site_id: str, asset_id: str):
    _upload_limit(request)
    return _json(svc.video_token(request.member_site, asset_id))


@router.post("/sites/{site_id}/media/{asset_id}/video/attach/")
def video_attach(request, site_id: str, asset_id: str, payload: AttachIn):
    if (payload.url is None) == (payload.video_id is None):
        raise svc._invalid({"url": "Send the url or the video_id of the uploaded video."})
    row = svc.attach_video(request.member_site, asset_id, url=payload.url, video_id=payload.video_id)
    return _json(svc.owner_out(row))
