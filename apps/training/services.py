"""Member training: what a member reads and does, and what the site owner writes.

Every read and write starts from one site: a course, module, lesson or live training of another
site is never found (404, the same answer as a missing id). Percent is computed from the
active lessons, so there is no progress rollup to keep right. Write the tables through here.

TWCinc source for the shape: app/university/models.py (Course, Module, Lesson, LessonProgress,
the active/archived rule and next/previous lesson), trimmed as the plan sets out. The sequential
unlock is new: TWCinc opens every lesson of a course the member may enter.

The sequence of a course is its active lessons of active modules, in module order and then
lesson order. A lesson is open when it is the first of the sequence or the one before it is
complete. A lesson completed before it was switched off stays in the database and counts for
nothing while it is off.
"""
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog import media
from apps.catalog.services import IMAGE_MESSAGES
from apps.common import private_files
from apps.common.errors import ApiError
from apps.members import notices

from .models import Course, Lesson, LessonProgress, LiveTraining, Module

IMAGE_FOLDER = "training"
LIVE_PATH = "/dashboard/training/"
INVALID_MSG = "Some values are missing or invalid."
IMAGE_NOT_CONFIGURED_MSG = "Pictures cannot be stored yet: the image store is not configured."
IMAGE_NOT_SAVED_MSG = "The picture could not be stored. Try again."
LOCKED_MSG = "Finish the lesson before this one first."

# -- video allowlist ----------------------------------------------------------------------------
# https embed URLs of YouTube, Vimeo and Bunny Stream only. Anything else (a watch page, a file, a
# script, another host) is refused, so a lesson page can only ever put these three players in an
# iframe.
_YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "www.youtube-nocookie.com", "youtube-nocookie.com"}
_YOUTUBE_PATH = re.compile(r"^/embed/[A-Za-z0-9_-]{11}$")
_VIMEO_PATH = re.compile(r"^/video/[0-9]{1,15}$")
_BUNNY_PATH = re.compile(r"^/(embed|play)/[0-9]{1,10}/[A-Za-z0-9-]{8,64}$")
VIDEO_MESSAGE = (
    "Use an https embed link from YouTube (youtube.com/embed/...), Vimeo (player.vimeo.com/video/...) "
    "or Bunny Stream (iframe.mediadelivery.net/embed/...)."
)
URL_MAX = 500


def video_url_ok(value):
    if not isinstance(value, str) or len(value) > URL_MAX or value != value.strip():
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or port is not None or parts.username or parts.password or parts.fragment:
        return False
    host = (parts.hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        return bool(_YOUTUBE_PATH.match(parts.path))
    if host == "player.vimeo.com":
        return bool(_VIMEO_PATH.match(parts.path))
    if host == "iframe.mediadelivery.net":
        return bool(_BUNNY_PATH.match(parts.path))
    return False


def join_url_ok(value):
    if not isinstance(value, str) or len(value) > URL_MAX or value != value.strip():
        return False
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname) and not (parts.username or parts.password)


# -- errors -------------------------------------------------------------------------------------

def _not_found():
    return ApiError("not_found", "Not found.", status=404, event="object_not_found", reason="missing")


def _invalid(fields):
    return ApiError("invalid_request", INVALID_MSG, fields={k: [v] for k, v in fields.items()})


def _locked():
    return ApiError("lesson_locked", LOCKED_MSG, status=409)


# -- member reads -------------------------------------------------------------------------------

def visible_courses(site):
    """Active, unarchived courses of the site."""
    return Course.objects.filter(site=site, active=True, archived_at__isnull=True)


def _course_lessons(course_ids):
    """{course pk: [lesson, ...]} in sequence order, active lessons of active modules only."""
    rows = (
        Lesson.objects.filter(module__course_id__in=course_ids, module__active=True, active=True)
        .select_related("module")
        .order_by("module__order", "module__pk", "order", "pk")
    )
    found = {pk: [] for pk in course_ids}
    for lesson in rows:
        found[lesson.module.course_id].append(lesson)
    return found


def _completed_ids(member, lessons):
    return set(
        LessonProgress.objects.filter(
            member=member, lesson__in=lessons, completed_at__isnull=False
        ).values_list("lesson_id", flat=True)
    )


def _percent(done, total):
    return 0 if total == 0 else (done * 100) // total


def _open_flags(lessons, done):
    """{lesson pk: locked?} - a lesson is open when the one before it is complete."""
    flags, previous_done = {}, True
    for lesson in lessons:
        flags[lesson.pk] = not previous_done
        previous_done = lesson.pk in done
    return flags


def _lesson_ref(lesson):
    return None if lesson is None else {"id": lesson.public_id, "title": lesson.title}


def course_out(course, lessons, done):
    count = len(lessons)
    finished = sum(1 for lesson in lessons if lesson.pk in done)
    return {
        "id": course.public_id, "slug": course.slug, "title": course.title,
        "description": course.description, "image_url": media.url_for(course.image_key),
        "order": course.order, "coming_soon": count == 0,
        "completed_lessons": finished, "total_lessons": count, "percent": _percent(finished, count),
    }


def _next_incomplete(lessons, done):
    return next((lesson for lesson in lessons if lesson.pk not in done), None)


def list_courses(member):
    """Every visible course with its percent, and where to pick up: the first unfinished lesson of
    the course the member touched last (None when they have not started or have finished all)."""
    courses = list(visible_courses(member.site))
    sequences = _course_lessons([c.pk for c in courses])
    done = _completed_ids(member, [lesson for lessons in sequences.values() for lesson in lessons])
    out = [course_out(c, sequences[c.pk], done) for c in courses]
    resume = None
    by_pk = {c.pk: c for c in courses}
    touched = (
        LessonProgress.objects.filter(member=member, lesson__module__course_id__in=by_pk)
        .order_by("-started_at", "-pk").select_related("lesson__module")
    )
    seen = set()
    for row in touched:
        course = by_pk[row.lesson.module.course_id]
        if course.pk in seen:
            continue
        seen.add(course.pk)
        lesson = _next_incomplete(sequences[course.pk], done)
        if lesson is not None:
            resume = {"course": {"id": course.public_id, "title": course.title}, **_lesson_ref(lesson)}
            break
    return {"courses": out, "resume": resume}


def _visible_course(member, course_id):
    course = visible_courses(member.site).filter(public_id=course_id).first()
    if course is None:
        raise _not_found()
    return course


def course_detail(member, course_id):
    course = _visible_course(member, course_id)
    lessons = _course_lessons([course.pk])[course.pk]
    done = _completed_ids(member, lessons)
    locked = _open_flags(lessons, done)
    modules = []
    for module in course.modules.filter(active=True):
        mine = [lesson for lesson in lessons if lesson.module_id == module.pk]
        if not mine:
            continue  # a module with no active lesson is not shown
        modules.append({
            "id": module.public_id, "title": module.title, "order": module.order,
            "completed_lessons": sum(1 for lesson in mine if lesson.pk in done), "total_lessons": len(mine),
            "lessons": [
                {
                    "id": lesson.public_id, "title": lesson.title, "duration": lesson.duration,
                    "image_url": media.url_for(lesson.image_key),
                    "completed": lesson.pk in done, "locked": locked[lesson.pk],
                }
                for lesson in mine
            ],
        })
    nxt = _next_incomplete(lessons, done)
    return {**course_out(course, lessons, done), "modules": modules, "next_lesson": _lesson_ref(nxt)}


def _visible_lesson(member, lesson_id):
    lesson = (
        Lesson.objects.filter(public_id=lesson_id, active=True, module__active=True)
        .select_related("module__course").first()
    )
    if lesson is None or lesson.module.course not in visible_courses(member.site):
        raise _not_found()
    return lesson


def _module_ref(module, lessons):
    first = next((lesson for lesson in lessons if lesson.module_id == module.pk), None)
    return {"id": module.public_id, "title": module.title, "first_lesson_id": first.public_id}


def lesson_detail(member, lesson_id):
    """The lesson, or 409 lesson_locked. Opening an open lesson records that the member started it."""
    lesson = _visible_lesson(member, lesson_id)
    course = lesson.module.course
    lessons = _course_lessons([course.pk])[course.pk]
    done = _completed_ids(member, lessons)
    locked = _open_flags(lessons, done)
    if locked[lesson.pk]:
        raise _locked()
    LessonProgress.objects.get_or_create(member=member, lesson=lesson)
    index = lessons.index(lesson)
    module = lesson.module
    modules = list(course.modules.filter(active=True))
    position = modules.index(module)
    mine = [x for x in lessons if x.module_id == module.pk]

    def module_ref(other):
        return _module_ref(other, lessons) if any(x.module_id == other.pk for x in lessons) else None

    previous_module = next((module_ref(m) for m in reversed(modules[:position]) if module_ref(m)), None)
    next_module = next((module_ref(m) for m in modules[position + 1:] if module_ref(m)), None)
    nxt = lessons[index + 1] if index + 1 < len(lessons) else None
    return {
        "id": lesson.public_id, "title": lesson.title, "duration": lesson.duration,
        "description": lesson.description, "video_url": lesson.video_url or None,
        "image_url": media.url_for(lesson.image_key), "completed": lesson.pk in done,
        "course": {"id": course.public_id, "title": course.title},
        "module": {"id": module.public_id, "title": module.title},
        "lessons": [
            {"id": x.public_id, "title": x.title, "duration": x.duration,
             "completed": x.pk in done, "locked": locked[x.pk]}
            for x in mine
        ],
        "previous_lesson": _lesson_ref(lessons[index - 1] if index > 0 else None),
        "next_lesson": _lesson_ref(nxt),
        "next_lesson_locked": nxt is not None and lesson.pk not in done,
        "previous_module": previous_module, "next_module": next_module,
    }


def complete_lesson(member, lesson_id):
    """Mark an open lesson complete (again is not an error). A locked one is 409 lesson_locked."""
    lesson = _visible_lesson(member, lesson_id)
    course = lesson.module.course
    with transaction.atomic():
        lessons = _course_lessons([course.pk])[course.pk]
        done = _completed_ids(member, lessons)
        if _open_flags(lessons, done)[lesson.pk]:
            raise _locked()
        try:
            with transaction.atomic():
                progress, _ = LessonProgress.objects.get_or_create(member=member, lesson=lesson)
        except IntegrityError:  # two taps at once: the other one made the row
            progress = LessonProgress.objects.get(member=member, lesson=lesson)
        if progress.completed_at is None:
            progress.completed_at = timezone.now()
            progress.save(update_fields=["completed_at"])
            done.add(lesson.pk)
    index = lessons.index(lesson)
    finished = sum(1 for x in lessons if x.pk in done)
    return {
        "lesson_id": lesson.public_id, "completed": True,
        "course": {
            "id": course.public_id, "completed_lessons": finished, "total_lessons": len(lessons),
            "percent": _percent(finished, len(lessons)),
        },
        "next_lesson": _lesson_ref(lessons[index + 1] if index + 1 < len(lessons) else None),
    }


def live_out(live):
    return {
        "id": live.public_id, "title": live.title, "starts_at": live.starts_at,
        "duration_minutes": live.duration_minutes, "description": live.description,
        "join_url": live.join_url or None, "thumbnail_url": media.url_for(live.thumbnail_key),
    }


def next_live(member, now=None):
    """The soonest live training of the member's own site that has not ended. None when there is none."""
    now = now or timezone.now()
    candidates = LiveTraining.objects.filter(site=member.site, active=True)
    for live in candidates.order_by("starts_at", "pk")[:50]:  # ended ones are skipped below
        if live.starts_at + timedelta(minutes=live.duration_minutes) > now:
            return live_out(live)
    return None




# -- owner authoring ----------------------------------------------------------------------------
# Bodies are checked here, so the owner routes and any later caller share one rule. Every row is
# found through its site first, so another site's id is a 404.

def _text(data, name, errors, out, *, required=False, maximum, message=None):
    if name not in data:
        if required:
            errors[name] = message or "This is required."
        return
    value = data[name]
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        errors[name] = message or f"Use {'1' if required else '0'} to {maximum} characters."
        return
    out[name] = value.strip()


def _flag(data, name, errors, out):
    if name in data:
        if isinstance(data[name], bool):
            out[name] = data[name]
        else:
            errors[name] = "Send true or false."


def _number(data, name, errors, out, *, low, high):
    if name in data:
        value = data[name]
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            out[name] = value
        else:
            errors[name] = f"Use a whole number from {low} to {high}."


def _finish(errors):
    if errors:
        raise _invalid(errors)


def _unique_slug(site, wanted, exclude_pk=None):
    base = slugify(wanted)[:120] or "course"
    slug, n = base, 1
    taken = Course.objects.filter(site=site)
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)
    while taken.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _next_order(queryset):
    return (queryset.aggregate(top=Max("order"))["top"] or 0) + 1


def course_owner_out(course):
    lessons = Lesson.objects.filter(module__course=course)
    return {
        "id": course.public_id, "title": course.title,
        "slug": course.slug, "description": course.description,
        "image_url": media.url_for(course.image_key), "order": course.order, "active": course.active,
        "archived_at": course.archived_at, "created_at": course.created_at, "updated_at": course.updated_at,
        "module_count": course.modules.count(),
        "lesson_count": lessons.count(), "active_lesson_count": lessons.filter(
            active=True, module__active=True
        ).count(),
    }


def module_owner_out(module):
    return {
        "id": module.public_id, "course_id": module.course.public_id, "title": module.title,
        "order": module.order, "active": module.active,
        "lessons": [lesson_owner_out(lesson) for lesson in module.lessons.all()],
    }


def lesson_owner_out(lesson):
    return {
        "id": lesson.public_id, "module_id": lesson.module.public_id, "title": lesson.title,
        "duration": lesson.duration, "description": lesson.description,
        "video_url": lesson.video_url or None, "image_url": media.url_for(lesson.image_key),
        "order": lesson.order, "active": lesson.active,
    }


def live_owner_out(live):
    return {**live_out(live), "active": live.active, "created_at": live.created_at, "updated_at": live.updated_at}


def course_tree(course):
    return {
        **course_owner_out(course),
        "modules": [module_owner_out(m) for m in course.modules.prefetch_related("lessons")],
    }


def list_courses_for_owner(site):
    return Course.objects.filter(site=site)


def list_live_for_owner(site):
    return LiveTraining.objects.filter(site=site)


def get_course(site, public_id):
    course = Course.objects.filter(site=site, public_id=public_id).first()
    if course is None:
        raise _not_found()
    return course


def get_module(site, public_id):
    module = Module.objects.select_related("course").filter(course__site=site, public_id=public_id).first()
    if module is None:
        raise _not_found()
    return module


def get_lesson(site, public_id):
    lesson = (
        Lesson.objects.select_related("module__course").filter(module__course__site=site, public_id=public_id).first()
    )
    if lesson is None:
        raise _not_found()
    return lesson


def get_live(site, public_id):
    live = LiveTraining.objects.filter(site=site, public_id=public_id).first()
    if live is None:
        raise _not_found()
    return live


def _apply(row, values):
    for name, value in values.items():
        setattr(row, name, value)
    row.save()
    return row


@transaction.atomic
def create_course(site, data):
    errors, out = {}, {}
    _text(data, "title", errors, out, required=True, maximum=120)
    _text(data, "slug", errors, out, maximum=120)
    _text(data, "description", errors, out, maximum=5000)
    _flag(data, "active", errors, out)
    _number(data, "order", errors, out, low=1, high=100000)
    _finish(errors)
    out["slug"] = _unique_slug(site, out.get("slug") or out["title"])
    out.setdefault("order", _next_order(Course.objects.filter(site=site)))
    return Course.objects.create(site=site, **out)


@transaction.atomic
def update_course(site, public_id, data):
    course = Course.objects.select_for_update().filter(site=site, public_id=public_id).first()
    if course is None:
        raise _not_found()
    errors, out = {}, {}
    _text(data, "title", errors, out, required="title" in data, maximum=120)
    _text(data, "slug", errors, out, required="slug" in data, maximum=120)
    _text(data, "description", errors, out, maximum=5000)
    _flag(data, "active", errors, out)
    _flag(data, "archived", errors, out)
    _number(data, "order", errors, out, low=1, high=100000)
    _finish(errors)
    if "slug" in out:
        out["slug"] = _unique_slug(site, out["slug"], exclude_pk=course.pk)
    archived = out.pop("archived", None)
    if archived is True:
        out["archived_at"], out["active"] = course.archived_at or timezone.now(), False
    elif archived is False or out.get("active") is True:
        out["archived_at"] = None  # switching an archived course on brings it back
    return _apply(course, out)


def _delete_with_images(row, keys):
    with transaction.atomic():
        row.delete()
        transaction.on_commit(lambda: [media.discard(key) for key in keys])


def delete_course(site, public_id):
    course = get_course(site, public_id)
    keys = [course.image_key, *Lesson.objects.filter(module__course=course).values_list("image_key", flat=True)]
    _delete_with_images(course, keys)


@transaction.atomic
def create_module(site, course_id, data):
    course = get_course(site, course_id)
    errors, out = {}, {}
    _text(data, "title", errors, out, required=True, maximum=120)
    _flag(data, "active", errors, out)
    _number(data, "order", errors, out, low=1, high=100000)
    _finish(errors)
    out.setdefault("order", _next_order(course.modules))
    return Module.objects.create(course=course, **out)


@transaction.atomic
def update_module(site, public_id, data):
    module = Module.objects.select_for_update().filter(course__site=site, public_id=public_id).first()
    if module is None:
        raise _not_found()
    errors, out = {}, {}
    _text(data, "title", errors, out, required="title" in data, maximum=120)
    _flag(data, "active", errors, out)
    _number(data, "order", errors, out, low=1, high=100000)
    _finish(errors)
    return _apply(module, out)


def delete_module(site, public_id):
    module = get_module(site, public_id)
    _delete_with_images(module, list(module.lessons.values_list("image_key", flat=True)))


def _lesson_values(data, errors, out, *, creating):
    _text(data, "title", errors, out, required=creating or "title" in data, maximum=160)
    _text(data, "duration", errors, out, maximum=20)
    _text(data, "description", errors, out, maximum=10000)
    _flag(data, "active", errors, out)
    _number(data, "order", errors, out, low=1, high=100000)
    if "video_url" in data:
        value = data["video_url"]
        if value is None or value == "":
            out["video_url"] = ""
        elif video_url_ok(value):
            out["video_url"] = value
        else:
            errors["video_url"] = VIDEO_MESSAGE


@transaction.atomic
def create_lesson(site, module_id, data):
    module = get_module(site, module_id)
    errors, out = {}, {}
    _lesson_values(data, errors, out, creating=True)
    _finish(errors)
    out.setdefault("order", _next_order(module.lessons))
    return Lesson.objects.create(module=module, **out)


@transaction.atomic
def update_lesson(site, public_id, data):
    lesson = Lesson.objects.select_for_update().filter(module__course__site=site, public_id=public_id).first()
    if lesson is None:
        raise _not_found()
    errors, out = {}, {}
    _lesson_values(data, errors, out, creating=False)
    _finish(errors)
    return _apply(lesson, out)


def delete_lesson(site, public_id):
    lesson = get_lesson(site, public_id)
    _delete_with_images(lesson, [lesson.image_key])


@transaction.atomic
def reorder(site, kind, parent, ids):
    """Set `order` 1..n for the siblings, in the order sent. `ids` must be exactly all of them."""
    if kind == "course":
        siblings = Course.objects.select_for_update().filter(site=site)
    elif kind == "module":
        siblings = Module.objects.select_for_update().filter(course=get_course(site, parent))
    else:
        siblings = Lesson.objects.select_for_update().filter(module=get_module(site, parent))
    rows = {row.public_id: row for row in siblings}
    if not isinstance(ids, list) or len(ids) != len(set(ids)) or set(ids) != set(rows):
        raise _invalid({"ids": "Send every item of the list once, in the order you want."})
    for position, public_id in enumerate(ids, start=1):
        row = rows[public_id]
        if row.order != position:
            type(row).objects.filter(pk=row.pk).update(order=position)
            row.order = position
    return [rows[public_id] for public_id in ids]


def _live_values(data, errors, out, *, creating):
    _text(data, "title", errors, out, required=creating or "title" in data, maximum=120)
    _text(data, "description", errors, out, maximum=5000)
    _flag(data, "active", errors, out)
    _number(data, "duration_minutes", errors, out, low=1, high=1440)
    if "starts_at" in data or creating:
        value = data.get("starts_at")
        if isinstance(value, str):
            from django.utils.dateparse import parse_datetime

            value = parse_datetime(value)
        if value is not None and value.tzinfo is not None and value.utcoffset() is not None:
            out["starts_at"] = value
        else:
            errors["starts_at"] = "Enter a date and time with its time zone."
    if "join_url" in data:
        value = data["join_url"]
        if value is None or value == "":
            out["join_url"] = ""
        elif join_url_ok(value):
            out["join_url"] = value
        else:
            errors["join_url"] = "Use an https link."


def notify_live(live):
    """One bell entry (kind "live") for every member of the training's site. Returns how many."""
    return notices.notify_members(live.site, "live", f"Live training: {live.title}", url=LIVE_PATH)


@transaction.atomic
def create_live(site, data):
    """A new live training. One created active puts an entry in the bell of the site's members."""
    errors, out = {}, {}
    _live_values(data, errors, out, creating=True)
    _finish(errors)
    live = LiveTraining.objects.create(site=site, **out)
    if live.active:
        notify_live(live)
    return live


@transaction.atomic
def update_live(site, public_id, data):
    live = LiveTraining.objects.select_for_update().filter(site=site, public_id=public_id).first()
    if live is None:
        raise _not_found()
    errors, out = {}, {}
    _live_values(data, errors, out, creating=False)
    _finish(errors)
    return _apply(live, out)


def delete_live(site, public_id):
    live = get_live(site, public_id)
    _delete_with_images(live, [live.thumbnail_key])


# -- pictures -----------------------------------------------------------------------------------
KINDS = {"course": get_course, "lesson": get_lesson, "live": get_live}
IMAGE_FIELD = {"course": "image_key", "lesson": "image_key", "live": "thumbnail_key"}


def set_image(site, kind, public_id, upload):
    """Store `upload` (JPEG, PNG or WebP, at most 2 MB) as the picture, replacing any. 503 when the
    store is not configured; a refused file stores nothing; the old file goes after commit."""
    KINDS[kind](site, public_id)
    if not media.configured():
        raise ApiError("image_store_not_configured", IMAGE_NOT_CONFIGURED_MSG, status=503)
    try:
        content, content_type = private_files.check(
            upload, allowed=private_files.IMAGE_TYPES, max_bytes=media.MAX_BYTES
        )
    except private_files.FileRefused as refused:
        raise _invalid({"file": IMAGE_MESSAGES[refused.reason]}) from None
    try:
        key = media.save(content, content_type, folder=IMAGE_FOLDER)
    except media.StoreError:
        raise ApiError("image_not_saved", IMAGE_NOT_SAVED_MSG, status=502) from None
    field = IMAGE_FIELD[kind]
    try:
        with transaction.atomic():
            row = KINDS[kind](site, public_id)
            old = getattr(row, field)
            setattr(row, field, key)
            row.save(update_fields=[field, "updated_at"])
            transaction.on_commit(lambda: media.discard(old))
    except BaseException:
        media.discard(key)
        raise
    return row


@transaction.atomic
def clear_image(site, kind, public_id):
    row = KINDS[kind](site, public_id)
    field = IMAGE_FIELD[kind]
    old = getattr(row, field)
    if old:
        setattr(row, field, "")
        row.save(update_fields=[field, "updated_at"])
        transaction.on_commit(lambda: media.discard(old))
    return row
