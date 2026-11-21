"""The member's training reads and the one write, added to the storefront surface next to the
member routes (apps/storefront/api.py mounts this router under /member/):

    POST /storefront/v1/member/training/courses/                      {host, token}
         -> {courses: [course], resume: {course: {id, title}, id, title} | null}
    POST /storefront/v1/member/training/courses/{course_id}/          {host, token}
         -> course + {modules: [{id, title, order, completed_lessons, total_lessons, lessons: [...]}],
                      next_lesson: {id, title} | null}
    POST /storefront/v1/member/training/lessons/{lesson_id}/          {host, token}
         -> the lesson (409 lesson_locked until the one before it is complete)
    POST /storefront/v1/member/training/lessons/{lesson_id}/complete/ {host, token}
         -> {lesson_id, completed, course: {id, completed_lessons, total_lessons, percent}, next_lesson}
    POST /storefront/v1/member/training/lessons/{lesson_id}/watch/    {host, token, percent}
         -> {percent}: keeps the furthest percent (0-100), never completes
    POST /storefront/v1/member/training/live/                         {host, token}
         -> {live: {id, title, starts_at, duration_minutes, description, join_url, thumbnail_url} | null}

Same gates as the other member routes: the storefronts server's credential, MEMBERS_ENABLED, a
served Santé host (404), and a live member session of that host's site (401
member_session_invalid). Courses and live trainings are read through the member's own site,
so another site's content answers the same 404 as a missing id.
"""
from datetime import datetime

from ninja import Router, Schema
from pydantic import StrictInt

from apps.members.api import _session, _site

from . import services

router = Router()


class SessionIn(Schema):
    host: str = ""
    token: str = ""


class RefOut(Schema):
    id: str
    title: str


class CourseOut(Schema):
    id: str
    slug: str
    title: str
    description: str
    image_url: str | None
    order: int
    coming_soon: bool
    module_count: int
    completed_lessons: int
    total_lessons: int
    percent: int


class ResumeOut(Schema):
    course: RefOut
    id: str
    title: str


class CoursesOut(Schema):
    courses: list[CourseOut]
    resume: ResumeOut | None


class LessonRowOut(Schema):
    id: str
    title: str
    duration: str
    image_url: str | None
    completed: bool
    locked: bool


class ModuleOut(Schema):
    id: str
    title: str
    order: int
    module_number: int
    completed_lessons: int
    total_lessons: int
    lessons: list[LessonRowOut]


class CourseDetailOut(CourseOut):
    modules: list[ModuleOut]
    next_lesson: RefOut | None


class SideLessonOut(Schema):
    id: str
    title: str
    duration: str
    completed: bool
    locked: bool


class LessonModuleOut(Schema):
    id: str
    title: str
    module_number: int


class ModuleRefOut(Schema):
    id: str
    title: str
    module_number: int
    first_lesson_id: str


class VideoOut(Schema):
    kind: str  # stream | legacy | none
    status: str
    embed_url: str | None
    needs_video: bool


class LessonOut(Schema):
    id: str
    title: str
    duration: str
    description: str
    video_url: str | None
    video: VideoOut
    image_url: str | None
    completed: bool
    course: RefOut
    module: LessonModuleOut
    lessons: list[SideLessonOut]
    previous_lesson: RefOut | None
    next_lesson: RefOut | None
    next_lesson_locked: bool
    previous_module: ModuleRefOut | None
    next_module: ModuleRefOut | None


class CourseProgressOut(Schema):
    id: str
    completed_lessons: int
    total_lessons: int
    percent: int


class CompleteOut(Schema):
    lesson_id: str
    completed: bool
    course: CourseProgressOut
    next_lesson: RefOut | None


class WatchIn(Schema):
    host: str = ""
    token: str = ""
    percent: StrictInt


class WatchOut(Schema):
    percent: int


class LiveOut(Schema):
    id: str
    title: str
    starts_at: datetime
    duration_minutes: int
    description: str
    join_url: str | None
    thumbnail_url: str | None


class NextLiveOut(Schema):
    live: LiveOut | None


def _member(body):
    return _session(_site(body.host), body.token).member


@router.post("/training/courses/", response=CoursesOut)
def courses(request, body: SessionIn):
    return services.list_courses(_member(body))


@router.post("/training/courses/{course_id}/", response=CourseDetailOut)
def course(request, course_id: str, body: SessionIn):
    return services.course_detail(_member(body), course_id)


@router.post("/training/lessons/{lesson_id}/", response=LessonOut)
def lesson(request, lesson_id: str, body: SessionIn):
    return services.lesson_detail(_member(body), lesson_id)


@router.post("/training/lessons/{lesson_id}/complete/", response=CompleteOut)
def lesson_complete(request, lesson_id: str, body: SessionIn):
    return services.complete_lesson(_member(body), lesson_id)


@router.post("/training/lessons/{lesson_id}/watch/", response=WatchOut)
def lesson_watch(request, lesson_id: str, body: WatchIn):
    return services.record_watch(_member(body), lesson_id, body.percent)


@router.post("/training/live/", response=NextLiveOut)
def live(request, body: SessionIn):
    return {"live": services.next_live(_member(body))}
