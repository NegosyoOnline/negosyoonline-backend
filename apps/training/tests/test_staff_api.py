"""The HQ authoring routes under /staff/v1/training/: staff only, validated, no admin."""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.catalog.tests.base import FakeMedia, image_file
from apps.members.models import MemberNotification
from apps.members.tests.base import MembersTestCase
from apps.operators import audit, staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import STAFF, StaffTestCase, on_staff
from apps.operators.tests.base import HQ
from apps.sites.tests.base import make_account, make_site
from apps.training import services, staff
from apps.training.models import Course, Lesson, LiveTraining, Module
from apps.training.tests.base import YT, make_course_with_lessons

BASE = "/staff/v1/training/"
GOOD_VIDEOS = (
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    "https://youtube.com/embed/dQw4w9WgXcQ?rel=0",
    "https://player.vimeo.com/video/76979871",
    "https://player.vimeo.com/video/76979871?h=8272103f6e",
    "https://iframe.mediadelivery.net/embed/12345/0a1b2c3d-4e5f-6789-abcd-ef0123456789",
    "https://iframe.mediadelivery.net/play/12345/0a1b2c3d-4e5f-6789-abcd-ef0123456789",
)
BAD_VIDEOS = (
    "http://www.youtube.com/embed/dQw4w9WgXcQ",             # not https
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",           # a watch page, not an embed
    "https://www.youtube.com/embed/short",                   # not an 11 character id
    "https://evil.example/embed/dQw4w9WgXcQ",
    "https://www.youtube.com.evil.example/embed/dQw4w9WgXcQ",
    "https://user@www.youtube.com/embed/dQw4w9WgXcQ",
    "https://www.youtube.com:8443/embed/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ#x",
    "https://player.vimeo.com/video/abc",
    "https://vimeo.com/76979871",
    "https://iframe.mediadelivery.net/embed/abc/0a1b2c3d-4e5f",
    "https://cdn.example.com/video.mp4",
    "javascript:alert(1)",
    "//www.youtube.com/embed/dQw4w9WgXcQ",
    " https://www.youtube.com/embed/dQw4w9WgXcQ",
    "<iframe src='https://www.youtube.com/embed/dQw4w9WgXcQ'></iframe>",
)


class VideoAllowlistTests(StaffTestCase):
    def test_the_three_providers_embed_urls_pass(self):
        for url in GOOD_VIDEOS:
            self.assertTrue(services.video_url_ok(url), url)

    def test_everything_else_is_refused(self):
        for url in BAD_VIDEOS:
            self.assertFalse(services.video_url_ok(url), url)
        self.assertFalse(services.video_url_ok(None))
        self.assertFalse(services.video_url_ok("https://www.youtube.com/embed/" + "a" * 11 + "?" + "x" * 500))


@on_staff
class StaffTrainingTestCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()

    def api(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def signed_in(self):
        self.staff_sign_in()
        return self


class AccessTests(StaffTrainingTestCase):
    def test_the_routes_are_in_the_table_and_are_posts(self):
        self.assertEqual(len(staff.ROUTES), 20)
        for path in staff.ROUTES:
            self.assertTrue(path.startswith("training/"))
            self.assertEqual(staff_views.ROUTES[path][0], "POST")

    def test_another_origin_is_refused(self):
        for path in staff.ROUTES:
            response = self.call("POST", "/staff/v1/" + path, {}, origin="https://evil.example")
            self.assertError(response, 403, "origin_not_allowed")

    def test_no_session_and_a_session_before_the_second_step_are_refused(self):
        for path in staff.ROUTES:
            self.assertError(self.call("POST", "/staff/v1/" + path, {"title": "x"}), 401, "unauthenticated")
        self.staff_email_step()
        for path in staff.ROUTES:
            self.assertError(self.call("POST", "/staff/v1/" + path, {"title": "x"}), 401, "second_step_required")
        self.assertFalse(Course.objects.exists())
        self.assertFalse(OperatorAuditEntry.objects.filter(action="training_authored").exists())

    def test_a_subscriber_api_session_cannot_reach_them_and_nothing_is_in_django_admin(self):
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)
        self.assertFalse(hasattr(services, "admin"))
        response = self.client.post(
            "/v1/training/courses/create/", {"title": "x"}, content_type="application/json", secure=True
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Course.objects.exists())


class CourseAuthoringTests(StaffTrainingTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def test_create_update_list_get_delete_a_course_with_modules_and_lessons(self):
        created = self.api("courses/create/", {"title": "How to use the Santé Mobile App", "description": "d"})
        self.assertEqual(created.status_code, 200, created.content)
        course = created.json()
        self.assertEqual((course["slug"], course["order"], course["active"], course["template_key"]),
                         ("how-to-use-the-sante-mobile-app", 1, True, "sante"))
        module = self.api("modules/create/", {"course": course["id"], "title": "Basics"}).json()
        lesson = self.api("lessons/create/", {
            "module": module["id"], "title": "Welcome", "duration": "4:10", "video_url": YT,
        }).json()
        self.assertEqual((lesson["order"], lesson["video_url"], lesson["module_id"]), (1, YT, module["id"]))
        tree = self.api("courses/get/", {"id": course["id"]}).json()
        self.assertEqual(tree["modules"][0]["lessons"][0]["id"], lesson["id"])
        self.assertEqual((tree["module_count"], tree["lesson_count"], tree["active_lesson_count"]), (1, 1, 1))
        self.assertEqual(len(self.api("courses/list/").json()["courses"]), 1)
        changed = self.api("lessons/update/", {"id": lesson["id"], "title": "Hello", "active": False}).json()
        self.assertEqual((changed["title"], changed["active"], changed["video_url"]), ("Hello", False, YT))
        self.assertEqual(self.api("modules/update/", {"id": module["id"], "title": "Start"}).json()["title"], "Start")
        self.assertEqual(self.api("courses/update/", {"id": course["id"], "title": "Renamed"}).json()["slug"],
                         "how-to-use-the-sante-mobile-app")
        self.assertEqual(self.api("courses/delete/", {"id": course["id"]}).json(), {"status": "deleted"})
        self.assertEqual((Course.objects.count(), Module.objects.count(), Lesson.objects.count()), (0, 0, 0))

    def test_a_video_outside_the_allowlist_is_a_field_error_and_nothing_is_stored(self):
        module = Module.objects.create(course=Course.objects.create(title="C", slug="c"), title="M")
        for url in ("https://evil.example/x", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
            response = self.api("lessons/create/", {"module": module.public_id, "title": "L", "video_url": url})
            self.assertEqual(response.status_code, 400)
            self.assertIn("video_url", response.json()["error"]["fields"])
        self.assertFalse(Lesson.objects.exists())
        lesson = self.api("lessons/create/", {"module": module.public_id, "title": "L"}).json()
        self.assertIsNone(lesson["video_url"])  # a lesson may have no video
        bad = self.api("lessons/update/", {"id": lesson["id"], "video_url": "https://evil.example/x"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(Lesson.objects.get().video_url, "")
        cleared = self.api("lessons/update/", {"id": lesson["id"], "video_url": YT})
        self.assertEqual(cleared.json()["video_url"], YT)
        self.assertIsNone(self.api("lessons/update/", {"id": lesson["id"], "video_url": ""}).json()["video_url"])

    def test_bad_values_are_field_errors(self):
        response = self.api("courses/create/", {"title": "", "order": 0, "active": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()["error"]["fields"]), {"title", "order", "active"})
        self.assertEqual(self.api("modules/create/", {"title": "M", "course": "crs_none"}).status_code, 400)
        self.assertEqual(self.api("courses/update/", {"title": "x"}).status_code, 400)
        self.assertEqual(self.api("courses/update/", {"id": "crs_missing", "title": "x"}).status_code, 404)
        self.assertEqual(self.api("lessons/create/", {"module": "mdl_none", "title": "L"}).status_code, 400)
        self.assertFalse(Course.objects.exists())

    def test_slugs_are_unique_and_a_name_can_be_chosen(self):
        a = self.api("courses/create/", {"title": "Same"}).json()
        b = self.api("courses/create/", {"title": "Same"}).json()
        c = self.api("courses/create/", {"title": "Other", "slug": "same"}).json()
        self.assertEqual((a["slug"], b["slug"], c["slug"]), ("same", "same-2", "same-3"))
        self.assertEqual((a["order"], b["order"], c["order"]), (1, 2, 3))

    def test_archiving_switches_the_course_off_and_switching_on_brings_it_back(self):
        course = self.api("courses/create/", {"title": "A"}).json()
        archived = self.api("courses/update/", {"id": course["id"], "archived": True}).json()
        self.assertFalse(archived["active"])
        self.assertIsNotNone(archived["archived_at"])
        back = self.api("courses/update/", {"id": course["id"], "active": True}).json()
        self.assertTrue(back["active"])
        self.assertIsNone(back["archived_at"])

    def test_ordering(self):
        course, lessons = make_course_with_lessons((3,))
        other = Course.objects.create(title="Second", slug="second", order=2)
        module = course.modules.get()
        ids = [x.public_id for x in reversed(lessons)]
        out = self.api("lessons/reorder/", {"module": module.public_id, "ids": ids}).json()["lessons"]
        self.assertEqual([x["id"] for x in out], ids)
        self.assertEqual(list(Lesson.objects.order_by("order").values_list("public_id", flat=True)), ids)
        courses = self.api("courses/reorder/", {"ids": [other.public_id, course.public_id]}).json()["courses"]
        self.assertEqual([c["order"] for c in courses], [1, 2])
        self.assertEqual(Course.objects.get(pk=other.pk).order, 1)
        second = Module.objects.create(course=course, title="Z", order=5)
        mods = self.api("modules/reorder/", {"course": course.public_id, "ids": [second.public_id, module.public_id]})
        self.assertEqual([m["id"] for m in mods.json()["modules"]], [second.public_id, module.public_id])

    def test_a_reorder_that_misses_or_repeats_an_item_changes_nothing(self):
        course, lessons = make_course_with_lessons((3,))
        module = course.modules.get()
        before = list(Lesson.objects.order_by("pk").values_list("order", flat=True))
        for ids in ([lessons[0].public_id], [lessons[0].public_id] * 3, "x", None,
                    [x.public_id for x in lessons] + ["lsn_extra"]):
            response = self.api("lessons/reorder/", {"module": module.public_id, "ids": ids})
            self.assertEqual(response.status_code, 400, ids)
        self.assertEqual(list(Lesson.objects.order_by("pk").values_list("order", flat=True)), before)

    def test_every_change_is_audited_without_text(self):
        course = self.api("courses/create/", {"title": "Secret title"}).json()
        self.api("courses/update/", {"id": course["id"], "active": False})
        self.api("courses/delete/", {"id": course["id"]})
        entries = list(OperatorAuditEntry.objects.filter(action="training_authored").order_by("pk"))
        self.assertEqual([e.detail["op"] for e in entries], ["create", "update", "delete"])
        self.assertEqual({e.target_id for e in entries}, {course["id"]})
        self.assertNotIn("Secret title", str([(e.detail, e.reason) for e in entries]))
        self.assertIn("training_authored", audit.ACTIONS)

    def test_other_template_content_is_not_reachable(self):
        foreign = Course.objects.create(title="F", slug="f", template_key="basic")
        self.assertEqual(self.api("courses/get/", {"id": foreign.public_id}).status_code, 404)
        self.assertEqual(self.api("courses/update/", {"id": foreign.public_id, "title": "x"}).status_code, 404)
        self.assertEqual(self.api("courses/delete/", {"id": foreign.public_id}).status_code, 404)
        self.assertEqual(self.api("modules/create/", {"course": foreign.public_id, "title": "M"}).status_code, 400)
        self.assertEqual(self.api("courses/list/").json()["courses"], [])


class ImageTests(StaffTrainingTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()
        self.course = Course.objects.create(title="C", slug="c")
        self.lesson = Lesson.objects.create(module=Module.objects.create(course=self.course, title="M"), title="L")

    def upload(self, kind, row, file=None):
        return self.client.post(
            BASE + "image/set/", {"kind": kind, "id": row.public_id, "file": file or image_file()},
            secure=True, HTTP_HOST=HQ, HTTP_ORIGIN=STAFF,
        )

    def test_a_picture_is_stored_replaced_and_cleared(self):
        with FakeMedia() as media:
            first = self.upload("course", self.course)
            self.assertEqual(first.status_code, 200, first.content)
            self.assertTrue(first.json()["image_url"].startswith("https://media.example.test/training/"))
            old = Course.objects.get().image_key
            with self.captureOnCommitCallbacks(execute=True):
                self.upload("course", self.course)
            self.assertNotEqual(Course.objects.get().image_key, old)
            self.assertIn(old, media.deleted)
            cleared = self.api("image/clear/", {"kind": "course", "id": self.course.public_id})
            self.assertIsNone(cleared.json()["image_url"])
            self.assertEqual(Course.objects.get().image_key, "")
            self.assertEqual(self.upload("lesson", self.lesson).status_code, 200)

    def test_a_live_thumbnail_uses_the_same_route(self):
        live = LiveTraining.objects.create(title="L", starts_at=timezone.now())
        with FakeMedia():
            self.assertEqual(self.upload("live", live).status_code, 200)
        self.assertTrue(LiveTraining.objects.get().thumbnail_key.startswith("training/"))

    def test_the_store_not_configured_is_503(self):
        response = self.upload("course", self.course)
        self.assertEqual((response.status_code, response.json()["error"]["code"]),
                         (503, "image_store_not_configured"))
        self.assertEqual(Course.objects.get().image_key, "")

    def test_a_wrong_file_or_kind_is_refused_and_nothing_is_stored(self):
        with FakeMedia() as media:
            wrong = self.upload("course", self.course, image_file(b"not an image", "text/plain", "x.txt"))
            self.assertEqual(wrong.status_code, 400)
            self.assertIn("file", wrong.json()["error"]["fields"])
            self.assertEqual(self.upload("module", self.course).status_code, 400)
            self.assertEqual(media.files, {})

    def test_the_picture_route_needs_a_session(self):
        self.client.cookies.clear()
        with FakeMedia() as media:
            response = self.upload("course", self.course)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(media.files, {})


class LiveAuthoringTests(StaffTrainingTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()
        self.when = (timezone.now() + timedelta(days=2)).isoformat()

    def test_create_update_list_delete(self):
        live = self.api("live/create/", {
            "title": "Bootcamp", "starts_at": self.when, "duration_minutes": 90,
            "join_url": "https://zoom.example/j/1", "description": "Join us",
        })
        self.assertEqual(live.status_code, 200, live.content)
        body = live.json()
        self.assertEqual((body["duration_minutes"], body["site_id"], body["active"]), (90, None, True))
        changed = self.api("live/update/", {"id": body["id"], "title": "Renamed", "active": False}).json()
        self.assertEqual((changed["title"], changed["active"]), ("Renamed", False))
        self.assertEqual(len(self.api("live/list/").json()["live"]), 1)
        self.assertEqual(self.api("live/delete/", {"id": body["id"]}).json(), {"status": "deleted"})
        self.assertFalse(LiveTraining.objects.exists())

    def test_bad_values_are_field_errors(self):
        for data, field in (
            ({"title": "T", "starts_at": "tomorrow"}, "starts_at"),
            ({"title": "T", "starts_at": "2026-12-01T10:00:00"}, "starts_at"),  # no time zone
            ({"title": "T", "starts_at": self.when, "join_url": "http://zoom.example/j"}, "join_url"),
            ({"title": "T", "starts_at": self.when, "join_url": "javascript:alert(1)"}, "join_url"),
            ({"title": "T", "starts_at": self.when, "duration_minutes": 0}, "duration_minutes"),
            ({"title": "T", "starts_at": self.when, "site": "site_nope"}, "site"),
            ({"starts_at": self.when}, "title"),
        ):
            response = self.api("live/create/", data)
            self.assertEqual(response.status_code, 400, data)
            self.assertIn(field, response.json()["error"]["fields"])
        self.assertFalse(LiveTraining.objects.exists())

    def test_creating_one_active_rings_the_bell_of_the_members_it_is_for(self):
        site = make_site(make_account(), "anas-store", "Ana's Store")
        other = make_site(make_account("ben@example.test"), "bens-store", "Ben's Store")
        a = services_member(site, "a@buyer.test", "09175550130")
        b = services_member(other, "b@buyer.test", "09175550131")
        self.api("live/create/", {"title": "Ours", "starts_at": self.when, "site": site.public_id})
        self.assertEqual(MemberNotification.objects.filter(member=a, kind="live").count(), 1)
        self.assertEqual(MemberNotification.objects.filter(member=b).count(), 0)
        self.api("live/create/", {"title": "Everyone", "starts_at": self.when})
        self.assertEqual(MemberNotification.objects.filter(member=b, kind="live").count(), 1)
        self.assertEqual(MemberNotification.objects.filter(member=a, kind="live").count(), 2)
        self.api("live/create/", {"title": "Draft", "starts_at": self.when, "active": False})
        self.assertEqual(MemberNotification.objects.count(), 3)


def services_member(site, email, mobile):
    from apps.members import services as member_services

    return member_services.create_member(site, full_name="M", mobile=mobile, email=email, email_state="pending")
