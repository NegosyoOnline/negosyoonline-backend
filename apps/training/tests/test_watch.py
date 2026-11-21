"""R2: watch progress (member POST, furthest percent only) and the owner's per-course view, plus how
a lesson reads to a member (Stream embed from the lesson's own library, or a legacy link)."""
from django.core.cache import cache
from django.test import override_settings

from apps.members.tests.base import NOT_FOUND, OTHER_HOST, MembersTestCase
from apps.sites.tests.api_base import SitesApiTestCase
from apps.training.models import LessonProgress
from apps.training.tests.base import YT, make_course, make_course_with_lessons, make_lesson, make_module

PREFIX = "training/"


class WatchCase(MembersTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in()
        self.course, self.lessons = make_course_with_lessons(self.site, (2,))

    def call(self, path, token=None, **extra):
        return self.post(PREFIX + path, token=self.token if token is None else token, **extra)

    def watch(self, lesson, percent, **extra):
        return self.call(f"lessons/{lesson.public_id}/watch/", percent=percent, **extra)

    def row(self, lesson=None):
        return LessonProgress.objects.get(member=self.member, lesson=lesson or self.lessons[0])


class MemberWatchTests(WatchCase):
    def test_it_creates_the_open_row_and_keeps_the_maximum_only(self):
        self.assertEqual(self.watch(self.lessons[0], 30).json(), {"percent": 30})
        self.assertEqual(self.watch(self.lessons[0], 10).json()["percent"], 30)
        self.assertEqual(self.watch(self.lessons[0], 30).json()["percent"], 30)
        self.assertEqual(self.watch(self.lessons[0], 100).json()["percent"], 100)
        self.assertEqual(self.watch(self.lessons[0], 0).json()["percent"], 100)
        row = self.row()
        self.assertEqual(row.watched_percent, 100)
        self.assertIsNotNone(row.watched_at)

    def test_watching_never_completes_the_lesson_or_unlocks_the_next(self):
        self.watch(self.lessons[0], 100)
        self.assertIsNone(self.row().completed_at)
        self.assertEqual(self.call(f"lessons/{self.lessons[1].public_id}/").status_code, 409)
        self.call(f"lessons/{self.lessons[0].public_id}/complete/")
        done = self.row().completed_at
        self.watch(self.lessons[0], 100)
        self.assertEqual(self.row().completed_at, done)  # a later watch leaves it alone

    def test_a_percent_outside_zero_to_hundred_or_not_a_whole_number_is_refused(self):
        for bad in (-1, 101, 5.5, "50", None, True):
            with self.subTest(percent=bad):
                r = self.watch(self.lessons[0], bad)
                self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(LessonProgress.objects.exists())

    def test_a_locked_lesson_cannot_be_watched(self):
        r = self.watch(self.lessons[1], 50)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "lesson_locked"))
        self.assertFalse(LessonProgress.objects.exists())

    def test_it_is_rate_limited_per_member(self):
        cache.clear()
        codes = [self.watch(self.lessons[0], 1).status_code for _ in range(61)]
        self.assertEqual(codes[:60], [200] * 60)
        self.assertEqual(codes[60], 429)
        cache.clear()

    def test_it_needs_a_signed_in_member_of_this_site(self):
        self.assertEqual(self.watch(self.lessons[0], 10, token="nope").status_code, 401)
        self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550120", name="Ben")
        ben = self.sign_in("ben@buyer.test", host=OTHER_HOST)
        # a member of another site cannot reach this site's lesson, and nothing is written
        r = self.call(f"lessons/{self.lessons[0].public_id}/watch/", token=ben, host=OTHER_HOST, percent=50)
        self.assertEqual((r.status_code, r.json()), (404, NOT_FOUND))
        self.assertFalse(LessonProgress.objects.exists())

    def test_an_inactive_or_foreign_lesson_is_not_found(self):
        elsewhere = make_lesson(make_module(make_course(self.other_site, "Elsewhere")), "E")
        self.assertEqual(self.watch(elsewhere, 10).status_code, 404)


class MemberReadTests(WatchCase):
    def test_a_ready_stream_lesson_reads_the_embed_of_its_own_library_and_no_legacy_link(self):
        lesson = make_lesson(
            make_module(make_course(self.site, "Streams")), "S", video_url=YT,
            stream_library_id=816560, stream_video_id="abc-12345678", stream_status="ready",
        )
        body = self.call(f"lessons/{lesson.public_id}/").json()
        self.assertEqual(body["video"], {
            "kind": "stream", "status": "ready", "needs_video": False,
            "embed_url": "https://iframe.mediadelivery.net/embed/816560/abc-12345678",
        })
        self.assertIsNone(body["video_url"])

    def test_a_legacy_lesson_still_reads_its_link_and_a_bare_lesson_reads_none(self):
        body = self.call(f"lessons/{self.lessons[0].public_id}/").json()
        self.assertEqual((body["video_url"], body["video"]["kind"]), (YT, "legacy"))
        bare = make_lesson(make_module(make_course(self.site, "Bare")), "B", video_url="", needs_video=True)
        body = self.call(f"lessons/{bare.public_id}/").json()
        self.assertEqual((body["video_url"], body["video"]["kind"], body["video"]["needs_video"]), (None, "none", True))

    def test_a_video_still_processing_has_no_embed_yet(self):
        lesson = make_lesson(
            make_module(make_course(self.site, "Wait")), "W", video_url="",
            stream_library_id=816560, stream_video_id="abc-12345678", stream_status="processing",
        )
        body = self.call(f"lessons/{lesson.public_id}/").json()
        self.assertEqual((body["video"]["status"], body["video"]["embed_url"]), ("processing", None))


@override_settings(MEMBERS_ENABLED=True)
class OwnerWatchTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        from apps.members import services as members
        from apps.sites.models import Site

        cache.clear()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)
        self.course, self.lessons = make_course_with_lessons(self.site, (2,))
        self.ana = members.create_member(self.site, full_name="Ana Reyes", mobile="09171234567", email="ana@buyer.test", email_state="pending")
        self.cy = members.create_member(self.site, full_name="Cy Dela Cruz", mobile="09171234568", email="cy@buyer.test", email_state="pending")
        self.ben_member = members.create_member(self.ben_site, full_name="Ben's Member", mobile="09171234569", email="bm@buyer.test", email_state="pending")

    def url(self, course=None, site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/training/courses/{(course or self.course).public_id}/watch/"

    def test_it_lists_each_lesson_with_its_members_percent_and_completed(self):
        from django.utils import timezone

        LessonProgress.objects.create(member=self.ana, lesson=self.lessons[0], watched_percent=70, completed_at=timezone.now())
        LessonProgress.objects.create(member=self.cy, lesson=self.lessons[0], watched_percent=20)
        LessonProgress.objects.create(member=self.ana, lesson=self.lessons[1], watched_percent=5)
        body = self.get(self.url()).json()
        self.assertEqual(body["course_id"], self.course.public_id)
        first, second = body["lessons"]
        self.assertEqual((first["lesson_id"], first["title"]), (self.lessons[0].public_id, "L1.1"))
        self.assertEqual(first["members"], [
            {"member_id": self.ana.public_id, "name": "Ana Reyes", "percent": 70, "completed": True},
            {"member_id": self.cy.public_id, "name": "Cy Dela Cruz", "percent": 20, "completed": False},
        ])
        self.assertEqual(second["members"], [{"member_id": self.ana.public_id, "name": "Ana Reyes", "percent": 5, "completed": False}])

    def test_another_sites_members_never_appear_even_with_progress_on_this_lesson(self):
        LessonProgress.objects.create(member=self.ben_member, lesson=self.lessons[0], watched_percent=99)
        body = self.get(self.url()).json()
        self.assertEqual(body["lessons"][0]["members"], [])
        self.assertNotIn("Ben's Member", str(body))

    def test_a_foreign_course_or_site_is_not_found(self):
        self.assertEqual(self.get(self.url(), client=self.ben).status_code, 404)
        self.assertEqual(self.get(self.url(site_id=self.ben_site_id), client=self.ben).status_code, 404)
        self.assertEqual(self.get(self.url(site_id=self.ben_site_id)).status_code, 404)
