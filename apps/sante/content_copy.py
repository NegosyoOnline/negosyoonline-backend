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
- Lesson videos (R2, Bunny only): a lesson with a ready Stream video in the source store's own
  library is copied into the TARGET store's own library: Create Video there, then this task
  downloads the source from the source library's CDN host and PUTs the bytes into the new video.
  The copy never references the source library. The item reads Copying until Bunny says every
  new video is ready (poll_videos); a video that fails makes the whole copy fail and removes it.
  YouTube, Vimeo, 744094 links, no video, or a source library that is not configured: the lesson
  arrives empty with needs_video set. Links are no longer copied.
- The target store has no usable library: a course with Stream videos is refused (503 when
  nothing else is queued, else listed as skipped with reason "video_unavailable").
"""
import logging
import os
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
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
from apps.training import stream, video
from apps.training.models import Course, Lesson, Module

logger = logging.getLogger(__name__)

COURSE, MEDIA = "course", "media"
KINDS = (COURSE, MEDIA)
NOT_COPIED, COPYING, COPIED, FAILED = "not_copied", "copying", "copied", "failed"
MAX_IDS = 100
STALE_AFTER = timedelta(hours=2)  # a copy still "copying" after this reads failed (videos take a while)
POLL_EVERY = 20  # seconds between checks of the new videos
NOT_OFFERED_MSG = "Some of these items are not offered."
COPY_FAILED_MSG = "The files could not be copied."
GONE_MSG = "The item is no longer offered."
STORE_MSG = "The media store is not configured."
VIDEO_FAILED_MSG = "A video could not be copied."


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
    """What a copy brings in stored video: the Stream size of its ready videos. Course and lesson
    pictures store no size, so they add nothing."""
    modules = Module.objects.filter(course=course, active=True)
    return Lesson.objects.filter(
        module__in=modules, active=True, stream_status=video.READY
    ).aggregate(total=Sum("stream_bytes"))["total"] or 0


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
        target_ready = video.configured_for(site)
        refused = False
        for source_id in wanted:
            state, _ = _state(site, kind, latest.get(source_id))
            if state == COPYING:
                skipped.append({"kind": kind, "id": source_id, "reason": "copying"})
            elif state == COPIED and not again:
                skipped.append({"kind": kind, "id": source_id, "reason": "copied"})
            elif kind == COURSE and not target_ready and _has_stream_videos(by_id[source_id]):
                skipped.append({"kind": kind, "id": source_id, "reason": "video_unavailable"})
                refused = True
            else:
                row = SanteContentCopy.objects.create(site=site, kind=kind, source_public_id=source_id)
                queued.append({"kind": kind, "id": source_id, "copy_state": COPYING})
                transaction.on_commit(lambda pk=row.pk: _dispatch(pk))
    if refused and not queued:
        raise video.not_available()
    return {"queued": queued, "skipped": skipped}


def _stream_lessons(course):
    """The active lessons of a source course whose Stream video is ready and whose library is set."""
    modules = Module.objects.filter(course=course, active=True)
    found = []
    for lesson in Lesson.objects.filter(module__in=modules, active=True).order_by("pk"):
        if video.has_ready_stream(lesson) and _source_library(lesson) is not None:
            found.append(lesson)
    return found


def _has_stream_videos(course):
    return bool(_stream_lessons(course))


def _source_library(lesson):
    """The source lesson's library, or None when it has none set (the lesson arrives empty)."""
    try:
        return stream.library_by_id(lesson.stream_library_id)
    except stream.NotConfigured:
        return None


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
        self.videos = []  # (library id, video id) created in a target library

    def copy(self, src_key, dest_key):
        if not src_key:
            return ""
        self.keys.append(media.copy(src_key, dest_key))
        return dest_key

    def discard(self):
        for key in self.keys:
            media.discard(key)
        for library_id, guid in self.videos:
            video.discard_if_unreferenced(library_id, guid)  # not named by any lesson: deleted


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
        _fail(row, stream.NOT_AVAILABLE_MSG if isinstance(exc, _VideoUnavailable) else COPY_FAILED_MSG)
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


def _copy_video(lesson, source_library, target_library, staged):
    """Make a new video in the target library from the source lesson's video and return its id.
    The source is downloaded from its library's CDN host and PUT into the new video."""
    guid = stream.create_video(target_library, lesson.title)
    staged.videos.append((target_library.id, guid))
    source, length = stream.download_source(source_library, lesson.stream_video_id)
    try:
        stream.upload_bytes(target_library, guid, source, length)
    finally:
        source.close()
    return guid


def _copy_course(row, src, staged):
    site = row.site
    image = staged.copy(src.image_key, _training_key(src.image_key))
    modules = list(Module.objects.filter(course=src, active=True).order_by("order", "pk"))
    lessons = {
        m.pk: list(Lesson.objects.filter(module=m, active=True).order_by("order", "pk")) for m in modules
    }
    # Every lesson picture and video is copied before any row is written, so a failure leaves no
    # rows (the new files and videos are removed by the caller).
    lesson_images = {
        lesson.pk: staged.copy(lesson.image_key, _training_key(lesson.image_key))
        for items in lessons.values() for lesson in items
    }
    new_videos = {}
    wanted = [lesson for items in lessons.values() for lesson in items if video.has_ready_stream(lesson)]
    target_library = None
    for lesson in wanted:
        source_library = _source_library(lesson)
        if source_library is None:
            continue  # the source library is not set: this lesson arrives as "Add your video"
        if target_library is None:
            try:
                target_library = stream.library_for_site(site)
            except stream.NotConfigured:
                raise _VideoUnavailable() from None
        new_videos[lesson.pk] = (target_library.id, _copy_video(lesson, source_library, target_library, staged))
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
                library_id, guid = new_videos.get(lesson.pk, (None, ""))
                Lesson.objects.create(
                    module=new_module, title=lesson.title, duration=lesson.duration,
                    description=lesson.description, video_url="",
                    image_key=lesson_images[lesson.pk], needs_video=not guid, order=lesson.order, active=True,
                    stream_library_id=library_id, stream_video_id=guid,
                    stream_status=video.PROCESSING if guid else "",
                )
        if new_videos:  # stays Copying until Bunny says every new video is ready
            held = SanteContentCopy.objects.filter(pk=row.pk, state=SanteContentCopy.COPYING).update(
                target_public_id=course.public_id
            )
            if not held:
                raise RuntimeError("The copy request is no longer open.")
            transaction.on_commit(lambda: _schedule_poll(row.pk, 0))
        else:
            _finish_rows(row, course.public_id)
    return course.public_id


class _VideoUnavailable(Exception):
    """The target store has no usable Stream library."""


def _schedule_poll(copy_pk, attempt):
    from apps.sante.tasks import poll_videos

    try:
        poll_videos.apply_async((copy_pk, attempt), countdown=POLL_EVERY)
    except Exception:  # noqa: BLE001 - the queue is down: say so on the item instead of hanging
        logger.warning("A video copy check could not be queued.")
        _abort_video_copy(copy_pk, "The copy could not be finished. Try again.")


def _abort_video_copy(copy_pk, message):
    """Fail a copy whose course rows exist: remove the course (and, through the delete guards,
    its pictures and new videos), then close the row as failed."""
    row = SanteContentCopy.objects.select_related("site").filter(pk=copy_pk).first()
    if row is None or row.state != SanteContentCopy.COPYING:
        return
    if row.target_public_id:
        try:
            training.delete_course(row.site, row.target_public_id)
        except ApiError:
            pass  # the owner already deleted it
    _finish(copy_pk, SanteContentCopy.FAILED, error=message)


def poll_videos(copy_pk, attempt=0):
    """Check the new videos of a Copying course against Bunny. Everything ready: Copied. Any
    failed, or the copy outlived STALE_AFTER: the copy is removed and reads failed. Otherwise
    look again shortly. Never raises."""
    row = SanteContentCopy.objects.select_related("site").filter(pk=copy_pk).first()
    if row is None or row.state != SanteContentCopy.COPYING or not row.target_public_id:
        return
    if row.requested_at < timezone.now() - STALE_AFTER:
        return _abort_video_copy(copy_pk, COPY_FAILED_MSG)
    course = Course.objects.filter(site=row.site, public_id=row.target_public_id).first()
    if course is None:
        return _finish(copy_pk, SanteContentCopy.FAILED, error=GONE_MSG)
    waiting = False
    for lesson in Lesson.objects.filter(module__course=course).exclude(stream_video_id=""):
        if lesson.stream_status == video.READY:
            continue
        try:
            answer = stream.get_video(stream.library_by_id(lesson.stream_library_id), lesson.stream_video_id)
        except (stream.StreamError, stream.NotConfigured):
            waiting = True  # try again shortly; the age limit ends it
            continue
        status = stream.status_name(answer["status"])
        if status == video.FAILED:
            return _abort_video_copy(copy_pk, VIDEO_FAILED_MSG)
        if status == video.READY:
            Lesson.objects.filter(pk=lesson.pk).update(
                stream_status=video.READY, stream_bytes=answer["storage_size"]
            )
        else:
            waiting = True
    if waiting:
        return _schedule_poll(copy_pk, attempt + 1)
    _finish_rows_open(copy_pk, course.public_id)


def _finish_rows_open(copy_pk, target):
    SanteContentCopy.objects.filter(pk=copy_pk, state=SanteContentCopy.COPYING).update(
        state=SanteContentCopy.COPIED, target_public_id=target, error="", finished_at=timezone.now()
    )


def _finish_rows(row, target):
    updated = SanteContentCopy.objects.filter(pk=row.pk, state=SanteContentCopy.COPYING).update(
        state=SanteContentCopy.COPIED, target_public_id=target, error="", finished_at=timezone.now()
    )
    if not updated:  # the row was closed meanwhile (stale): undo everything
        raise RuntimeError("The copy request is no longer open.")
