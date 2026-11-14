"""The HQ training authoring routes: what the HQ staff app calls to write courses, modules,
lessons and live trainings. No Django admin.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps; the allowed Origin,
the method and the session are checked there and nowhere else. Every route is a POST. The body
is JSON, except the two picture routes, which are a form post (multipart).

    training/courses/list/        {}                          -> {courses: [course]}
    training/courses/get/         {id}                        -> course + {modules: [module + {lessons}]}
    training/courses/create/      {title, slug?, description?, order?, active?} -> course
    training/courses/update/      {id, title?, slug?, description?, order?, active?, archived?} -> course
    training/courses/delete/      {id}                        -> {status: "deleted"}
    training/courses/reorder/     {ids: [course ids, all of them]} -> {courses}
    training/modules/create/      {course, title, order?, active?} -> module
    training/modules/update/      {id, title?, order?, active?}    -> module
    training/modules/delete/      {id}                        -> {status: "deleted"}
    training/modules/reorder/     {course, ids}               -> {modules}
    training/lessons/create/      {module, title, duration?, description?, video_url?, order?, active?} -> lesson
    training/lessons/update/      {id, title?, duration?, description?, video_url?, order?, active?} -> lesson
    training/lessons/delete/      {id}                        -> {status: "deleted"}
    training/lessons/reorder/     {module, ids}               -> {lessons}
    training/live/list/           {}                          -> {live: [live]}
    training/live/create/         {title, starts_at, duration_minutes?, description?, join_url?, site?, active?} -> live
    training/live/update/         {id, ...the same keys}      -> live
    training/live/delete/         {id}                        -> {status: "deleted"}
    training/image/set/           form: kind (course|lesson|live), id, file -> the row
    training/image/clear/         {kind, id}                  -> the row

Errors: 400 invalid_request with `fields`, 404 not_found, 503 image_store_not_configured.
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


def _log(operator, kind, op, row):
    audit.record(
        operator, "training_authored", target_type=f"training_{kind}", target_id=row.public_id,
        detail={"op": op},
    )


def _id(data):
    value = data.get("id")
    if not isinstance(value, str) or not value:
        raise ApiError("invalid_request", services.INVALID_MSG, fields={"id": ["This is required."]})
    return value


DELETED = {"status": "deleted"}


@_route
def course_list(request, operator):
    return {"courses": [services.course_staff_out(c) for c in services.Course.objects.filter(template_key=services.SANTE)]}


@_route
def course_get(request, operator):
    return services.course_tree(services.get_course(_id(_body(request))))


@_route
def course_create(request, operator):
    course = services.create_course(_body(request))
    _log(operator, "course", "create", course)
    return services.course_staff_out(course)


@_route
def course_update(request, operator):
    data = _body(request)
    course = services.update_course(_id(data), {k: v for k, v in data.items() if k != "id"})
    _log(operator, "course", "update", course)
    return services.course_staff_out(course)


@_route
def course_delete(request, operator):
    public_id = _id(_body(request))
    course = services.get_course(public_id)
    services.delete_course(public_id)
    _log(operator, "course", "delete", course)
    return DELETED


@_route
def course_reorder(request, operator):
    rows = services.reorder("course", None, _body(request).get("ids"))
    return {"courses": [services.course_staff_out(c) for c in rows]}


@_route
def module_create(request, operator):
    module = services.create_module(_body(request))
    _log(operator, "module", "create", module)
    return services.module_staff_out(module)


@_route
def module_update(request, operator):
    data = _body(request)
    module = services.update_module(_id(data), {k: v for k, v in data.items() if k != "id"})
    _log(operator, "module", "update", module)
    return services.module_staff_out(module)


@_route
def module_delete(request, operator):
    public_id = _id(_body(request))
    module = services.get_module(public_id)
    services.delete_module(public_id)
    _log(operator, "module", "delete", module)
    return DELETED


@_route
def module_reorder(request, operator):
    data = _body(request)
    rows = services.reorder("module", data.get("course"), data.get("ids"))
    return {"modules": [services.module_staff_out(m) for m in rows]}


@_route
def lesson_create(request, operator):
    lesson = services.create_lesson(_body(request))
    _log(operator, "lesson", "create", lesson)
    return services.lesson_staff_out(lesson)


@_route
def lesson_update(request, operator):
    data = _body(request)
    lesson = services.update_lesson(_id(data), {k: v for k, v in data.items() if k != "id"})
    _log(operator, "lesson", "update", lesson)
    return services.lesson_staff_out(lesson)


@_route
def lesson_delete(request, operator):
    public_id = _id(_body(request))
    lesson = services.get_lesson(public_id)
    services.delete_lesson(public_id)
    _log(operator, "lesson", "delete", lesson)
    return DELETED


@_route
def lesson_reorder(request, operator):
    data = _body(request)
    rows = services.reorder("lesson", data.get("module"), data.get("ids"))
    return {"lessons": [services.lesson_staff_out(x) for x in rows]}


@_route
def live_list(request, operator):
    rows = services.LiveTraining.objects.filter(template_key=services.SANTE).select_related("site")
    return {"live": [services.live_staff_out(x) for x in rows]}


@_route
def live_create(request, operator):
    live = services.create_live(_body(request))
    _log(operator, "live", "create", live)
    return services.live_staff_out(live)


@_route
def live_update(request, operator):
    data = _body(request)
    live = services.update_live(_id(data), {k: v for k, v in data.items() if k != "id"})
    _log(operator, "live", "update", live)
    return services.live_staff_out(live)


@_route
def live_delete(request, operator):
    public_id = _id(_body(request))
    live = services.get_live(public_id)
    services.delete_live(public_id)
    _log(operator, "live", "delete", live)
    return DELETED


def _row_out(kind, row):
    return {
        "course": services.course_staff_out, "lesson": services.lesson_staff_out,
        "live": services.live_staff_out,
    }[kind](row)


@_route
def image_set(request, operator):
    """A form post (multipart): `kind`, `id` and `file` (JPEG, PNG or WebP, at most 2 MB)."""
    kind, public_id = request.POST.get("kind", ""), request.POST.get("id", "")
    row = services.set_image(kind, public_id, request.FILES.get("file"))
    _log(operator, kind, "image_set", row)
    return _row_out(kind, row)


@_route
def image_clear(request, operator):
    data = _body(request)
    kind = data.get("kind")
    row = services.clear_image(kind, data.get("id"))
    _log(operator, kind, "image_clear", row)
    return _row_out(kind, row)


ROUTES = {
    "training/courses/list/": course_list,
    "training/courses/get/": course_get,
    "training/courses/create/": course_create,
    "training/courses/update/": course_update,
    "training/courses/delete/": course_delete,
    "training/courses/reorder/": course_reorder,
    "training/modules/create/": module_create,
    "training/modules/update/": module_update,
    "training/modules/delete/": module_delete,
    "training/modules/reorder/": module_reorder,
    "training/lessons/create/": lesson_create,
    "training/lessons/update/": lesson_update,
    "training/lessons/delete/": lesson_delete,
    "training/lessons/reorder/": lesson_reorder,
    "training/live/list/": live_list,
    "training/live/create/": live_create,
    "training/live/update/": live_update,
    "training/live/delete/": live_delete,
    "training/image/set/": image_set,
    "training/image/clear/": image_clear,
}
