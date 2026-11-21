"""R2: copying a lesson's Stream video from the content source store's own library into the
distributor's own library, the Copying state until Bunny says ready, and the refusals."""
from contextlib import nullcontext
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.sante import content_copy
from apps.sante.models import SanteContentCopy
from apps.sante.tests.test_content_copy import CopyCase, SHARED, YT
from apps.meta_ads.tests.base import setUpModule, tearDownModule  # noqa: F401 - Celery runs in-process here (see that module)
from apps.training import stream
from apps.training.models import Course, Lesson, Module
from apps.training.tests.stream_fake import FakeBunny, set_library

SRC_LIB, DST_LIB = 816560, 111
SRC_GUID = "816560-source-guid-1"


class VideoCopyCase(CopyCase):
    def setUp(self):
        super().setUp()
        self.set_source()
        self.bunny = FakeBunny()
        self.src_row = set_library(self.source, SRC_LIB, "LIVE4MORE", "vz-live.b-cdn.net")
        self.dst_row = set_library(self.site, DST_LIB, "ANA", "vz-ana.b-cdn.net")
        self.bunny.put_ready(SRC_LIB, SRC_GUID, b"the-original-video")

    def course_with_videos(self, title="Videos"):
        course = Course.objects.create(site=self.source, title=title, slug=title.lower(), shared=True)
        module = Module.objects.create(course=course, title="M")
        self.stream_lesson = Lesson.objects.create(
            module=module, title="Streamed", order=1, stream_library_id=SRC_LIB,
            stream_video_id=SRC_GUID, stream_status="ready", stream_bytes=5000,
        )
        self.legacy_lesson = Lesson.objects.create(module=module, title="Legacy", order=2, video_url=YT)
        self.bare_lesson = Lesson.objects.create(module=module, title="Bare", order=3)
        return course

    def run_copy(self, ids, site=None, status=202, hold_poll=False, **extra):
        poll = mock.patch.object(content_copy, "_schedule_poll") if hold_poll else nullcontext()
        with self.bunny, poll as scheduled:
            body = self.copy({"kind": "course", "ids": ids, **extra}, site=site, status=status)
        return body, scheduled

    def mine(self):
        return Course.objects.get(site=self.site)


class StreamCopyTests(VideoCopyCase):
    def test_a_ready_stream_video_becomes_a_new_video_in_the_distributors_own_library(self):
        course = self.course_with_videos()
        body, _ = self.run_copy([course.public_id])
        self.assertEqual(body["queued"][0]["copy_state"], "copying")
        lesson = Lesson.objects.get(module__course=self.mine(), title="Streamed")
        self.assertEqual(lesson.stream_library_id, DST_LIB)
        self.assertNotEqual(lesson.stream_video_id, SRC_GUID)
        self.assertEqual(
            [c for c in self.bunny.calls if c[0] in ("create", "upload")], [("create", DST_LIB), ("upload", DST_LIB)]
        )
        self.assertIn(("download", SRC_LIB), self.bunny.calls)
        self.assertEqual(self.bunny.videos[(DST_LIB, lesson.stream_video_id)]["bytes"], b"the-original-video")
        # the source lesson and its video are untouched
        self.stream_lesson.refresh_from_db()
        self.assertEqual((self.stream_lesson.stream_library_id, self.stream_lesson.stream_video_id), (SRC_LIB, SRC_GUID))
        self.assertIn((SRC_LIB, SRC_GUID), self.bunny.videos)
        self.assertEqual(self.bunny.deleted, [])

    def test_the_item_reads_copying_until_bunny_says_ready_then_copied(self):
        course = self.course_with_videos()
        self.bunny.next_status = stream.PROCESSING
        _, scheduled = self.run_copy([course.public_id], hold_poll=True)
        self.assertEqual(self.list()["courses"][0]["copy_state"], "copying")
        self.assertEqual(self.list()["courses"][0]["copy_id"], None)
        copy_row = SanteContentCopy.objects.get(site=self.site)
        scheduled.assert_called_once_with(copy_row.pk, 0)
        lesson = Lesson.objects.get(module__course=self.mine(), title="Streamed")
        self.assertEqual(lesson.stream_status, "processing")
        # still processing: another look is scheduled, nothing changes
        with self.bunny, mock.patch.object(content_copy, "_schedule_poll") as again:
            content_copy.poll_videos(copy_row.pk, 0)
        again.assert_called_once_with(copy_row.pk, 1)
        self.assertEqual(self.list()["courses"][0]["copy_state"], "copying")
        # Bunny says ready
        self.bunny.videos[(DST_LIB, lesson.stream_video_id)]["status_override"] = stream.FINISHED
        self.bunny.videos[(DST_LIB, lesson.stream_video_id)]["storage"] = 7777
        with self.bunny:
            content_copy.poll_videos(copy_row.pk, 1)
        self.assertEqual(self.list()["courses"][0]["copy_state"], "copied")
        self.assertEqual(self.list()["courses"][0]["copy_id"], self.mine().public_id)
        lesson.refresh_from_db()
        self.assertEqual((lesson.stream_status, lesson.stream_bytes), ("ready", 7777))

    def test_links_and_lessons_without_video_arrive_as_add_your_video(self):
        course = self.course_with_videos()
        self.run_copy([course.public_id])
        got = {x.title: x for x in Lesson.objects.filter(module__course=self.mine())}
        self.assertEqual((got["Streamed"].needs_video, got["Streamed"].video_url), (False, ""))
        for title in ("Legacy", "Bare"):
            self.assertEqual((got[title].needs_video, got[title].video_url, got[title].stream_video_id), (True, "", ""))

    def test_a_ready_copy_reads_copied_with_the_streams_bytes_counted(self):
        course = self.course_with_videos()
        self.assertEqual(self.list()["courses"][0]["bytes"], 5000)
        self.run_copy([course.public_id])
        self.assertEqual(self.list()["courses"][0]["copy_state"], "copied")
        self.assertEqual(self.mine().public_id, self.list()["courses"][0]["copy_id"])

    def test_deleting_the_source_video_never_touches_the_copy_and_the_other_way_round(self):
        from apps.training import video

        course = self.course_with_videos()
        self.run_copy([course.public_id])
        copy = Lesson.objects.get(module__course=self.mine(), title="Streamed")
        with self.bunny:
            video.discard_if_unreferenced(SRC_LIB, SRC_GUID)  # still named by the source lesson: kept
            self.assertEqual(self.bunny.deleted, [])
            self.stream_lesson.delete()
            video.discard_if_unreferenced(SRC_LIB, SRC_GUID)
            self.assertEqual(self.bunny.deleted, [(SRC_LIB, SRC_GUID)])
            self.assertIn((DST_LIB, copy.stream_video_id), self.bunny.videos)


class RefusalTests(VideoCopyCase):
    def test_an_unconfigured_target_refuses_a_course_with_stream_videos_with_the_503_message(self):
        course = self.course_with_videos()
        self.dst_row.delete()
        before = list(self.bunny.calls)
        with self.bunny:
            r = self.post(SHARED.format(self.site.public_id) + "copy/", {"kind": "course", "ids": [course.public_id]})
        self.assertEqual(r.status_code, 503, r.content)
        self.assertEqual(r.json()["error"]["message"], "Video uploads are not available yet.")
        self.assertFalse(Course.objects.filter(site=self.site).exists())
        self.assertFalse(SanteContentCopy.objects.exists())
        self.assertEqual(self.bunny.calls, before)

    def test_other_items_still_copy_when_one_is_refused(self):
        refused = self.course_with_videos()
        plain = self.course(title="Plain", site=self.source)  # links only: needs no Stream library
        self.dst_row.delete()
        body, _ = self.run_copy([refused.public_id, plain.public_id])
        self.assertEqual([q["id"] for q in body["queued"]], [plain.public_id])
        self.assertEqual(body["skipped"], [{"kind": "course", "id": refused.public_id, "reason": "video_unavailable"}])
        self.assertEqual(Course.objects.filter(site=self.site).count(), 1)

    def test_a_target_whose_key_variable_is_missing_counts_as_not_configured(self):
        import os

        course = self.course_with_videos()
        with self.bunny, mock.patch.dict(os.environ, {}):
            os.environ.pop("BUNNY_STREAM_KEY_ANA")
            r = self.post(SHARED.format(self.site.public_id) + "copy/", {"kind": "course", "ids": [course.public_id]})
        self.assertEqual(r.status_code, 503)

    def test_an_unconfigured_source_library_makes_the_lesson_arrive_as_needs_video(self):
        course = self.course_with_videos()
        self.src_row.delete()
        self.run_copy([course.public_id])
        got = {x.title: x for x in Lesson.objects.filter(module__course=self.mine())}
        self.assertEqual((got["Streamed"].needs_video, got["Streamed"].stream_video_id), (True, ""))
        self.assertFalse([c for c in self.bunny.calls if c[0] in ("create", "download", "upload")])
        self.assertEqual(self.list()["courses"][0]["copy_state"], "copied")


class FailureTests(VideoCopyCase):
    def test_a_failed_download_leaves_no_course_and_removes_the_new_video(self):
        course = self.course_with_videos()
        self.bunny.fail = {"download"}
        self.run_copy([course.public_id])
        self.assertFalse(Course.objects.filter(site=self.site).exists())
        self.assertEqual(self.list()["courses"][0]["copy_state"], "failed")
        self.assertEqual(len(self.bunny.deleted), 1)
        self.assertEqual(self.bunny.deleted[0][0], DST_LIB)  # the new one only; the source is untouched
        self.assertIn((SRC_LIB, SRC_GUID), self.bunny.videos)

    def test_a_failed_upload_also_removes_the_new_video(self):
        course = self.course_with_videos()
        self.bunny.fail = {"upload"}
        self.run_copy([course.public_id])
        self.assertFalse(Course.objects.filter(site=self.site).exists())
        self.assertEqual([lib for lib, _ in self.bunny.deleted], [DST_LIB])

    def test_a_video_bunny_reports_failed_removes_the_whole_copy(self):
        course = self.course_with_videos()
        self.bunny.next_status = stream.PROCESSING
        self.run_copy([course.public_id], hold_poll=True)
        lesson = Lesson.objects.get(module__course=self.mine(), title="Streamed")
        guid = lesson.stream_video_id
        self.bunny.videos[(DST_LIB, guid)]["status_override"] = stream.ERROR
        row = SanteContentCopy.objects.get(site=self.site)
        with self.bunny, self.captureOnCommitCallbacks(execute=True):
            content_copy.poll_videos(row.pk, 3)
        self.assertFalse(Course.objects.filter(site=self.site).exists())
        self.assertEqual(self.list()["courses"][0]["copy_state"], "failed")
        self.assertEqual(self.bunny.deleted, [(DST_LIB, guid)])

    def test_a_copy_that_outlives_the_age_limit_is_removed_and_reads_failed(self):
        course = self.course_with_videos()
        self.bunny.next_status = stream.PROCESSING
        self.run_copy([course.public_id], hold_poll=True)
        row = SanteContentCopy.objects.get(site=self.site)
        SanteContentCopy.objects.filter(pk=row.pk).update(
            requested_at=timezone.now() - content_copy.STALE_AFTER - timedelta(minutes=1)
        )
        with self.bunny, self.captureOnCommitCallbacks(execute=True):
            content_copy.poll_videos(row.pk, 9)
        self.assertFalse(Course.objects.filter(site=self.site).exists())
        self.assertEqual(SanteContentCopy.objects.get(pk=row.pk).state, "failed")

    def test_a_poll_on_a_closed_or_unknown_copy_does_nothing(self):
        with self.bunny:
            content_copy.poll_videos(999999, 0)
        self.assertFalse(self.bunny.calls)
