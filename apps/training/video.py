"""A lesson's Bunny Stream video: upload, refresh, remove, how it reads, and the delete guard.

Each store uploads into its own library (stream.py). The lesson names the video by the pair
(stream_library_id, stream_video_id). A replacement upload waits in the stream_pending_* columns
while the lesson keeps playing what it has (a ready Stream video or a legacy link); when the
replacement is ready it takes over and the previous Stream video is discarded through the guard.
Responses carry the library id and the upload signature only, never the key or its name.
"""
import logging

from django.db import transaction
from django.db.models import Q

from apps.common.errors import ApiError

from . import stream
from .models import Lesson

logger = logging.getLogger(__name__)

UPLOADING, PROCESSING, READY, FAILED = "uploading", "processing", "ready", "failed"


def not_available():
    return ApiError("video_not_available", stream.NOT_AVAILABLE_MSG, status=503)


def has_ready_stream(lesson):
    return bool(lesson.stream_video_id and lesson.stream_library_id and lesson.stream_status == READY)


def _has_playable(lesson):
    return has_ready_stream(lesson) or bool(lesson.video_url)


def video_out(lesson):
    """{kind: stream|legacy|none, status, embed_url, needs_video}. The status is the replacement's
    while one is on its way, else the video's own; embed_url is set only for a ready Stream video."""
    pending = lesson.stream_pending_status if lesson.stream_pending_video_id else ""
    if lesson.stream_video_id:
        kind, status = "stream", pending or lesson.stream_status
    elif lesson.video_url:
        kind, status = "legacy", pending or READY
    elif pending:
        kind, status = "none", pending
    else:
        kind, status = "none", ""
    embed = stream.embed_url(lesson.stream_library_id, lesson.stream_video_id) if has_ready_stream(lesson) else None
    return {
        "kind": kind, "status": status, "embed_url": embed,
        "needs_video": bool(lesson.needs_video and not lesson.stream_video_id),
    }


def legacy_url_for_member(lesson):
    """The legacy link a member's page may play: only while no ready Stream video replaces it."""
    return None if has_ready_stream(lesson) else (lesson.video_url or None)


# -- the delete guard ---------------------------------------------------------------------------

def pairs_of(lesson):
    """The Stream videos a lesson row names: [(library id, video id)]."""
    out = []
    if lesson.stream_library_id and lesson.stream_video_id:
        out.append((lesson.stream_library_id, lesson.stream_video_id))
    if lesson.stream_pending_library_id and lesson.stream_pending_video_id:
        out.append((lesson.stream_pending_library_id, lesson.stream_pending_video_id))
    return out


def is_referenced(library_id, video_id):
    """True when any lesson, of any site, still names this video."""
    return Lesson._base_manager.filter(
        Q(stream_library_id=library_id, stream_video_id=video_id)
        | Q(stream_pending_library_id=library_id, stream_pending_video_id=video_id)
    ).exists()


def discard_if_unreferenced(library_id, video_id):
    """Delete a Stream video unless a lesson still names it, with the key of the library it lives
    in. Run after the row change has committed. Never raises; when unsure the video is kept."""
    if not library_id or not video_id:
        return
    try:
        if is_referenced(library_id, video_id):
            return
        library = stream.library_by_id(library_id)
        stream.delete_video(library, video_id)
    except Exception:  # noqa: BLE001 - a failed clean-up never hides the change that led to it
        logger.warning("A Stream video clean-up did not complete.")


def discard_pairs_on_commit(pairs):
    pairs = [p for p in pairs if p[0] and p[1]]
    if pairs:
        transaction.on_commit(lambda: [discard_if_unreferenced(*p) for p in pairs])


# -- owner actions ------------------------------------------------------------------------------

def _lesson(site, public_id):
    from . import services

    return services.get_lesson(site, public_id)


def _locked_lesson(site, public_id):
    lesson = (
        Lesson.objects.select_for_update().filter(module__course__site=site, public_id=public_id).first()
    )
    if lesson is None:
        from . import services

        raise services._not_found()
    return lesson


def _library(site):
    try:
        return stream.library_for_site(site)
    except stream.NotConfigured:
        raise not_available() from None


def start_upload(site, lesson_id):
    """Create the video in the store's own library and sign a direct upload for the browser."""
    lesson = _lesson(site, lesson_id)
    library = _library(site)
    try:
        guid = stream.create_video(library, lesson.title)
    except stream.StreamError:
        raise ApiError("video_failed", "The video could not be started. Try again.", status=502) from None
    try:
        with transaction.atomic():
            lesson = _locked_lesson(site, lesson_id)
            old = []
            if _has_playable(lesson):
                old += [(lesson.stream_pending_library_id, lesson.stream_pending_video_id)]
                lesson.stream_pending_library_id = library.id
                lesson.stream_pending_video_id = guid
                lesson.stream_pending_status = UPLOADING
            else:
                old += [(lesson.stream_library_id, lesson.stream_video_id),
                        (lesson.stream_pending_library_id, lesson.stream_pending_video_id)]
                lesson.stream_library_id = library.id
                lesson.stream_video_id = guid
                lesson.stream_status = UPLOADING
                lesson.stream_bytes = 0
                lesson.stream_pending_library_id = None
                lesson.stream_pending_video_id = ""
                lesson.stream_pending_status = ""
                lesson.needs_video = False
            lesson.save()
            discard_pairs_on_commit(old)
    except BaseException:
        discard_if_unreferenced(library.id, guid)
        raise
    return {"upload": stream.tus_signature(library, guid), "video": video_out(lesson)}


def refresh(site, lesson_id):
    """Ask Bunny about a video still on its way and record the answer."""
    lesson = _lesson(site, lesson_id)
    target = _in_flight(lesson)
    if target is None:
        return video_out(lesson)
    which, library_id, guid = target
    try:
        library = stream.library_by_id(library_id)
    except stream.NotConfigured:
        raise not_available() from None
    try:
        answer = stream.get_video(library, guid)
    except stream.StreamError:
        raise ApiError("video_failed", "The video status could not be read. Try again.", status=502) from None
    status = stream.status_name(answer["status"])
    with transaction.atomic():
        lesson = _locked_lesson(site, lesson_id)
        if _in_flight(lesson) != target:  # changed meanwhile: answer with what is there now
            return video_out(lesson)
        _apply(lesson, which, status, answer["storage_size"])
    return video_out(lesson)


def _in_flight(lesson):
    """("pending"|"current", library id, video id) of the video still uploading or processing."""
    if lesson.stream_pending_video_id and lesson.stream_pending_status in (UPLOADING, PROCESSING):
        return "pending", lesson.stream_pending_library_id, lesson.stream_pending_video_id
    if (
        lesson.stream_video_id and not lesson.stream_pending_video_id
        and lesson.stream_status in (UPLOADING, PROCESSING)
    ):
        return "current", lesson.stream_library_id, lesson.stream_video_id
    return None


def _apply(lesson, which, status, size):
    if which == "current":
        lesson.stream_status = status
        if status == READY:
            lesson.stream_bytes = size
            lesson.video_url = ""
            lesson.needs_video = False
        lesson.save()
        return
    if status != READY:
        lesson.stream_pending_status = status
        lesson.save()
        return
    old = (lesson.stream_library_id, lesson.stream_video_id)
    lesson.stream_library_id = lesson.stream_pending_library_id
    lesson.stream_video_id = lesson.stream_pending_video_id
    lesson.stream_status = READY
    lesson.stream_bytes = size
    lesson.stream_pending_library_id = None
    lesson.stream_pending_video_id = ""
    lesson.stream_pending_status = ""
    lesson.video_url = ""
    lesson.needs_video = False
    lesson.save()
    discard_pairs_on_commit([old])


def remove(site, lesson_id):
    """Take the Stream video (and any replacement on its way) off the lesson and delete it."""
    _lesson(site, lesson_id)
    with transaction.atomic():
        lesson = _locked_lesson(site, lesson_id)
        pairs = pairs_of(lesson)
        lesson.stream_library_id = None
        lesson.stream_video_id = ""
        lesson.stream_status = ""
        lesson.stream_bytes = 0
        lesson.stream_pending_library_id = None
        lesson.stream_pending_video_id = ""
        lesson.stream_pending_status = ""
        lesson.save()
        discard_pairs_on_commit(pairs)
    return video_out(lesson)


# -- the staff command's rows -------------------------------------------------------------------

def library_summary(row):
    """What the command may print about a library row: everything except the key."""
    return {
        "site": row.site.public_id, "library_id": row.library_id, "cdn_host": row.cdn_host,
        "key_ref": row.key_ref, "key_variable_present": stream.key_present(row.key_ref),
    }


def configured_for(site):
    """True when the store has a library row and its key variable is set."""
    try:
        stream.library_for_site(site)
    except stream.NotConfigured:
        return False
    return True
