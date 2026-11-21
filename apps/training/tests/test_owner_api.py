"""The Subscriber's training editor: the gates, validation, the picture, and one owner per site."""
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.accounts.tests.base import ORIGIN
from apps.catalog.tests.base import FakeMedia, image_file
from apps.members.models import Member, MemberNotification
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.training import services
from apps.training.models import Course, Lesson, LiveTraining, Module
from apps.training.tests.base import YT, make_course, make_lesson, make_module

NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
WHEN = "2026-12-01T10:00:00+08:00"


@override_settings(MEMBERS_ENABLED=True)
class TrainingOwnerCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)

    def url(self, tail="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/training/{tail}"

    def req(self, method, path, body=None, client=None):
        if method == "get":
            return self.get(path, client=client)
        return self.send(method, path, body, client=client)

    def upload(self, tail, client=None, site_id=None, file=None):
        return (client or self.client).post(
            self.url(tail, site_id), data={"file": file or image_file()}, secure=True, HTTP_ORIGIN=ORIGIN
        )

    def tree(self, site=None):
        course = make_course(site or self.site, "Ways to Earn")
        module = make_module(course, "Start here")
        return course, module, make_lesson(module, "Welcome")


class GateTests(TrainingOwnerCase):
    def test_every_route_is_404_while_the_switch_is_off(self):
        course, module, lesson = self.tree()
        with override_settings(MEMBERS_ENABLED=False):
            for method, tail in (
                ("get", "courses/"), ("post", "courses/"), ("get", f"courses/{course.public_id}/"),
                ("patch", f"modules/{module.public_id}/"), ("delete", f"lessons/{lesson.public_id}/"),
                ("get", "live/"),
            ):
                with self.subTest(method=method, tail=tail):
                    body = {"title": "x"} if method in ("post", "patch") else None
                    self.error(self.req(method, self.url(tail), body), 404, "not_found")

    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self.get(self.url("courses/")).status_code, 401)
        self.assertEqual(self.send("post", self.url("courses/"), {"title": "x"}).status_code, 401)

    def test_a_site_that_is_not_santa_is_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        self.error(self.get(self.url("courses/")), 404, "not_found")

    def test_every_answer_is_not_cached(self):
        self.assertEqual(self.get(self.url("courses/"))["Cache-Control"], "no-store")
        self.assertEqual(self.get(self.url("live/"))["Cache-Control"], "no-store")


class CourseTests(TrainingOwnerCase):
    def test_build_a_course_with_a_module_and_lessons_then_read_the_whole_tree(self):
        made = self.send("post", self.url("courses/"), {"title": "  Ways to Earn ", "description": "Start"})
        self.assertEqual(made.status_code, 201, made.content)
        course = made.json()
        self.assertEqual((course["title"], course["slug"], course["order"], course["active"]), ("Ways to Earn", "ways-to-earn", 1, True))
        self.assertTrue(course["id"].startswith("crs_"))
        self.assertEqual(
            set(course),
            {"id", "title", "slug", "description", "image_url", "order", "active", "shared", "copied_from",
             "archived_at", "created_at",
             "updated_at", "module_count", "lesson_count", "active_lesson_count"},
        )
        module = self.send("post", self.url(f"courses/{course['id']}/modules/"), {"title": "Start here"})
        self.assertEqual(module.status_code, 201, module.content)
        module = module.json()
        self.assertEqual((module["course_id"], module["order"], module["lessons"]), (course["id"], 1, []))
        lesson = self.send("post", self.url(f"modules/{module['id']}/lessons/"), {
            "title": "Welcome", "duration": "6:30", "description": "Hi", "video_url": YT,
        })
        self.assertEqual(lesson.status_code, 201, lesson.content)
        lesson = lesson.json()
        self.assertEqual(
            (lesson["module_id"], lesson["duration"], lesson["video_url"], lesson["order"], lesson["image_url"]),
            (module["id"], "6:30", YT, 1, None),
        )
        full = self.get(self.url(f"courses/{course['id']}/")).json()
        self.assertEqual((full["module_count"], full["lesson_count"]), (1, 1))
        self.assertEqual(full["modules"][0]["id"], module["id"])
        self.assertEqual([x["id"] for x in full["modules"][0]["lessons"]], [lesson["id"]])
        self.assertEqual([c["id"] for c in self.get(self.url("courses/")).json()["courses"]], [course["id"]])

    def test_update_and_delete(self):
        course, module, lesson = self.tree()
        patched = self.send("patch", self.url(f"courses/{course.public_id}/"), {"title": "Renamed", "active": False})
        self.assertEqual((patched.status_code, patched.json()["title"], patched.json()["active"]), (200, "Renamed", False))
        self.assertEqual(patched.json()["description"], "")
        self.assertEqual(self.send("patch", self.url(f"modules/{module.public_id}/"), {"title": "M2"}).json()["title"], "M2")
        patched = self.send("patch", self.url(f"lessons/{lesson.public_id}/"), {"duration": "9:00", "video_url": None})
        self.assertEqual((patched.json()["duration"], patched.json()["video_url"]), ("9:00", None))
        self.assertEqual(self.send("delete", self.url(f"lessons/{lesson.public_id}/")).json(), {"status": "deleted"})
        self.assertEqual(self.send("delete", self.url(f"courses/{course.public_id}/")).json(), {"status": "deleted"})
        self.assertFalse(Course.objects.exists() or Module.objects.exists() or Lesson.objects.exists())

    def test_the_list_holds_inactive_and_archived_courses_too(self):
        make_course(self.site, "A", active=False)
        make_course(self.site, "B", active=False, archived_at=timezone.now())
        make_course(self.site, "C")
        self.assertEqual(len(self.get(self.url("courses/")).json()["courses"]), 3)

    def test_archiving_switches_the_course_off_and_switching_on_brings_it_back(self):
        course = make_course(self.site)
        path = self.url(f"courses/{course.public_id}/")
        archived = self.send("patch", path, {"archived": True}).json()
        self.assertEqual((archived["active"], archived["archived_at"] is not None), (False, True))
        back = self.send("patch", path, {"active": True}).json()
        self.assertEqual((back["active"], back["archived_at"]), (True, None))

    def test_a_video_outside_the_allowlist_is_a_field_error_and_nothing_is_stored(self):
        module = make_module(make_course(self.site))
        for bad in ("http://www.youtube.com/embed/dQw4w9WgXcQ", "https://evil.example/embed/x",
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "javascript:alert(1)"):
            with self.subTest(video=bad):
                response = self.send("post", self.url(f"modules/{module.public_id}/lessons/"), {"title": "L", "video_url": bad})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("video_url", response.json()["error"]["fields"])
        self.assertFalse(Lesson.objects.exists())

    def test_bad_values_unknown_keys_and_nulls_are_refused(self):
        course = make_course(self.site)
        cases = (
            ("post", "courses/", {"title": ""}, "title"), ("post", "courses/", {"title": "x" * 121}, "title"),
            ("patch", f"courses/{course.public_id}/", {"order": 0}, "order"),
            ("patch", f"courses/{course.public_id}/", {"title": None}, "title"),
        )
        for method, tail, body, name in cases:
            with self.subTest(body=body):
                response = self.send(method, self.url(tail), body)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(name, response.json()["error"]["fields"])
        for body in ({"title": "x", "site": "other"}, {"title": "x", "id": "crs_1"}, {}):
            self.assertEqual(self.send("post", self.url("courses/"), body).status_code, 400)
        self.assertEqual(Course.objects.count(), 1)

    def test_slugs_are_unique_within_a_site_but_may_repeat_on_another(self):
        self.send("post", self.url("courses/"), {"title": "Ways to Earn"})
        second = self.send("post", self.url("courses/"), {"title": "Ways to Earn"}).json()
        self.assertEqual(second["slug"], "ways-to-earn-2")
        bens = self.send("post", self.url("courses/", self.ben_site_id), {"title": "Ways to Earn"}, client=self.ben).json()
        self.assertEqual(bens["slug"], "ways-to-earn")

    def test_ordering(self):
        a, b, c = (make_course(self.site, t, order=n) for n, t in enumerate("ABC", start=1))
        response = self.send("post", self.url("courses/reorder/"), {"ids": [c.public_id, a.public_id, b.public_id]})
        self.assertEqual([x["title"] for x in response.json()["courses"]], ["C", "A", "B"])
        self.assertEqual([x["order"] for x in response.json()["courses"]], [1, 2, 3])
        m1, m2 = make_module(a, "m1", order=1), make_module(a, "m2", order=2)
        response = self.send("post", self.url(f"courses/{a.public_id}/modules/reorder/"), {"ids": [m2.public_id, m1.public_id]})
        self.assertEqual([x["title"] for x in response.json()["modules"]], ["m2", "m1"])
        l1, l2 = make_lesson(m1, "l1", order=1), make_lesson(m1, "l2", order=2)
        response = self.send("post", self.url(f"modules/{m1.public_id}/lessons/reorder/"), {"ids": [l2.public_id, l1.public_id]})
        self.assertEqual([x["title"] for x in response.json()["lessons"]], ["l2", "l1"])
        self.assertEqual(Lesson.objects.get(pk=l2.pk).order, 1)

    def test_a_reorder_that_misses_or_repeats_an_item_changes_nothing(self):
        a, b = make_course(self.site, "A", order=1), make_course(self.site, "B", order=2)
        for ids in ([a.public_id], [a.public_id, a.public_id], [a.public_id, b.public_id, "crs_x"], []):
            with self.subTest(ids=ids):
                response = self.send("post", self.url("courses/reorder/"), {"ids": ids})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("ids", response.json()["error"]["fields"])
        self.assertEqual([Course.objects.get(pk=x.pk).order for x in (a, b)], [1, 2])

    def test_new_items_go_to_the_end(self):
        course = make_course(self.site)
        for title in ("one", "two"):
            self.send("post", self.url(f"courses/{course.public_id}/modules/"), {"title": title})
        self.assertEqual(list(course.modules.values_list("title", "order")), [("one", 1), ("two", 2)])


class IsolationTests(TrainingOwnerCase):
    def test_an_owner_sees_only_their_own_sites_training(self):
        mine, _, _ = self.tree()
        bens, _, _ = self.tree(self.ben_site)
        LiveTraining.objects.create(site=self.ben_site, title="Bens", starts_at=timezone.now())
        self.assertEqual([c["id"] for c in self.get(self.url("courses/")).json()["courses"]], [mine.public_id])
        self.assertEqual(self.get(self.url("live/")).json(), {"live": []})
        theirs = self.get(self.url("courses/", self.ben_site_id), client=self.ben).json()["courses"]
        self.assertEqual([c["id"] for c in theirs], [bens.public_id])

    def test_a_foreign_site_is_the_same_404_as_a_missing_site(self):
        bens, module, lesson = self.tree(self.ben_site)
        before = (Course.objects.count(), Module.objects.count(), Lesson.objects.count())
        for method, tail, body in (
            ("get", "courses/", None), ("post", "courses/", {"title": "x"}),
            ("patch", f"courses/{bens.public_id}/", {"title": "hacked"}),
            ("delete", f"lessons/{lesson.public_id}/", None),
        ):
            with self.subTest(method=method, tail=tail):
                foreign = self.req(method, self.url(tail, self.ben_site_id), body)
                missing = self.req(method, self.url(tail, "site_00000000000000000000"), body)
                self.assertEqual((foreign.status_code, foreign.json()), (404, NOT_FOUND))
                self.assertEqual(foreign.content, missing.content)
        self.assertEqual((Course.objects.count(), Module.objects.count(), Lesson.objects.count()), before)

    def test_another_sites_ids_under_my_own_site_are_the_same_404_as_missing_ones(self):
        bens, module, lesson = self.tree(self.ben_site)
        live = LiveTraining.objects.create(site=self.ben_site, title="Bens", starts_at=timezone.now())
        mine = make_course(self.site)
        missing = {"crs": "crs_00000000000000000000", "mdl": "mdl_00000000000000000000",
                   "lsn": "lsn_00000000000000000000", "liv": "liv_00000000000000000000"}
        cases = (
            ("get", "courses/{crs}/", None), ("patch", "courses/{crs}/", {"title": "hacked"}),
            ("delete", "courses/{crs}/", None), ("delete", "courses/{crs}/image/", None),
            ("post", "courses/{crs}/modules/", {"title": "x"}),
            ("post", "courses/{crs}/modules/reorder/", {"ids": []}),
            ("patch", "modules/{mdl}/", {"title": "hacked"}), ("delete", "modules/{mdl}/", None),
            ("post", "modules/{mdl}/lessons/", {"title": "x"}),
            ("post", "modules/{mdl}/lessons/reorder/", {"ids": []}),
            ("patch", "lessons/{lsn}/", {"title": "hacked"}), ("delete", "lessons/{lsn}/", None),
            ("delete", "lessons/{lsn}/image/", None),
            ("patch", "live/{liv}/", {"title": "hacked"}), ("delete", "live/{liv}/", None),
            ("delete", "live/{liv}/thumbnail/", None),
        )
        before = (Course.objects.count(), Module.objects.count(), Lesson.objects.count(), LiveTraining.objects.count())
        for method, tail, body in cases:
            foreign_ids = {"crs": bens.public_id, "mdl": module.public_id, "lsn": lesson.public_id, "liv": live.public_id}
            with self.subTest(method=method, tail=tail):
                foreign = self.req(method, self.url(tail.format(**foreign_ids)), body)
                gone = self.req(method, self.url(tail.format(**missing)), body)
                self.assertEqual((foreign.status_code, foreign.json()), (404, NOT_FOUND))
                self.assertEqual(foreign.content, gone.content)
        with FakeMedia():
            for tail in (f"courses/{bens.public_id}/image/", f"lessons/{lesson.public_id}/image/", f"live/{live.public_id}/thumbnail/"):
                self.assertEqual(self.upload(tail).status_code, 404)
        self.assertEqual((Course.objects.count(), Module.objects.count(), Lesson.objects.count(), LiveTraining.objects.count()), before)
        bens.refresh_from_db()
        self.assertEqual((bens.title, bens.image_key), ("Ways to Earn", ""))
        self.assertEqual(mine.modules.count(), 0)

    def test_a_reorder_cannot_name_another_sites_course(self):
        bens, _, _ = self.tree(self.ben_site)
        mine = make_course(self.site, "Mine")
        response = self.send("post", self.url("courses/reorder/"), {"ids": [mine.public_id, bens.public_id]})
        self.assertEqual(response.status_code, 400, response.content)

    def test_ben_cannot_reach_anas_site(self):
        self.tree()
        self.assertEqual(self.get(self.url("courses/"), client=self.ben).status_code, 404)
        self.assertEqual(self.send("post", self.url("courses/"), {"title": "x"}, client=self.ben).status_code, 404)
        self.assertEqual(Course.objects.count(), 1)


class ImageTests(TrainingOwnerCase):
    def test_a_picture_is_stored_replaced_and_cleared_for_a_course_a_lesson_and_a_live(self):
        course, _, lesson = self.tree()
        live = LiveTraining.objects.create(site=self.site, title="Boot", starts_at=timezone.now())
        targets = (
            (f"courses/{course.public_id}/image/", course, "image_key"),
            (f"lessons/{lesson.public_id}/image/", lesson, "image_key"),
            (f"live/{live.public_id}/thumbnail/", live, "thumbnail_key"),
        )
        with override_settings(PUBLIC_MEDIA_BASE_URL="https://media.example.test", PUBLIC_BLOB_READ_WRITE_TOKEN="t"), \
                FakeMedia() as media:
            for tail, row, field in targets:
                with self.subTest(tail=tail):
                    response = self.upload(tail)
                    self.assertEqual(response.status_code, 200, response.content)
                    first = getattr(type(row).objects.get(pk=row.pk), field)
                    self.assertTrue(first.startswith("training/"))
                    url = response.json().get("image_url") or response.json().get("thumbnail_url")
                    self.assertEqual(url, f"https://media.example.test/{first}")
                    with self.captureOnCommitCallbacks(execute=True):
                        self.upload(tail)
                    second = getattr(type(row).objects.get(pk=row.pk), field)
                    self.assertNotEqual(first, second)
                    self.assertIn(first, media.deleted)
                    with self.captureOnCommitCallbacks(execute=True):
                        cleared = self.send("delete", self.url(tail))
                    self.assertEqual(cleared.status_code, 200, cleared.content)
                    self.assertEqual(getattr(type(row).objects.get(pk=row.pk), field), "")
            self.assertEqual(media.files, {})

    def test_deleting_a_course_removes_its_pictures(self):
        course, _, lesson = self.tree()
        with FakeMedia() as media:
            self.upload(f"courses/{course.public_id}/image/")
            self.upload(f"lessons/{lesson.public_id}/image/")
            keys = {Course.objects.get(pk=course.pk).image_key, Lesson.objects.get(pk=lesson.pk).image_key}
            with self.captureOnCommitCallbacks(execute=True):
                self.send("delete", self.url(f"courses/{course.public_id}/"))
            self.assertEqual(set(media.deleted), keys)

    def test_a_wrong_type_stores_nothing(self):
        course = make_course(self.site)
        with FakeMedia() as media:
            response = self.upload(f"courses/{course.public_id}/image/", file=image_file(b"plain text", "text/plain", "x.txt"))
            self.assertEqual(response.status_code, 400, response.content)
            self.assertIn("file", response.json()["error"]["fields"])
            self.assertEqual(media.files, {})

    def test_no_store_configured_is_a_503_and_nothing_changes(self):
        course = make_course(self.site)
        response = self.upload(f"courses/{course.public_id}/image/")
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["error"]["code"], "image_store_not_configured")
        self.assertEqual(Course.objects.get(pk=course.pk).image_key, "")


class LiveTests(TrainingOwnerCase):
    def test_create_list_update_delete(self):
        made = self.send("post", self.url("live/"), {
            "title": " Bootcamp ", "starts_at": WHEN, "duration_minutes": 90, "join_url": "https://zoom.example/j/1",
        })
        self.assertEqual(made.status_code, 201, made.content)
        live = made.json()
        self.assertEqual((live["title"], live["duration_minutes"], live["join_url"], live["active"]),
                         ("Bootcamp", 90, "https://zoom.example/j/1", True))
        self.assertEqual(set(live), {"id", "title", "starts_at", "duration_minutes", "description", "join_url",
                                     "thumbnail_url", "active", "created_at", "updated_at"})
        self.assertEqual([x["id"] for x in self.get(self.url("live/")).json()["live"]], [live["id"]])
        patched = self.send("patch", self.url(f"live/{live['id']}/"), {"title": "Renamed", "join_url": ""})
        self.assertEqual((patched.json()["title"], patched.json()["join_url"]), ("Renamed", None))
        self.assertEqual(self.send("delete", self.url(f"live/{live['id']}/")).json(), {"status": "deleted"})
        self.assertFalse(LiveTraining.objects.exists())

    def test_bad_values_are_field_errors(self):
        for body, name in (
            ({"title": "x", "starts_at": "2026-12-01T10:00:00"}, "starts_at"),
            ({"title": "", "starts_at": WHEN}, "title"),
            ({"title": "x", "starts_at": WHEN, "duration_minutes": 0}, "duration_minutes"),
            ({"title": "x", "starts_at": WHEN, "join_url": "http://insecure.example"}, "join_url"),
            ({"title": "x", "starts_at": WHEN, "join_url": "javascript:alert(1)"}, "join_url"),
        ):
            with self.subTest(body=body):
                response = self.send("post", self.url("live/"), body)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(name, response.json()["error"]["fields"])
        for body in ({"title": "x", "starts_at": WHEN, "site": self.ben_site_id}, {"title": "x"}):
            self.assertEqual(self.send("post", self.url("live/"), body).status_code, 400)
        self.assertFalse(LiveTraining.objects.exists())

    def test_creating_one_active_rings_the_bell_of_this_sites_members_only(self):
        mine = Member.objects.create(site=self.site, full_name="M", mobile="+639171110001", email="m@x.test")
        Member.objects.create(site=self.ben_site, full_name="B", mobile="+639171110002", email="b@x.test")
        self.send("post", self.url("live/"), {"title": "Boot", "starts_at": WHEN, "active": False})
        self.assertEqual(MemberNotification.objects.count(), 0)
        live = self.send("post", self.url("live/"), {"title": "Boot", "starts_at": WHEN}).json()
        (row,) = MemberNotification.objects.all()
        self.assertEqual((row.member_id, row.kind, row.title), (mine.pk, "live", "Live training: Boot"))
        self.send("patch", self.url(f"live/{live['id']}/"), {"title": "Renamed"})
        self.assertEqual(MemberNotification.objects.count(), 1)

    def test_services_keep_the_site_rule(self):
        live = services.create_live(self.site, {"title": "Boot", "starts_at": timezone.now() + timedelta(days=1)})
        with self.assertRaises(Exception):
            services.update_live(self.ben_site, live.public_id, {"title": "x"})
