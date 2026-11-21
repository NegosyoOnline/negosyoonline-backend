"""Copy the content source store's shared courses and media into another Santé store.

A distributor asks for a copy from the dash; the request is written as a SanteContentCopy row and
a Celery task does the file copying (apps/sante/tasks.py), never the web request. Rules:

- Only items the source store offers are copied: courses that are active, not archived and
  shared; media that is active, published (not in the future) and shared. The source store itself
  and a store with no source set see nothing.
- A copy is written like an upload: the target's own rows, its own new file keys, the source's
  file_size. It arrives switched off (course) or unpublished (media).
- The newest SanteContentCopy row per (site, kind, source id) is that item's state. A copied
  state only counts while the target row still exists; deleting the copy reads "not_copied".
- Copying again (`again`) makes a new, separate copy. Nothing is updated when the source changes.
- A failed copy removes the files it stored and writes no row.
- A lesson whose video is a Bunny (iframe.mediadelivery.net) link is copied with an empty video
  and needs_video set; YouTube and Vimeo links are copied as links.
"""
import logging
import os
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from apps.catalog import media
from apps.common.errors import ApiError
from apps.media_library import store as media_store
from apps.media_library.models import MediaAsset
from apps.sante import services
from apps.sante.models import SanteContentCopy
from apps.sites import templates
from apps.sites.models import Site
from apps.training import services as training
from apps.training.models import Course, Lesson, Module

logger = logging.getLogger(__name__)

COURSE, MEDIA = "course", "media"
KINDS = (COURSE, MEDIA)
NOT_COPIED, COPYING, COPIED, FAILED = "not_copied", "copying", "copied", "failed"
MAX_IDS = 100
STALE_AFTER = timedelta(minutes=30)  # a copy still "copying" after this reads failed
BUNNY_HOST = "iframe.mediadelivery.net"
NOT_OFFERED_MSG = "Some of these items are not offered."
COPY_FAILED_MSG = "The files could not be copied."
GONE_MSG = "The item is no longer offered."
STORE_MSG = "The media store is not configured."


# -- what is offered ---------------------------------------------------------------------------

def source_for(site):
    """The source store when `site` may copy from it, else None (no source, or `site` is it)."""
    source = services.content_source_site()
    if source is None or source.pk == site.pk:
        return None
    return source


def offered_courses(source):
    return Course.objects.filter(
        site=source, shared=True, active=True, archived_at__isnull=True
    ).order_by("order", "pk")


def offered_media(source, now=None):
    return MediaAsset.objects.filter(
        site=source, shared=True, active=True, published_at__isnull=False,
        published_at__lte=now or timezone.now(),
    ).order_by("order", "pk")


def _latest(site, kind):
    """The newest copy row of each source item of this kind, for this site."""
    latest = {}
    for row in SanteContentCopy.objects.filter(site=site, kind=kind).order_by("id"):
        latest[row.source_public_id] = row
    return latest


def _target_exists(site, kind, public_id):
    model = Course if kind == COURSE else MediaAsset
    return bool(public_id) and model.objects.filter(site=site, public_id=public_id).exists()


def _state(site, kind, row, now=None):
    """(copy_state, copy_id) for one item from its newest copy row."""
    if row is None:
        return NOT_COPIED, None
    if row.state == SanteContentCopy.COPYING:
        if row.requested_at < (now or timezone.now()) - STALE_AFTER:
            return FAILED, None
        return COPYING, None
    if row.state == SanteContentCopy.FAILED:
        return FAILED, None
    if _target_exists(site, kind, row.target_public_id):
        return COPIED, row.target_public_id
    return NOT_COPIED, None


def _course_bytes(course):
    # Course and lesson pictures store no size, so only known sizes count: none for a course.
    return 0


def list_for(site):
    """The body of GET shared-content/ for this site."""
    empty = {"is_source": False, "source_name": "", "courses": [], "media": []}
    if site.template != templates.SANTE:
        return empty
    source = services.content_source_site()
    if source is None:
        return empty
    if source.pk == site.pk:
        return {**empty, "is_source": True, "source_name": source.display_name}
    now = timezone.now()
    courses, assets = list(offered_courses(source)), list(offered_media(source, now))
    latest_course, latest_media = _latest(site, COURSE), _latest(site, MEDIA)
    modules = _counts(courses)
    out_courses = []
    for course in courses:
        state, copy_id = _state(site, COURSE, latest_course.get(course.public_id), now)
        module_count, lesson_count = modules.get(course.pk, (0, 0))
        out_courses.append({
            "id": course.public_id, "title": course.title, "image_url": media.url_for(course.image_key),
            "module_count": module_count, "lesson_count": lesson_count, "bytes": _course_bytes(course),
            "copy_state": state, "copy_id": copy_id,
        })
    out_media = []
    for asset in assets:
        state, copy_id = _state(site, MEDIA, latest_media.get(asset.public_id), now)
        out_media.append({
            "id": asset.public_id, "title": asset.title, "asset_type": asset.asset_type,
            "thumb_url": media_store.url_for(_thumb_key(asset)), "bytes": asset.file_size,
            "copy_state": state, "copy_id": copy_id,
        })
    return {
        "is_source": False, "source_name": source.display_name,
        "courses": out_courses, "media": out_media,
    }


def _thumb_key(asset):
    return asset.thumb_key or asset.poster_key or (asset.file_key if asset.asset_type == "image" else "")


def _counts(courses):
    """{course pk: (active module count, active lesson count)}: what a copy brings."""
    counts = {}
    for course in courses:
        active_modules = Module.objects.filter(course=course, active=True)
        counts[course.pk] = (
            active_modules.count(),
            Lesson.objects.filter(module__in=active_modules, active=True).count(),
        )
    return counts


# -- the request -------------------------------------------------------------------------------

def _invalid(field, message):
    return ApiError("invalid_request", "Some values are missing or invalid.", fields={field: [message]})


def request_copy(site, *, kind, ids=None, every=False, again=False):
    """Queue copies of offered items. Answers {"queued": [...], "skipped": [...]} at once; the
    files are copied later by the task. `every` copies all offered items not yet copied."""
    if kind not in KINDS:
        raise _invalid("kind", "Choose course or media.")
    if every is not True and not (isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids)):
        raise _invalid("ids", "Send a list of item ids, or all: true.")
    if isinstance(ids, list) and len(ids) > MAX_IDS:
        raise _invalid("ids", f"Send at most {MAX_IDS} items at a time.")
    if site.template != templates.SANTE:
        raise _invalid("kind", NOT_OFFERED_MSG)
    queued, skipped = [], []
    with transaction.atomic():
        Site.objects.select_for_update().get(pk=site.pk)  # one request at a time per store
        source = source_for(site)
        rows = []
        if source is not None:
            rows = list(offered_courses(source) if kind == COURSE else offered_media(source))
        by_id = {row.public_id: row for row in rows}
        if every is True:
            wanted = list(by_id)
        else:
            wanted = list(dict.fromkeys(ids))
            if any(i not in by_id for i in wanted):
                raise _invalid("ids", NOT_OFFERED_MSG)
        latest = _latest(site, kind)
        for source_id in wanted:
            state, _ = _state(site, kind, latest.get(source_id))
            if state == COPYING:
                skipped.append({"kind": kind, "id": source_id, "reason": "copying"})
            elif state == COPIED and not again:
                skipped.append({"kind": kind, "id": source_id, "reason": "copied"})
            else:
                row = SanteContentCopy.objects.create(site=site, kind=kind, source_public_id=source_id)
                queued.append({"kind": kind, "id": source_id, "copy_state": COPYING})
                transaction.on_commit(lambda pk=row.pk: _dispatch(pk))
    return {"queued": queued, "skipped": skipped}


def _dispatch(copy_pk):
    from apps.sante.tasks import copy_content

    try:
        copy_content.delay(copy_pk)
    except Exception:  # noqa: BLE001 - the queue is down: say so on the item instead of hanging
        logger.warning("A content copy could not be queued.")
        _finish(copy_pk, SanteContentCopy.FAILED, error="The copy could not be started. Try again.")


def _finish(copy_pk, state, *, target="", error=""):
    SanteContentCopy.objects.filter(pk=copy_pk, state=SanteContentCopy.COPYING).update(
        state=state, target_public_id=target, error=error[:200], finished_at=timezone.now()
    )


# -- the copy itself (runs in the Celery task) ---------------------------------------------------

class _Staged:
    """New files stored so far: removed again unless the rows were written."""

    def __init__(self):
        self.keys = []

    def copy(self, src_key, dest_key):
        if not src_key:
            return ""
        self.keys.append(media.copy(src_key, dest_key))
        return dest_key

    def discard(self):
        for key in self.keys:
            media.discard(key)


def _ext(key):
    return os.path.splitext(key)[1][:8]


def _training_key(src_key):
    return f"training/{secrets.token_hex(16)}{_ext(src_key)}" if src_key else ""


def run_copy(copy_pk):
    """Do one queued copy. Never raises: the row reads copied or failed afterwards."""
    row = SanteContentCopy.objects.select_related("site").filter(pk=copy_pk).first()
    if row is None or row.state != SanteContentCopy.COPYING:
        return None
    staged = _Staged()
    try:
        source = source_for(row.site)
        if source is None:
            return _fail(row, GONE_MSG)
        if not media.configured():
            return _fail(row, STORE_MSG)
        if row.kind == COURSE:
            item = offered_courses(source).filter(public_id=row.source_public_id).first()
            if item is None:
                return _fail(row, GONE_MSG)
            target = _copy_course(row, item, staged)
        else:
            item = offered_media(source).filter(public_id=row.source_public_id).first()
            if item is None:
                return _fail(row, GONE_MSG)
            target = _copy_media(row, item, staged)
    except BaseException as exc:  # noqa: BLE001 - nothing half-made stays behind
        staged.discard()
        logger.warning("A content copy failed (%s).", type(exc).__name__)
        _fail(row, COPY_FAILED_MSG)
        if not isinstance(exc, Exception):
            raise
        return None
    return target


def _fail(row, message):
    _finish(row.pk, SanteContentCopy.FAILED, error=message)
    return None


def _copy_media(row, src, staged):
    site = row.site
    asset = MediaAsset(
        site=site, title=src.title, asset_type=src.asset_type, category=src.category,
        product_category=src.product_category, crop_ratio=src.crop_ratio, caption=src.caption,
        width=src.width, height=src.height, file_size=src.file_size,
        duration_seconds=src.duration_seconds, active=True, published_at=None,
        shared=False, copied_from=src.public_id,
    )
    prefix = media_store.asset_prefix(asset)

    def dest(key):
        return f"{prefix}{secrets.token_hex(16)}{_ext(key)}"

    asset.file_key = staged.copy(src.file_key, dest(src.file_key))
    asset.thumb_key = staged.copy(src.thumb_key, dest(src.thumb_key))
    asset.poster_key = staged.copy(src.poster_key, dest(src.poster_key))
    with transaction.atomic():
        asset.order = (
            MediaAsset.objects.filter(site=site).order_by("-order").values_list("order", flat=True).first() or 0
        ) + 1
        asset.save()
        _finish_rows(row, asset.public_id)
    return asset.public_id


def _is_bunny(url):
    try:
        return (urlsplit(url).hostname or "").lower() == BUNNY_HOST
    except ValueError:
        return False


def _copy_course(row, src, staged):
    site = row.site
    image = staged.copy(src.image_key, _training_key(src.image_key))
    modules = list(Module.objects.filter(course=src, active=True).order_by("order", "pk"))
    lessons = {
        m.pk: list(Lesson.objects.filter(module=m, active=True).order_by("order", "pk")) for m in modules
    }
    # Every lesson picture is copied before any row is written, so a failure leaves no rows.
    lesson_images = {
        lesson.pk: staged.copy(lesson.image_key, _training_key(lesson.image_key))
        for items in lessons.values() for lesson in items
    }
    with transaction.atomic():
        course = Course.objects.create(
            site=site, title=src.title, description=src.description, image_key=image,
            slug=training._unique_slug(site, src.slug), order=training._next_order(Course.objects.filter(site=site)),
            active=False, shared=False, copied_from=src.public_id,
        )
        for module in modules:
            new_module = Module.objects.create(
                course=course, title=module.title, order=module.order, active=True
            )
            for lesson in lessons[module.pk]:
                bunny = bool(lesson.video_url) and _is_bunny(lesson.video_url)
                Lesson.objects.create(
                    module=new_module, title=lesson.title, duration=lesson.duration,
                    description=lesson.description, video_url="" if bunny else lesson.video_url,
                    image_key=lesson_images[lesson.pk], needs_video=bunny, order=lesson.order, active=True,
                )
        _finish_rows(row, course.public_id)
    return course.public_id


def _finish_rows(row, target):
    updated = SanteContentCopy.objects.filter(pk=row.pk, state=SanteContentCopy.COPYING).update(
        state=SanteContentCopy.COPIED, target_public_id=target, error="", finished_at=timezone.now()
    )
    if not updated:  # the row was closed meanwhile (stale): undo everything
        raise RuntimeError("The copy request is no longer open.")
