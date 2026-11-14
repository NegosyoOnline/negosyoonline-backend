"""The HQ media library routes: what the HQ staff app calls to upload and manage the media members
see. No Django admin.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps; the allowed Origin,
the method and the session are checked there and nowhere else. Every route is a POST. The body
is JSON, except `media/upload/` and `media/file/set/`, which are form posts (multipart). A form
post carries one image per request (10 MB each, so it stays under the 21 MB request cap); a video
never goes through this server: the browser uploads it straight to the media store.

    media/list/        {}                          -> {assets: [asset]}  (every asset, drafts too, by `order`)
    media/get/         {id}                        -> asset
    media/upload/      form: title, asset_type (image|video), file (image only), poster (a video
                       needs one), thumb?, category?, product_category?, crop_ratio?, caption?, width?, height?,
                       duration_seconds?, active? -> asset   (created as a draft: not published)
    media/update/      {id, title?, caption?, category?, product_category?, crop_ratio?, width?,
                       height?, duration_seconds?, order?, active?} -> asset
    media/delete/      {id}                        -> {status: "deleted"}  (its files go too)
    media/reorder/     {ids: [asset ids, all of them]} -> {assets}
    media/publish/     {id, published_at?}         -> asset  (now, or the ISO time with offset sent)
    media/unpublish/   {id}                        -> asset
    media/file/set/    form: id, kind (file|thumb|poster), file -> asset  (kind file: an image asset only)
    media/video/token/ {id}                        -> {pathname, client_token, upload_url, method,
                       headers, max_bytes, content_type, expires_at, public_url}  (a video asset;
                       the browser PUTs the MP4 to upload_url with those headers within 10 minutes)
    media/video/attach/ {id, url}                  -> asset  (url = public_url of the stored video;
                       refused unless on our store, under this asset's prefix, video/mp4, <= 50 MB)
    media/file/clear/  {id}                        -> asset  (clears the thumb; it falls back to the poster or image)

An asset is {id, title, asset_type, category, category_label, product_category,
product_category_label, crop_ratio, caption, file_url, thumb_url, poster_url, width, height,
file_size, duration_seconds, published_at, is_new, active, order, published, created_at,
updated_at}. Files: images JPEG, PNG or WebP up to 10 MB; video MP4 up to 50 MB (browser to store).

Errors: 400 invalid_request with `fields`, 404 not_found, 502 image_not_saved or
video_not_confirmed, 503 image_store_not_configured. A video asset cannot be published until its
video is attached.
"""
import json

from django.core.serializers.json import DjangoJSONEncoder as _Encoder
from django.http import JsonResponse

from apps.common.errors import ApiError, error_body
from apps.operators import audit

from . import services

MAX_BODY = 40000


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
            response = JsonResponse(view(request, operator), safe=False, encoder=_Encoder)
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


def _log(operator, op, row):
    audit.record(
        operator, "media_library_authored", target_type="media_asset", target_id=row.public_id,
        detail={"op": op},
    )


def _id(data):
    value = data.get("id")
    if not isinstance(value, str) or not value:
        raise ApiError("invalid_request", services.INVALID_MSG, fields={"id": ["This is required."]})
    return value


DELETED = {"status": "deleted"}


@_route
def asset_list(request, operator):
    rows = services.MediaAsset.objects.filter(template_key=services.SANTE)
    return {"assets": [services.staff_out(a) for a in rows]}


@_route
def asset_get(request, operator):
    return services.staff_out(services.get_asset(_id(_body(request))))


@_route
def asset_upload(request, operator):
    asset = services.upload_asset(request.POST.dict(), request.FILES)
    _log(operator, "upload", asset)
    return services.staff_out(asset)


@_route
def asset_update(request, operator):
    data = _body(request)
    asset = services.update_asset(_id(data), {k: v for k, v in data.items() if k != "id"})
    _log(operator, "update", asset)
    return services.staff_out(asset)


@_route
def asset_delete(request, operator):
    public_id = _id(_body(request))
    asset = services.get_asset(public_id)
    services.delete_asset(public_id)
    _log(operator, "delete", asset)
    return DELETED


@_route
def asset_reorder(request, operator):
    rows = services.reorder(_body(request).get("ids"))
    return {"assets": [services.staff_out(a) for a in rows]}


@_route
def asset_publish(request, operator):
    data = _body(request)
    asset = services.publish(_id(data), data.get("published_at"))
    _log(operator, "publish", asset)
    return services.staff_out(asset)


@_route
def asset_unpublish(request, operator):
    asset = services.unpublish(_id(_body(request)))
    _log(operator, "unpublish", asset)
    return services.staff_out(asset)


@_route
def file_set(request, operator):
    """A form post (multipart): `id`, `kind` (file, thumb or poster) and `file`."""
    asset = services.set_file(
        request.POST.get("id", ""), request.POST.get("kind", ""), request.FILES.get("file")
    )
    _log(operator, f"{request.POST.get('kind')}_set", asset)
    return services.staff_out(asset)


@_route
def video_token(request, operator):
    return services.video_token(_id(_body(request)))


@_route
def video_attach(request, operator):
    data = _body(request)
    asset = services.attach_video(_id(data), data.get("url"))
    _log(operator, "video_attach", asset)
    return services.staff_out(asset)


@_route
def file_clear(request, operator):
    asset = services.clear_thumb(_id(_body(request)))
    _log(operator, "thumb_clear", asset)
    return services.staff_out(asset)


ROUTES = {
    "media/list/": asset_list,
    "media/get/": asset_get,
    "media/upload/": asset_upload,
    "media/update/": asset_update,
    "media/delete/": asset_delete,
    "media/reorder/": asset_reorder,
    "media/publish/": asset_publish,
    "media/unpublish/": asset_unpublish,
    "media/file/set/": file_set,
    "media/file/clear/": file_clear,
    "media/video/token/": video_token,
    "media/video/attach/": video_attach,
}
