"""Fix round 2: a course is shown to members only when every lesson has a ready Stream video; the audit
and backfill commands. No real Bunny call is made."""
import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from apps.training import video
from apps.training.models import Course, Lesson
from apps.training.tests.base import YT, make_course, make_lesson, make_module
from apps.training.tests.stream_fake import FakeBunny, set_library
from apps.training.tests.test_stream import StreamCase

LIB = 111
GUID = "11111111-2222-3333-4444-555555555555"
def ready(guid="g-ready-1"):
    return {"stream_library_id": LIB, "stream_video_id": guid, "stream_status": "ready"}


class RequiredCase(StreamCase):
    def setUp(self):
        super().setUp()
        self.course = make_course(self.site, "Required", active=False)
        self.mod = make_module(self.course, "M1")
        self.good = make_lesson(self.mod, "Has video", video_url="", **ready())
        self.bad = make_lesson(self.mod, "No video", video_url="", order=2)

    def course_url(self, course=None):
        return f"/v1/sites/{self.site_id}/training/courses/{(course or self.course).public_id}/"

    def patch_path(self, path, data):
        return self.send("patch", path, data)

    def assert_required(self, response, *lessons):
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "video_required")
        self.assertEqual(body["detail"], "Every lesson needs a video before members can see this course.")
        self.assertEqual(body["lessons"], [{"id": x.public_id, "title": x.title} for x in lessons])
        self.assertEqual(body["error"]["code"], "video_required")


class PublishTests(RequiredCase):
    def test_turning_a_course_on_is_refused_while_a_lesson_has_no_ready_video(self):
        r = self.patch_path(self.course_url(), {"active": True})
        self.assert_required(r, self.bad)
        self.course.refresh_from_db()
        self.assertFalse(self.course.active)

    def test_legacy_links_pending_and_failed_videos_do_not_count(self):
        legacy = make_lesson(self.mod, "Legacy", video_url=YT, order=3)
        pending = make_lesson(self.mod, "Pending", video_url="", order=4, stream_library_id=LIB,
                              stream_video_id="g-p", stream_status="processing")
        failed = make_lesson(self.mod, "Failed", video_url="", order=5, stream_library_id=LIB,
                             stream_video_id="g-f", stream_status="failed")
        self.assert_required(self.patch_path(self.course_url(), {"active": True}), self.bad, legacy, pending, failed)

    def test_it_turns_on_when_every_enabled_lesson_has_a_ready_video(self):
        Lesson.objects.filter(pk=self.bad.pk).update(**ready("g-ready-2"))
        r = self.patch_path(self.course_url(), {"active": True})
        self.assertEqual((r.status_code, r.json()["active"]), (200, True))

    def test_disabled_lessons_and_modules_are_not_counted(self):
        Lesson.objects.filter(pk=self.bad.pk).update(active=False)
        off = make_module(self.course, "Off", active=False)
        make_lesson(off, "In off module", video_url="")
        self.assertEqual(self.patch_path(self.course_url(), {"active": True}).status_code, 200)

    def test_other_edits_and_turning_off_are_never_blocked(self):
        self.assertEqual(self.patch_path(self.course_url(), {"title": "New title"}).status_code, 200)
        Course.objects.filter(pk=self.course.pk).update(active=True)
        self.assertEqual(self.patch_path(self.course_url(), {"active": False}).status_code, 200)

    def test_unarchiving_with_active_true_is_checked_too(self):
        from django.utils import timezone

        Course.objects.filter(pk=self.course.pk).update(archived_at=timezone.now())
        self.assert_required(self.patch_path(self.course_url(), {"active": True}), self.bad)

    def test_a_new_empty_course_can_be_created_on(self):
        r = self.send("post", f"/v1/sites/{self.site_id}/training/courses/", {"title": "Fresh", "active": True})
        self.assertEqual((r.status_code, r.json()["active"]), (201, True))

    def test_turning_a_lesson_or_module_back_on_in_a_published_course_is_checked(self):
        Course.objects.filter(pk=self.course.pk).update(active=True)
        Lesson.objects.filter(pk=self.bad.pk).update(active=False)
        r = self.patch_path(f"/v1/sites/{self.site_id}/training/lessons/{self.bad.public_id}/", {"active": True})
        self.assert_required(r, self.bad)
        off = make_module(self.course, "Back", active=False)
        l2 = make_lesson(off, "Needs one", video_url="")
        r = self.patch_path(f"/v1/sites/{self.site_id}/training/modules/{off.public_id}/", {"active": True})
        self.assert_required(r, l2)
        Lesson.objects.filter(pk=l2.pk).update(**ready("g-ready-3"))
        r = self.patch_path(f"/v1/sites/{self.site_id}/training/modules/{off.public_id}/", {"active": True})
        self.assertEqual(r.status_code, 200)

    def test_in_an_unpublished_course_lessons_switch_freely(self):
        Lesson.objects.filter(pk=self.bad.pk).update(active=False)
        r = self.patch_path(f"/v1/sites/{self.site_id}/training/lessons/{self.bad.public_id}/", {"active": True})
        self.assertEqual(r.status_code, 200)

    def test_another_stores_course_is_still_a_404(self):
        theirs = make_course(self.ben_site, "Theirs", active=False)
        r = self.patch_path(self.course_url(theirs), {"active": True})
        self.assertEqual(r.status_code, 404)


class PublishedCourseEdgeTests(RequiredCase):
    def setUp(self):
        super().setUp()
        Lesson.objects.filter(pk=self.bad.pk).update(**ready("g-ready-2"))
        self.assertEqual(self.patch_path(self.course_url(), {"active": True}).status_code, 200)

    def test_adding_a_lesson_keeps_the_course_published_and_flags_it(self):
        r = self.send("post", f"/v1/sites/{self.site_id}/training/modules/{self.mod.public_id}/lessons/", {"title": "New"})
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["needs_video"])
        self.assertTrue(r.json()["video"]["needs_video"])
        self.course.refresh_from_db()
        self.assertTrue(self.course.active)
        tree = self.get(self.course_url()).json()
        self.assertEqual(tree["needs_video_count"], 1)

    def test_removing_a_video_keeps_the_course_published_and_flags_the_lesson(self):
        with self.bunny:
            set_library(self.site, LIB, "ANA")
            r = self.send("delete", self.url(self.good))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["video"]["needs_video"])
        self.course.refresh_from_db()
        self.assertTrue(self.course.active)
        self.assertTrue(Lesson.objects.filter(pk=self.good.pk).exists())

    def test_republishing_after_it_was_turned_off_is_checked_again(self):
        make_lesson(self.mod, "Late", video_url="", order=9)
        self.patch_path(self.course_url(), {"active": False})
        late = Lesson.objects.get(title="Late")
        self.assert_required(self.patch_path(self.course_url(), {"active": True}), late)


class NeedsVideoTests(RequiredCase):
    def test_needs_video_is_true_without_a_ready_or_coming_video_and_false_otherwise(self):
        legacy = make_lesson(self.mod, "L", video_url=YT, order=3)
        coming = make_lesson(self.mod, "C", video_url="", order=4, stream_library_id=LIB,
                             stream_video_id="g-c", stream_status="uploading")
        failed = make_lesson(self.mod, "F", video_url="", order=5, stream_library_id=LIB,
                             stream_video_id="g-f", stream_status="failed")
        self.assertEqual([video.needs_video(x) for x in (self.good, self.bad, legacy, coming, failed)],
                         [False, True, True, False, True])


class CopyArrivesOffTests(RequiredCase):
    def test_a_copy_cannot_be_published_until_every_lesson_has_a_video_and_keeps_its_link(self):
        course = make_course(self.site, "Copied", active=False, copied_from="crs_src")
        module = make_module(course, "M")
        linked = make_lesson(module, "Linked", video_url=YT, needs_video=True)
        r = self.patch_path(self.course_url(course), {"active": True})
        self.assert_required(r, linked)
        linked.refresh_from_db()
        self.assertEqual(linked.video_url, YT)


class ParseTests(RequiredCase):
    def test_bunny_links_parse_and_other_links_read_by_kind(self):
        for url in (f"https://iframe.mediadelivery.net/embed/{LIB}/{GUID}", f"https://iframe.mediadelivery.net/play/{LIB}/{GUID}/",
                    f"https://iframe.mediadelivery.net/embed/{LIB}/{GUID}?autoplay=false"):
            self.assertEqual(video.parse_bunny_link(url), (LIB, GUID))
        for url in ("https://example.com/embed/1/abcdefgh", "not a url", "", f"https://evil.net/embed/{LIB}/{GUID}"):
            self.assertIsNone(video.parse_bunny_link(url))
        kinds = {YT: "youtube", "https://player.vimeo.com/video/123": "vimeo", "https://x.example/v": "other-link", "": "none",
                 f"https://iframe.mediadelivery.net/embed/{LIB}/{GUID}": "bunny-embed-link"}
        for url, kind in kinds.items():
            self.assertEqual(video.link_kind(url), kind)


class AuditCommandTests(RequiredCase):
    def run_audit(self, *args):
        out = io.StringIO()
        call_command("training_video_audit", *args, stdout=out)
        return [line.split("\t") for line in out.getvalue().splitlines()]

    def test_one_row_per_lesson_with_kinds_and_library_and_guid(self):
        make_lesson(self.mod, "Bunny link", video_url=f"https://iframe.mediadelivery.net/embed/{LIB}/{GUID}", order=3)
        make_lesson(self.mod, "YT", video_url=YT, order=4)
        make_lesson(self.mod, "Coming", video_url="", order=5, stream_library_id=LIB, stream_video_id="g-c", stream_status="processing")
        rows = self.run_audit("--site", self.site_id)
        self.assertEqual(rows[0], ["store", "course", "course_published", "module", "lesson", "video_kind", "library_id", "video_guid"])
        by_lesson = {r[4]: r for r in rows[1:]}
        self.assertEqual(len(rows) - 1, 6)  # the 5 here plus the base lesson "Welcome"
        self.assertEqual(by_lesson["Has video"][5:], ["stream-ready", str(LIB), "g-ready-1"])
        self.assertEqual(by_lesson["No video"][5:], ["none", "", ""])
        self.assertEqual(by_lesson["Bunny link"][5:], ["bunny-embed-link", str(LIB), GUID])
        self.assertEqual(by_lesson["YT"][5], "youtube")
        self.assertEqual(by_lesson["Coming"][5], "stream-pending")
        self.assertEqual(by_lesson["Has video"][:4], [f"{self.site_id} ({self.site.display_name})", "Required", "no", "M1"])
        self.assertTrue(rows[1][0].startswith(self.site_id))

    def test_without_a_site_every_store_and_with_one_only_its_own_and_it_writes_nothing(self):
        make_lesson(make_module(make_course(self.ben_site, "Bens")), "Ben lesson")
        all_rows = self.run_audit()
        self.assertIn("Ben lesson", [r[4] for r in all_rows])
        mine = self.run_audit("--site", self.site_id)
        self.assertNotIn("Ben lesson", [r[4] for r in mine])
        before = list(Lesson.objects.values_list("pk", "updated_at", "video_url", "stream_status"))
        self.run_audit()
        self.assertEqual(before, list(Lesson.objects.values_list("pk", "updated_at", "video_url", "stream_status")))
        with self.assertRaises(CommandError):
            self.run_audit("--site", "site_nope")

    def test_published_reads_yes_only_for_an_active_unarchived_course(self):
        Course.objects.filter(pk=self.course.pk).update(active=True)
        rows = {r[4]: r for r in self.run_audit("--site", self.site_id)[1:]}
        self.assertEqual(rows["Has video"][2], "yes")


class BackfillTests(RequiredCase):
    def setUp(self):
        super().setUp()
        self.row = set_library(self.site, LIB, "ANA")
        self.link = f"https://iframe.mediadelivery.net/embed/{LIB}/{GUID}"
        self.lesson = make_lesson(self.mod, "Linked", video_url=self.link, order=3)
        self.other = make_lesson(self.mod, "Elsewhere", video_url=f"https://iframe.mediadelivery.net/embed/999/{GUID}", order=4)
        self.yt = make_lesson(self.mod, "YT", video_url=YT, order=5)

    def run_cmd(self, *args):
        out = io.StringIO()
        call_command("training_stream_backfill", "--site", self.site_id, *args, stdout=out)
        return out.getvalue()

    def snapshot(self):
        return list(Lesson.objects.order_by("pk").values())

    def test_dry_run_prints_the_plan_and_writes_and_asks_nothing(self):
        before = self.snapshot()
        with self.bunny:
            out = self.run_cmd()
        self.assertIn("DRY RUN", out)
        self.assertIn(self.lesson.public_id, out)
        self.assertNotIn(self.other.public_id, out)
        self.assertNotIn(self.yt.public_id, out)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.bunny.calls, [])

    def test_apply_marks_it_ready_when_bunny_confirms_and_keeps_the_link(self):
        self.bunny.videos[(LIB, GUID)] = {"status": 4, "storage": 4321, "bytes": b""}
        with self.bunny:
            out = self.run_cmd("--apply")
        self.lesson.refresh_from_db()
        self.assertEqual((self.lesson.stream_library_id, self.lesson.stream_video_id, self.lesson.stream_status),
                         (LIB, GUID, "ready"))
        self.assertEqual((self.lesson.stream_bytes, self.lesson.video_url), (4321, self.link))
        self.assertIn("status ready", out)
        self.other.refresh_from_db()
        self.assertEqual(self.other.stream_video_id, "")
        self.assertEqual(Lesson.objects.count(), 6)
        self.assertEqual(self.bunny.deleted, [])

    def test_apply_marks_it_processing_when_bunny_does_not_answer(self):
        self.bunny.fail.add("check")
        self.bunny.videos[(LIB, GUID)] = {"status": 4, "storage": 1, "bytes": b""}
        with self.bunny:
            self.run_cmd("--apply")
        self.lesson.refresh_from_db()
        self.assertEqual((self.lesson.stream_status, self.lesson.video_url), ("processing", self.link))

    def test_a_lesson_that_already_has_a_stream_video_is_left_alone(self):
        Lesson.objects.filter(pk=self.lesson.pk).update(stream_library_id=LIB, stream_video_id="mine", stream_status="ready")
        with self.bunny:
            out = self.run_cmd("--apply")
        self.assertIn("0 lesson(s) match", out)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.stream_video_id, "mine")

    def test_a_store_without_a_library_or_an_unknown_store_is_refused(self):
        self.row.delete()
        with self.assertRaises(CommandError):
            self.run_cmd()
        with self.assertRaises(CommandError):
            call_command("training_stream_backfill", "--site", "site_nope", stdout=io.StringIO())

    def test_another_stores_library_link_is_not_touched(self):
        set_library(self.ben_site, 222, "BEN")
        make_lesson(make_module(make_course(self.ben_site, "Bens")), "Ben link",
                    video_url=f"https://iframe.mediadelivery.net/embed/{LIB}/{GUID}")
        self.bunny.videos[(LIB, GUID)] = {"status": 4, "storage": 1, "bytes": b""}
        with self.bunny:
            self.run_cmd("--apply")
        self.assertEqual(Lesson.objects.get(title="Ben link").stream_video_id, "")
