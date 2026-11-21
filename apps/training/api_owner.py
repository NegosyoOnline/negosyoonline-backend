"""The Subscriber's training editor, under /v1/sites/{site_id}/training/. Ids are public ids.

    GET    .../courses/                     -> {courses: [course]}   (archived ones too)
    POST   .../courses/                     {title, slug?, description?, order?, active?} -> 201 course
    POST   .../courses/reorder/             {ids: [every course id]} -> {courses}
    GET    .../courses/{course_id}/         -> course + {modules: [module + {lessons: [lesson]}]}
    PATCH  .../courses/{course_id}/         any of title, slug, description, order, active, archived -> course
    DELETE .../courses/{course_id}/         -> {status: "deleted"}
    POST   .../courses/{course_id}/image/   multipart `file` (JPEG, PNG, WebP, 2 MB) -> course
    DELETE .../courses/{course_id}/image/   -> course
    POST   .../courses/{course_id}/modules/         {title, order?, active?} -> 201 module
    POST   .../courses/{course_id}/modules/reorder/ {ids} -> {modules}
    PATCH  .../modules/{module_id}/         any of title, order, active -> module
    DELETE .../modules/{module_id}/         -> {status: "deleted"}
    POST   .../modules/{module_id}/lessons/         {title, duration?, description?, video_url?, order?, active?} -> 201 lesson
    POST   .../modules/{module_id}/lessons/reorder/ {ids} -> {lessons}
    PATCH  .../lessons/{lesson_id}/         any of the same keys -> lesson
    DELETE .../lessons/{lesson_id}/         -> {status: "deleted"}
    POST   .../lessons/{lesson_id}/image/   multipart `file` -> lesson
    DELETE .../lessons/{lesson_id}/image/   -> lesson
    GET    .../live/                        -> {live: [live]}
    POST   .../live/                        {title, starts_at, duration_minutes?, description?, join_url?, active?} -> 201 live
    PATCH  .../live/{live_id}/              any of the same keys -> live
    DELETE .../live/{live_id}/              -> {status: "deleted"}
    POST   .../live/{live_id}/thumbnail/    multipart `file` -> live
    DELETE .../live/{live_id}/thumbnail/    -> live

Same gates as the member notices (apps/members/api_notices.py): the owner session, the site found
first through site_for (404 for a missing, foreign or non-Santé site), the Billing-only rule (403),
404 everywhere while MEMBERS_ENABLED is off. Every row is read under the caller's own site, so a
foreign id is 404. Errors: 400 invalid_request with `fields`, 404 not_found, 503
image_store_not_configured. Every answer is Cache-Control: no-store.
"""
from datetime import datetime

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.members.api_owner import member_site

from . import services as svc

router = Router(tags=["training"], auth=member_site)


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid")


class CourseIn(StrictSchema):
    title: str
    slug: str | None = None
    description: str | None = None
    order: int | None = None
    active: bool | None = None


class CoursePatch(StrictSchema):
    """Only the keys sent are changed. `archived: true` archives (and switches off)."""

    title: str | None = None
    slug: str | None = None
    description: str | None = None
    order: int | None = None
    active: bool | None = None
    archived: bool | None = None
    shared: bool | None = None  # only the content source store may send this


class ModuleIn(StrictSchema):
    title: str
    order: int | None = None
    active: bool | None = None


class ModulePatch(StrictSchema):
    title: str | None = None
    order: int | None = None
    active: bool | None = None


class LessonIn(StrictSchema):
    title: str
    duration: str | None = None
    description: str | None = None
    video_url: str | None = None
    order: int | None = None
    active: bool | None = None


class LessonPatch(StrictSchema):
    title: str | None = None
    duration: str | None = None
    description: str | None = None
    video_url: str | None = None
    order: int | None = None
    active: bool | None = None


class LiveIn(StrictSchema):
    title: str
    starts_at: datetime
    duration_minutes: int | None = None
    description: str | None = None
    join_url: str | None = None
    active: bool | None = None


class LivePatch(StrictSchema):
    title: str | None = None
    starts_at: datetime | None = None
    duration_minutes: int | None = None
    description: str | None = None
    join_url: str | None = None
    active: bool | None = None


class ReorderIn(StrictSchema):
    ids: list[str]


def _json(data, status=200):
    response = JsonResponse(data, status=status, encoder=DjangoJSONEncoder, safe=False)
    response["Cache-Control"] = "no-store"
    return response


def _sent(payload):
    """The keys the caller sent. A null is kept: the service refuses it as a wrong value (or, for
    video_url and join_url, reads it as clear)."""
    return payload.dict(exclude_unset=True)


DELETED = {"status": "deleted"}


# -- courses ------------------------------------------------------------------------------------

@router.get("/sites/{site_id}/training/courses/")
def course_list(request, site_id: str):
    return _json({"courses": [svc.course_owner_out(c) for c in svc.list_courses_for_owner(request.member_site)]})


@router.post("/sites/{site_id}/training/courses/")
def course_create(request, site_id: str, payload: CourseIn):
    return _json(svc.course_owner_out(svc.create_course(request.member_site, _sent(payload))), 201)


# Registered before courses/{course_id}/ so "reorder" is never read as an id.
@router.post("/sites/{site_id}/training/courses/reorder/")
def course_reorder(request, site_id: str, payload: ReorderIn):
    rows = svc.reorder(request.member_site, "course", None, payload.ids)
    return _json({"courses": [svc.course_owner_out(c) for c in rows]})


@router.get("/sites/{site_id}/training/courses/{course_id}/")
def course_get(request, site_id: str, course_id: str):
    return _json(svc.course_tree(svc.get_course(request.member_site, course_id)))


@router.patch("/sites/{site_id}/training/courses/{course_id}/")
def course_update(request, site_id: str, course_id: str, payload: CoursePatch):
    return _json(svc.course_owner_out(svc.update_course(request.member_site, course_id, _sent(payload))))


@router.delete("/sites/{site_id}/training/courses/{course_id}/")
def course_delete(request, site_id: str, course_id: str):
    svc.delete_course(request.member_site, course_id)
    return _json(DELETED)


@router.post("/sites/{site_id}/training/courses/{course_id}/image/")
def course_image_set(request, site_id: str, course_id: str):
    """A form post (multipart): `file` is a JPEG, PNG or WebP of at most 2 MB."""
    row = svc.set_image(request.member_site, "course", course_id, request.FILES.get("file"))
    return _json(svc.course_owner_out(row))


@router.delete("/sites/{site_id}/training/courses/{course_id}/image/")
def course_image_clear(request, site_id: str, course_id: str):
    return _json(svc.course_owner_out(svc.clear_image(request.member_site, "course", course_id)))


# -- modules ------------------------------------------------------------------------------------

@router.post("/sites/{site_id}/training/courses/{course_id}/modules/")
def module_create(request, site_id: str, course_id: str, payload: ModuleIn):
    return _json(svc.module_owner_out(svc.create_module(request.member_site, course_id, _sent(payload))), 201)


@router.post("/sites/{site_id}/training/courses/{course_id}/modules/reorder/")
def module_reorder(request, site_id: str, course_id: str, payload: ReorderIn):
    rows = svc.reorder(request.member_site, "module", course_id, payload.ids)
    return _json({"modules": [svc.module_owner_out(m) for m in rows]})


@router.patch("/sites/{site_id}/training/modules/{module_id}/")
def module_update(request, site_id: str, module_id: str, payload: ModulePatch):
    return _json(svc.module_owner_out(svc.update_module(request.member_site, module_id, _sent(payload))))


@router.delete("/sites/{site_id}/training/modules/{module_id}/")
def module_delete(request, site_id: str, module_id: str):
    svc.delete_module(request.member_site, module_id)
    return _json(DELETED)


# -- lessons ------------------------------------------------------------------------------------

@router.post("/sites/{site_id}/training/modules/{module_id}/lessons/")
def lesson_create(request, site_id: str, module_id: str, payload: LessonIn):
    return _json(svc.lesson_owner_out(svc.create_lesson(request.member_site, module_id, _sent(payload))), 201)


@router.post("/sites/{site_id}/training/modules/{module_id}/lessons/reorder/")
def lesson_reorder(request, site_id: str, module_id: str, payload: ReorderIn):
    rows = svc.reorder(request.member_site, "lesson", module_id, payload.ids)
    return _json({"lessons": [svc.lesson_owner_out(x) for x in rows]})


@router.patch("/sites/{site_id}/training/lessons/{lesson_id}/")
def lesson_update(request, site_id: str, lesson_id: str, payload: LessonPatch):
    return _json(svc.lesson_owner_out(svc.update_lesson(request.member_site, lesson_id, _sent(payload))))


@router.delete("/sites/{site_id}/training/lessons/{lesson_id}/")
def lesson_delete(request, site_id: str, lesson_id: str):
    svc.delete_lesson(request.member_site, lesson_id)
    return _json(DELETED)


@router.post("/sites/{site_id}/training/lessons/{lesson_id}/image/")
def lesson_image_set(request, site_id: str, lesson_id: str):
    row = svc.set_image(request.member_site, "lesson", lesson_id, request.FILES.get("file"))
    return _json(svc.lesson_owner_out(row))


@router.delete("/sites/{site_id}/training/lessons/{lesson_id}/image/")
def lesson_image_clear(request, site_id: str, lesson_id: str):
    return _json(svc.lesson_owner_out(svc.clear_image(request.member_site, "lesson", lesson_id)))


# -- live trainings -----------------------------------------------------------------------------

@router.get("/sites/{site_id}/training/live/")
def live_list(request, site_id: str):
    return _json({"live": [svc.live_owner_out(x) for x in svc.list_live_for_owner(request.member_site)]})


@router.post("/sites/{site_id}/training/live/")
def live_create(request, site_id: str, payload: LiveIn):
    return _json(svc.live_owner_out(svc.create_live(request.member_site, _sent(payload))), 201)


@router.patch("/sites/{site_id}/training/live/{live_id}/")
def live_update(request, site_id: str, live_id: str, payload: LivePatch):
    return _json(svc.live_owner_out(svc.update_live(request.member_site, live_id, _sent(payload))))


@router.delete("/sites/{site_id}/training/live/{live_id}/")
def live_delete(request, site_id: str, live_id: str):
    svc.delete_live(request.member_site, live_id)
    return _json(DELETED)


@router.post("/sites/{site_id}/training/live/{live_id}/thumbnail/")
def live_thumbnail_set(request, site_id: str, live_id: str):
    row = svc.set_image(request.member_site, "live", live_id, request.FILES.get("file"))
    return _json(svc.live_owner_out(row))


@router.delete("/sites/{site_id}/training/live/{live_id}/thumbnail/")
def live_thumbnail_clear(request, site_id: str, live_id: str):
    return _json(svc.live_owner_out(svc.clear_image(request.member_site, "live", live_id)))
