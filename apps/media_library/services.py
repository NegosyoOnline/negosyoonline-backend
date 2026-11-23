"""Member media library: what a member reads and what the site owner writes.

Every read and write starts from one site: a member sees only the assets of their own site that
are active and published (published_at set and not in the future), and an owner finds only the
assets of their own site (another site's asset is 404, the same as a missing one).

TWCinc source for the shape: app/media_library/models.py (MediaAsset: categories, crop ratios,
size limits, the 7-day "new" badge) and the library page's filters (type, category, search,
newest/oldest). The Source filter is not ported (Santé only); the product filter uses the
catalog's own categories (apps/catalog/services.py CATEGORIES). Write the table through here.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.catalog.services import CATEGORIES as PRODUCT_CATEGORIES
from apps.common import private_files
from apps.common.errors import ApiError

from . import client_upload, store
from .models import MediaAsset

INVALID_MSG = "Some values are missing or invalid."
STORE_NOT_CONFIGURED_MSG = "Media cannot be stored yet: the media store is not configured."
NOT_SAVED_MSG = "The file could not be stored. Try again."

PAGE_SIZE = 12  # "Load 12 more"
SEARCH_MAX = 100
NEW_DAYS = 7  # TWCinc ML_NEW_BADGE_DAYS

ASSET_TYPES = {"image": "Image", "video": "Video"}
CATEGORIES = {"promo": "Product Promo", "ad": "Ad Creative", "page": "Page Post"}  # TWCinc, minus Sponsoring
CROP_RATIOS = ("4:5", "1:1", "9:16", "16:9")
PRODUCT_LABELS = {key: key.capitalize() for key in PRODUCT_CATEGORIES}
SORTS = ("newest", "oldest")
FILE_KINDS = ("file", "thumb", "poster")
FILE_MESSAGES = {
    private_files.WRONG_TYPE: {
        "image": "Upload a JPEG, PNG or WebP image.",
        "video": "Upload an MP4 video.",
    },
    private_files.TOO_LARGE: {
        "image": f"The image is too large. The limit is {store.IMAGE_MAX_BYTES // (1024 * 1024)} MB.",
        "video": f"The video is too large. The limit is {store.VIDEO_MAX_BYTES // (1024 * 1024)} MB.",
    },
}


def _not_found():
    return ApiError("not_found", "Not found.", status=404, event="object_not_found", reason="missing")


def _invalid(fields):
    return ApiError("invalid_request", INVALID_MSG, fields={k: [v] for k, v in fields.items()})


# -- member reads -------------------------------------------------------------------------------

def visible(site, now=None):
    return MediaAsset.objects.filter(
        site=site, active=True, published_at__isnull=False,
        published_at__lte=now or timezone.now(),
    )


def _thumb_key(asset):
    return asset.thumb_key or asset.poster_key or (asset.file_key if asset.asset_type == "image" else "")


def asset_out(asset, now=None):
    now = now or timezone.now()
    published = asset.published_at
    return {
        "id": asset.public_id, "title": asset.title, "asset_type": asset.asset_type,
        "category": asset.category, "category_label": CATEGORIES.get(asset.category, asset.category),
        "product_category": asset.product_category or None,
        "product_category_label": PRODUCT_LABELS.get(asset.product_category),
        "crop_ratio": asset.crop_ratio, "caption": asset.caption,
        "file_url": store.url_for(asset.file_key), "thumb_url": store.url_for(_thumb_key(asset)),
        "poster_url": store.url_for(asset.poster_key),
        "width": asset.width, "height": asset.height, "file_size": asset.file_size,
        "duration_seconds": asset.duration_seconds, "published_at": published,
        "is_new": bool(published and published >= now - timedelta(days=NEW_DAYS)),
    }


def _options(base):
    """Filter options with the number of visible assets behind each (before any filter)."""
    def counts(field, labels):
        found = dict(base.exclude(**{field: ""}).values_list(field).annotate(n=Count("pk")))
        return [{"value": key, "label": label, "count": found.get(key, 0)} for key, label in labels.items()]
    return {
        "type": counts("asset_type", ASSET_TYPES),
        "category": counts("category", CATEGORIES),
        "product_category": counts("product_category", PRODUCT_LABELS),
    }


def _choice(value, allowed, name, errors):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or value not in allowed:
        errors[name] = "Choose one of: " + ", ".join(allowed) + "."
        return ""
    return value


def list_assets(member, params, now=None):
    now = now or timezone.now()
    errors = {}
    kind = _choice(params.get("type"), ASSET_TYPES, "type", errors)
    category = _choice(params.get("category"), CATEGORIES, "category", errors)
    product = _choice(params.get("product_category"), PRODUCT_LABELS, "product_category", errors)
    sort = _choice(params.get("sort"), SORTS, "sort", errors) or "newest"
    search = params.get("search") or ""
    if not isinstance(search, str) or len(search) > SEARCH_MAX:
        errors["search"] = f"Use up to {SEARCH_MAX} characters."
        search = ""
    offset = params.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        errors["offset"] = "Use a whole number of 0 or more."
        offset = 0
    if errors:
        raise _invalid(errors)

    base = visible(member.site, now)
    rows = base
    if kind:
        rows = rows.filter(asset_type=kind)
    if category:
        rows = rows.filter(category=category)
    if product:
        rows = rows.filter(product_category=product)
    if search.strip():
        rows = rows.filter(Q(title__icontains=search.strip()) | Q(caption__icontains=search.strip()))
    rows = rows.order_by("published_at", "pk") if sort == "oldest" else rows.order_by("-published_at", "-pk")
    count = rows.count()
    page = list(rows[offset:offset + PAGE_SIZE])
    end = offset + len(page)
    return {
        "items": [asset_out(a, now) for a in page], "count": count, "offset": offset,
        "limit": PAGE_SIZE, "next_offset": end if end < count else None, "options": _options(base),
    }


# -- owner writes -------------------------------------------------------------------------------

VIDEO_FILE_MSG = (
    "A video file is not sent here. Upload it straight to the media store "
    "(video/token/), then attach it (video/attach/)."
)


def owner_out(asset):
    return {
        **asset_out(asset), "active": asset.active, "order": asset.order,
        "published": asset.published_at is not None, "shared": asset.shared,
        "copied_from": asset.copied_from or None, "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


def list_for_owner(site):
    return list(MediaAsset.objects.filter(site=site).select_related("site"))


def get_asset(site, public_id, lock=False):
    rows = MediaAsset.objects.select_related("site")
    if lock:
        rows = rows.select_for_update(of=("self",))
    asset = rows.filter(site=site, public_id=public_id).first() if isinstance(public_id, str) else None
    if asset is None:
        raise _not_found()
    return asset


def _next_order(site):
    return (MediaAsset.objects.filter(site=site).aggregate(top=Max("order"))["top"] or 0) + 1


def _whole(value, low, high):
    """An int, or None when it is not a whole number in range."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and low <= value <= high else None


def _values(data, errors, *, creating, site=None):
    out = {}
    if "shared" in data:
        from apps.sante import services as sante

        if not isinstance(data["shared"], bool):
            errors["shared"] = "Send true or false."
        elif site is None or not sante.is_content_source(site):
            errors["shared"] = "Only the content source store can share media."
        else:
            out["shared"] = data["shared"]
    if "title" in data or creating:
        title = data.get("title")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            errors["title"] = "Use 1 to 160 characters."
        else:
            out["title"] = title.strip()
    if "caption" in data:
        caption = data["caption"]
        if not isinstance(caption, str) or len(caption) > 5000:
            errors["caption"] = "Use up to 5000 characters."
        else:
            out["caption"] = caption.strip()
    for name, allowed in (("category", CATEGORIES), ("crop_ratio", CROP_RATIOS)):
        if name in data:
            if isinstance(data[name], str) and data[name] in allowed:
                out[name] = data[name]
            else:
                errors[name] = "Choose one of: " + ", ".join(allowed) + "."
    if "product_category" in data:
        value = data["product_category"]
        if value in (None, ""):
            out["product_category"] = ""
        elif isinstance(value, str) and value in PRODUCT_LABELS:
            out["product_category"] = value
        else:
            errors["product_category"] = "Choose " + ", ".join(PRODUCT_LABELS) + ", or leave it blank."
    for name, high in (("width", 20000), ("height", 20000), ("duration_seconds", 86400), ("order", 1_000_000)):
        if name in data:
            number = _whole(data[name], 0 if name != "order" else 1, high)
            if number is None:
                errors[name] = "Use a whole number" + (" of 1 or more." if name == "order" else ".")
            else:
                out[name] = number
    if "active" in data:
        if isinstance(data["active"], bool):
            out["active"] = data["active"]
        else:
            errors["active"] = "Send true or false."
    return out


def _require_store():
    if not store.configured():
        raise ApiError("image_store_not_configured", STORE_NOT_CONFIGURED_MSG, status=503)


def create_asset(site, data):
    """A new asset, always a draft and without files: the image, poster and thumbnail go to
    set_image, a video file goes to the store (video_token) and is recorded (attach_video)."""
    errors = {}
    values = _values(data, errors, creating=True)
    kind = data.get("asset_type")
    if kind not in ASSET_TYPES:
        errors["asset_type"] = "Choose image or video."
    if errors:
        raise _invalid(errors)
    with transaction.atomic():
        values.setdefault("order", _next_order(site))
        return MediaAsset.objects.create(site=site, asset_type=kind, **values)


@transaction.atomic
def update_asset(site, public_id, data):
    asset = get_asset(site, public_id, lock=True)
    errors = {}
    values = _values(data, errors, creating=False, site=site)
    if errors:
        raise _invalid(errors)
    for name, value in values.items():
        setattr(asset, name, value)
    asset.save()
    return asset


def delete_asset(site, public_id):
    with transaction.atomic():
        asset = get_asset(site, public_id, lock=True)
        keys = [asset.file_key, asset.thumb_key, asset.poster_key]
        asset.delete()
        transaction.on_commit(lambda: [store.discard(key) for key in keys if key])


@transaction.atomic
def reorder(site, ids):
    """Set `order` 1..n for every asset of the site, in the order sent."""
    rows = {a.public_id: a for a in MediaAsset.objects.select_for_update().filter(site=site)}
    if not isinstance(ids, list) or len(ids) != len(set(ids)) or set(ids) != set(rows):
        raise _invalid({"ids": "Send every item of the list once, in the order you want."})
    for position, public_id in enumerate(ids, start=1):
        row = rows[public_id]
        if row.order != position:
            MediaAsset.objects.filter(pk=row.pk).update(order=position)
            row.order = position
    return [rows[public_id] for public_id in ids]


@transaction.atomic
def publish(site, public_id, when=None):
    """Publish now, or at `when` (a time with its time zone). Publishing again keeps the time it
    already has unless `when` is sent. An image needs its file; a video needs its attached file
    and a poster."""
    asset = get_asset(site, public_id, lock=True)
    problems = {}
    if not asset.file_key:
        problems["file"] = (
            "Attach the video file before publishing." if asset.asset_type == "video"
            else "Upload the image before publishing."
        )
    if asset.asset_type == "video" and not asset.poster_key:
        problems["poster"] = "A video needs a poster image before publishing."
    if problems:
        raise _invalid(problems)
    if when is not None:
        if when.tzinfo is None or when.utcoffset() is None:
            raise _invalid({"published_at": "Use a date and time with a time zone, like 2026-11-14T09:00:00+08:00."})
        asset.published_at = when
    elif asset.published_at is None:
        asset.published_at = timezone.now()
    asset.save(update_fields=["published_at", "updated_at"])
    return asset


@transaction.atomic
def unpublish(site, public_id):
    asset = get_asset(site, public_id, lock=True)
    asset.published_at = None
    asset.save(update_fields=["published_at", "updated_at"])
    return asset


KEY_FIELD = {"file": "file_key", "thumb": "thumb_key", "poster": "poster_key"}


def set_image(site, public_id, kind, upload):
    """Store one image for an asset: `file` (an image asset's picture), `poster` (a video's) or
    `thumb` (either, optional), JPEG, PNG or WebP of at most IMAGE_MAX_BYTES. The old file goes
    after commit. Nothing is stored when anything is refused."""
    asset = get_asset(site, public_id)
    if kind not in FILE_KINDS:
        raise _invalid({"kind": "Choose file, thumb or poster."})
    if kind == "poster" and asset.asset_type != "video":
        raise _invalid({"kind": "Only a video has a poster."})
    if kind == "file" and asset.asset_type == "video":
        raise _invalid({"kind": VIDEO_FILE_MSG})
    if upload is None:
        raise _invalid({"file": "This is required."})
    try:
        content, content_type = store.check_image(upload)
    except private_files.FileRefused as refused:
        raise _invalid({"file": FILE_MESSAGES[refused.reason]["image"]}) from None
    _require_store()
    try:
        key = store.save(asset, content, content_type)
    except store.StoreError:
        raise ApiError("image_not_saved", NOT_SAVED_MSG, status=502) from None
    field = KEY_FIELD[kind]
    try:
        with transaction.atomic():
            asset = get_asset(site, public_id, lock=True)
            old = getattr(asset, field)
            setattr(asset, field, key)
            update = [field, "updated_at"]
            if kind == "file":
                asset.file_size = len(content)
                update.append("file_size")
            asset.save(update_fields=update)
            if old:
                transaction.on_commit(lambda: store.discard(old))
    except BaseException:
        store.discard(key)
        raise
    return asset


@transaction.atomic
def clear_thumb(site, public_id):
    asset = get_asset(site, public_id, lock=True)
    old = asset.thumb_key
    if old:
        asset.thumb_key = ""
        asset.save(update_fields=["thumb_key", "updated_at"])
        transaction.on_commit(lambda: store.discard(old))
    return asset


# -- video: the browser uploads straight to the store ---------------------------------------------

def _video_asset(site, public_id, lock=False):
    asset = get_asset(site, public_id, lock=lock)
    if asset.asset_type != "video":
        raise _invalid({"id": "Only a video asset has a video file."})
    return asset


def video_token(site, public_id):
    """What the owner's browser needs to PUT one MP4 straight to the store (client_upload)."""
    asset = _video_asset(site, public_id)
    if not client_upload.usable():
        raise ApiError("image_store_not_configured", STORE_NOT_CONFIGURED_MSG, status=503)
    return client_upload.upload_instructions(asset)


def attach_video(site, public_id, url):
    """Record the video the browser stored. The URL must be on our store, under this site's and
    this asset's prefix; a HEAD of it must show video/mp4 of at most VIDEO_MAX_BYTES, else the
    stored object is removed."""
    asset = _video_asset(site, public_id)
    key = client_upload.key_from_url(asset, url)
    if key is None:
        raise _invalid({"url": "This is not a video uploaded for this asset."})
    _require_store()
    try:
        kind, size = client_upload.confirm_object(key)
    except store.StoreError:
        raise ApiError("video_not_confirmed", "The uploaded video could not be found. Try again.", status=502) from None
    problem = None
    if kind != store.VIDEO_TYPE:
        problem = FILE_MESSAGES[private_files.WRONG_TYPE]["video"]
    elif size <= 0 or size > store.VIDEO_MAX_BYTES:
        problem = FILE_MESSAGES[private_files.TOO_LARGE]["video"]
    if problem:
        store.discard(key)
        raise _invalid({"url": problem})
    with transaction.atomic():
        asset = _video_asset(site, public_id, lock=True)
        old = asset.file_key
        asset.file_key, asset.file_size = key, size
        asset.save(update_fields=["file_key", "file_size", "updated_at"])
        if old and old != key:
            transaction.on_commit(lambda: store.discard(old))
    return asset
