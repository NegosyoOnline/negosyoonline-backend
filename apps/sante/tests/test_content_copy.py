"""CP-1: the content source, the share switch, the offered list, copying, and the delete guard."""
import io
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog import media
from apps.catalog.tests.base import MEDIA
from apps.common import file_refs
from apps.media_library.models import MediaAsset
from apps.operators.models import OperatorAuditEntry
from apps.sante import content_copy, services
from apps.sante.models import SanteContentCopy, SanteContentSource
from apps.sante.tests.base import operator, retemplate
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.training.models import Course, Lesson, Module

BUNNY = "https://iframe.mediadelivery.net/embed/744094/abc-123"
YT = "https://www.youtube.com/embed/dQw4w9WgXcQ"
SHARED = "/v1/sites/{}/shared-content/"


class FakeBlob:
    """media.copy / media.discard replaced by a dict. `.files` holds what is stored."""

    def __init__(self, files=None, fail_on=None):
        self.files = dict(files or {})
        self.deleted, self.fail_on, self.calls = [], fail_on, 0

    def _copy(self, src, dest):
        self.calls += 1
        if self.fail_on and self.calls == self.fail_on:
            raise media.StoreError("boom")
        if src not in self.files:
            raise media.StoreError("missing")
        self.files[dest] = self.files[src]
        return dest

    def _discard(self, key):
        if key:
            self.deleted.append(key)
            self.files.pop(key, None)

    def __enter__(self):
        self.settings = override_settings(**MEDIA)
        self.settings.enable()
        self.patches = [
            mock.patch("apps.catalog.media.copy", side_effect=self._copy),
            mock.patch("apps.catalog.media.discard", side_effect=self._discard),
        ]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()
        self.settings.disable()


@override_settings(MEMBERS_ENABLED=True, SANTE_SETTINGS_ENABLED=True)
class CopyCase(SitesApiTestCase):
    """Ben owns the source store (self.source), Ana is a distributor (self.site), Cy a third."""

    def setUp(self):
        super().setUp()
        self.site = Site.objects.get(public_id=self.create_site()["id"])
        self.ben = self.other_client("ben@example.test")
        self.source = Site.objects.get(
            public_id=self.create_site(client=self.ben, subdomain="live4more", display_name="Live4More")["id"]
        )
        self.cy = self.other_client("cy@example.test")
        self.cy_site = Site.objects.get(
            public_id=self.create_site(client=self.cy, subdomain="cys-store", display_name="Cy's")["id"]
        )
        self.op = operator()
        self.blob = FakeBlob()

    # -- builders
    def set_source(self, site=None):
        services.set_content_source(site or self.source, operator=self.op)

    def course(self, title="Ways to Earn", site=None, **fields):
        site = site or self.source
        values = {"slug": title.lower().replace(" ", "-"), "shared": True, **fields}
        course = Course.objects.create(site=site, title=title, **values)
        if course.image_key:
            self.blob.files[course.image_key] = b"course-image"
        module = Module.objects.create(course=course, title="Start")
        lesson_key = f"training/{title.replace(' ', '')}-lesson.png"
        self.blob.files[lesson_key] = b"lesson-image"
        Lesson.objects.create(module=module, title="L1", video_url=YT, image_key=lesson_key, order=1)
        Lesson.objects.create(module=module, title="L2", video_url=BUNNY, order=2)
        Lesson.objects.create(module=module, title="Off", video_url=YT, order=3, active=False)
        return course

    def asset(self, title="Promo", site=None, size=1000, **fields):
        site = site or self.source
        values = {
            "asset_type": "video", "shared": True, "file_size": size,
            "file_key": f"media-library/{site.public_id}/{title}/f.mp4",
            "thumb_key": f"media-library/{site.public_id}/{title}/t.png",
            "poster_key": f"media-library/{site.public_id}/{title}/p.png",
            "published_at": timezone.now() - timedelta(days=1), **fields,
        }
        for name in ("file_key", "thumb_key", "poster_key"):
            self.blob.files[values[name]] = name.encode()
        return MediaAsset.objects.create(site=site, title=title, **values)

    def delete(self, path, client=None):
        return self.send("delete", path, None, client=client)

    def list(self, client=None, site=None):
        response = self.get(SHARED.format((site or self.site).public_id), client=client)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def copy(self, body, client=None, site=None, status=202):
        with self.blob:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post(SHARED.format((site or self.site).public_id) + "copy/", body, client=client)
        self.assertEqual(response.status_code, status, response.content)
        return response.json()


class SourceTests(CopyCase):
    def entries(self):
        return list(OperatorAuditEntry.objects.filter(action="sante_content_source_set"))

    def test_set_and_clear_write_one_audit_entry_each(self):
        self.set_source()
        self.assertEqual(services.content_source_site().pk, self.source.pk)
        self.set_source(self.cy_site)
        services.set_content_source(None, operator=self.op)
        self.assertIsNone(services.content_source_site())
        entries = self.entries()
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].detail, {"old_site": None, "new_site": self.source.public_id})
        self.assertEqual(entries[2].detail, {"old_site": self.cy_site.public_id, "new_site": None})

    def test_only_a_live_sante_site_can_be_the_source(self):
        retemplate(self.cy_site)
        with self.assertRaises(ValueError):
            services.set_content_source(self.cy_site, operator=self.op)
        self.assertFalse(self.entries())

    def test_the_source_site_is_kept_from_disposal(self):
        self.assertIsNone(services.refuses_disposal(self.source))
        self.set_source()
        self.assertEqual(services.refuses_disposal(self.source), services.CONTENT_SOURCE)

    def test_the_command_sets_and_clears_it(self):
        out = io.StringIO()
        call_command("sante_content_source", operator="op", site=self.source.public_id, stdout=out)
        self.assertEqual(services.content_source_site().pk, self.source.pk)
        call_command("sante_content_source", operator="op", clear=True, stdout=out)
        self.assertIsNone(services.content_source_site())
        self.assertEqual(len(self.entries()), 2)

    def test_the_command_refuses_a_bad_operator_or_site(self):
        for kwargs in (
            {"operator": "nobody", "site": self.source.public_id},
            {"operator": "op", "site": "site_00000000000000000000"},
        ):
            with self.assertRaises(CommandError):
                call_command("sante_content_source", **kwargs)
        self.assertFalse(SanteContentSource.objects.exists())


class ShareSwitchTests(CopyCase):
    def test_the_source_owner_sets_shared_on_course_and_media(self):
        self.set_source()
        course = self.course(shared=False)
        asset = self.asset(shared=False)
        r = self.patch(f"/v1/sites/{self.source.public_id}/training/courses/{course.public_id}/",
                       {"shared": True}, client=self.ben)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIs(r.json()["shared"], True)
        self.assertIsNone(r.json()["copied_from"])
        r = self.patch(f"/v1/sites/{self.source.public_id}/media/{asset.public_id}/", {"shared": True}, client=self.ben)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIs(r.json()["shared"], True)

    def test_a_site_that_is_not_the_source_gets_a_field_error(self):
        self.set_source()
        mine = self.course(site=self.site, shared=False)
        mine_asset = self.asset(site=self.site, shared=False)
        r = self.patch(f"/v1/sites/{self.site.public_id}/training/courses/{mine.public_id}/", {"shared": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("shared", r.json()["error"]["fields"])
        r = self.patch(f"/v1/sites/{self.site.public_id}/media/{mine_asset.public_id}/", {"shared": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("shared", r.json()["error"]["fields"])
        mine.refresh_from_db()
        self.assertFalse(mine.shared)

    def test_with_no_source_nobody_can_share(self):
        course = self.course(shared=False)
        r = self.patch(f"/v1/sites/{self.source.public_id}/training/courses/{course.public_id}/",
                       {"shared": True}, client=self.ben)
        self.assertEqual(r.status_code, 400)

    def test_an_archived_course_cannot_be_shared(self):
        self.set_source()
        course = self.course(shared=False, active=False, archived_at=timezone.now())
        url = f"/v1/sites/{self.source.public_id}/training/courses/{course.public_id}/"
        self.assertEqual(self.patch(url, {"shared": True}, client=self.ben).status_code, 400)
        live = self.course("Live one", shared=False)
        url = f"/v1/sites/{self.source.public_id}/training/courses/{live.public_id}/"
        self.assertEqual(self.patch(url, {"archived": True, "shared": True}, client=self.ben).status_code, 400)

    def test_a_non_boolean_is_refused(self):
        self.set_source()
        asset = self.asset(shared=False)
        r = self.patch(f"/v1/sites/{self.source.public_id}/media/{asset.public_id}/", {"shared": "maybe"}, client=self.ben)
        self.assertEqual(r.status_code, 400)

    def test_shared_defaults_off_and_is_in_the_owner_read(self):
        r = self.get(f"/v1/sites/{self.site.public_id}/media/")
        self.assertEqual(r.status_code, 200)
        self.asset(site=self.site, shared=False)
        row = self.get(f"/v1/sites/{self.site.public_id}/media/").json()["assets"][0]
        self.assertEqual((row["shared"], row["copied_from"]), (False, None))


class OfferedListTests(CopyCase):
    def test_no_source_reads_empty(self):
        self.course()
        self.assertEqual(self.list(), {"is_source": False, "source_name": "", "courses": [], "media": []})

    def test_the_source_itself_reads_empty_with_is_source(self):
        self.set_source()
        self.course()
        self.assertEqual(
            self.list(client=self.ben, site=self.source),
            {"is_source": True, "source_name": "Live4More", "courses": [], "media": []},
        )

    def test_a_non_sante_site_reads_empty(self):
        self.set_source()
        self.course()
        retemplate(self.site)
        self.assertEqual(self.list(), {"is_source": False, "source_name": "", "courses": [], "media": []})

    def test_only_published_active_shared_items_are_offered(self):
        self.set_source()
        offered = self.course("Offered")
        self.course("Not shared", shared=False)
        self.course("Off", active=False)
        self.course("Archived", active=False, archived_at=timezone.now())
        asset = self.asset("Good", size=500)
        self.asset("Draft", published_at=None)
        self.asset("Future", published_at=timezone.now() + timedelta(days=1))
        self.asset("Unshared", shared=False)
        self.asset("Inactive", active=False)
        body = self.list()
        self.assertEqual(body["source_name"], "Live4More")
        self.assertFalse(body["is_source"])
        self.assertEqual([c["id"] for c in body["courses"]], [offered.public_id])
        self.assertEqual(body["courses"][0]["module_count"], 1)
        self.assertEqual(body["courses"][0]["lesson_count"], 2)  # the switched-off lesson is not counted
        self.assertEqual(set(body["courses"][0]), {
            "id", "title", "image_url", "module_count", "lesson_count", "bytes", "copy_state", "copy_id"})
        self.assertEqual([m["id"] for m in body["media"]], [asset.public_id])
        self.assertEqual(body["media"][0]["bytes"], 500)
        self.assertEqual(body["media"][0]["copy_state"], "not_copied")
        self.assertIsNone(body["media"][0]["copy_id"])
        self.assertEqual(set(body["media"][0]), {
            "id", "title", "asset_type", "thumb_url", "bytes", "copy_state", "copy_id"})

    def test_only_the_source_store_content_is_offered_never_another_stores(self):
        self.set_source()
        self.course("Cys", site=self.cy_site)
        self.asset("Cys", site=self.cy_site)
        body = self.list()
        self.assertEqual((body["courses"], body["media"]), ([], []))

    def test_another_owner_cannot_read_a_store_that_is_not_theirs(self):
        self.set_source()
        r = self.get(SHARED.format(self.site.public_id), client=self.cy)
        self.assertEqual(r.status_code, 404)

    def test_gated_while_members_are_off(self):
        with override_settings(MEMBERS_ENABLED=False):
            self.assertEqual(self.get(SHARED.format(self.site.public_id)).status_code, 404)


class CopyTests(CopyCase):
    def setUp(self):
        super().setUp()
        self.set_source()

    def test_copy_one_course_arrives_switched_off_with_new_keys(self):
        src = self.course(image_key="training/src-cover.png")
        body = self.copy({"kind": "course", "ids": [src.public_id]})
        self.assertEqual(body["queued"], [{"kind": "course", "id": src.public_id, "copy_state": "copying"}])
        mine = Course.objects.get(site=self.site)
        self.assertFalse(mine.active)
        self.assertFalse(mine.shared)
        self.assertEqual(mine.copied_from, src.public_id)
        self.assertEqual(mine.title, src.title)
        self.assertNotEqual(mine.public_id, src.public_id)
        self.assertTrue(mine.image_key.startswith("training/"))
        self.assertNotEqual(mine.image_key, src.image_key)
        lessons = Lesson.objects.filter(module__course=mine).order_by("order")
        self.assertEqual([l.title for l in lessons], ["L1", "L2"])  # the switched-off lesson stays behind
        src_keys = set(Lesson.objects.filter(module__course=src).values_list("image_key", flat=True))
        self.assertTrue(lessons[0].image_key and lessons[0].image_key not in src_keys)
        # the source is untouched
        src.refresh_from_db()
        self.assertEqual(src.image_key, "training/src-cover.png")
        self.assertEqual(self.list()["courses"][0]["copy_state"], "copied")
        self.assertEqual(self.list()["courses"][0]["copy_id"], mine.public_id)

    def test_links_are_no_longer_copied_every_lesson_without_a_stream_video_needs_one(self):
        src = self.course()
        self.copy({"kind": "course", "ids": [src.public_id]})
        l1, l2 = Lesson.objects.filter(module__course__site=self.site).order_by("order")
        self.assertEqual((l1.video_url, l1.needs_video), ("", True))
        self.assertEqual((l2.video_url, l2.needs_video), ("", True))
        out = self.get(f"/v1/sites/{self.site.public_id}/training/courses/{Course.objects.get(site=self.site).public_id}/")
        lessons = out.json()["modules"][0]["lessons"]
        self.assertEqual([x["needs_video"] for x in lessons], [True, True])
        self.assertEqual([x["video"]["needs_video"] for x in lessons], [True, True])

    def test_adding_a_video_by_link_is_refused_so_the_marker_stays(self):
        src = self.course()
        self.copy({"kind": "course", "ids": [src.public_id]})
        lesson = Lesson.objects.get(module__course__site=self.site, title="L2")
        r = self.patch(f"/v1/sites/{self.site.public_id}/training/lessons/{lesson.public_id}/", {"video_url": YT})
        self.assertEqual(r.status_code, 400, r.content)
        lesson.refresh_from_db()
        self.assertTrue(lesson.needs_video)

    def test_copy_media_is_unpublished_with_new_keys_and_the_same_file_size(self):
        src = self.asset(size=4242)
        self.copy({"kind": "media", "ids": [src.public_id]})
        mine = MediaAsset.objects.get(site=self.site)
        self.assertIsNone(mine.published_at)
        self.assertTrue(mine.active)
        self.assertFalse(mine.shared)
        self.assertEqual(mine.copied_from, src.public_id)
        self.assertEqual(mine.file_size, 4242)
        self.assertEqual((mine.asset_type, mine.title), (src.asset_type, src.title))
        for name in ("file_key", "thumb_key", "poster_key"):
            self.assertNotEqual(getattr(mine, name), getattr(src, name))
            self.assertTrue(getattr(mine, name).startswith(f"media-library/{self.site.public_id}/{mine.public_id}/"))
        out = self.get(f"/v1/sites/{self.site.public_id}/media/").json()["assets"][0]
        self.assertEqual(out["copied_from"], src.public_id)
        self.assertEqual(self.list()["media"][0]["copy_state"], "copied")

    def test_copy_all_copies_every_offered_item_not_yet_copied(self):
        a, b = self.course("A"), self.course("B")
        self.course("Hidden", shared=False)
        self.copy({"kind": "course", "ids": [a.public_id]})
        body = self.copy({"kind": "course", "all": True})
        self.assertEqual([q["id"] for q in body["queued"]], [b.public_id])
        self.assertEqual(Course.objects.filter(site=self.site).count(), 2)
        body = self.copy({"kind": "course", "all": True})
        self.assertEqual(body["queued"], [])
        self.assertEqual(Course.objects.filter(site=self.site).count(), 2)

    def test_copying_again_needs_again_and_makes_a_new_copy(self):
        src = self.course()
        self.copy({"kind": "course", "ids": [src.public_id]})
        body = self.copy({"kind": "course", "ids": [src.public_id]})
        self.assertEqual(body["skipped"], [{"kind": "course", "id": src.public_id, "reason": "copied"}])
        self.assertEqual(Course.objects.filter(site=self.site).count(), 1)
        self.copy({"kind": "course", "ids": [src.public_id], "again": True})
        copies = Course.objects.filter(site=self.site).order_by("pk")
        self.assertEqual(copies.count(), 2)
        self.assertNotEqual(copies[0].slug, copies[1].slug)
        self.assertNotEqual(copies[0].image_key or "x", copies[1].image_key or "y")
        self.assertEqual(self.list()["courses"][0]["copy_id"], copies[1].public_id)

    def test_deleting_the_copy_reads_not_copied_again(self):
        src = self.asset()
        self.copy({"kind": "media", "ids": [src.public_id]})
        mine = MediaAsset.objects.get(site=self.site)
        with self.blob:
            self.assertEqual(self.delete(f"/v1/sites/{self.site.public_id}/media/{mine.public_id}/").status_code, 200)
        self.assertEqual(self.list()["media"][0]["copy_state"], "not_copied")

    def test_a_source_change_later_does_not_touch_the_copy(self):
        src = self.course()
        self.copy({"kind": "course", "ids": [src.public_id]})
        src.title = "Changed"
        src.save()
        self.assertEqual(Course.objects.get(site=self.site).title, "Ways to Earn")

    def test_unknown_unshared_or_foreign_ids_are_refused_and_nothing_is_queued(self):
        mine = self.course(site=self.cy_site, title="Cys")
        hidden = self.course("Hidden", shared=False)
        for ids in (["crs_00000000000000000000"], [mine.public_id], [hidden.public_id]):
            with self.subTest(ids=ids):
                body = self.copy({"kind": "course", "ids": ids}, status=400)
                self.assertIn("ids", body["error"]["fields"])
        self.assertFalse(SanteContentCopy.objects.exists())

    def test_the_source_store_cannot_copy_from_itself(self):
        src = self.course()
        self.copy({"kind": "course", "ids": [src.public_id]}, client=self.ben, site=self.source, status=400)
        self.assertEqual(self.copy({"kind": "course", "all": True}, client=self.ben, site=self.source)["queued"], [])
        self.assertFalse(SanteContentCopy.objects.exists())

    def test_no_source_copies_nothing(self):
        src = self.course()
        services.set_content_source(None, operator=self.op)
        self.copy({"kind": "course", "ids": [src.public_id]}, status=400)
        self.assertEqual(self.copy({"kind": "media", "all": True})["queued"], [])

    def test_bad_requests(self):
        for body in ({"kind": "lesson", "ids": ["x"]}, {"kind": "course"}, {"kind": "course", "ids": []},
                     {"kind": "course", "ids": ["a"], "extra": 1}):
            with self.subTest(body=body):
                self.copy(body, status=400)

    def test_another_site_never_sees_my_copies(self):
        src = self.course()
        self.copy({"kind": "course", "ids": [src.public_id]})
        self.assertEqual(self.list(client=self.cy, site=self.cy_site)["courses"][0]["copy_state"], "not_copied")

    def test_the_request_answers_before_any_file_is_copied(self):
        src = self.course(image_key="training/src-cover.png")
        with self.blob, mock.patch("apps.sante.tasks.copy_content.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                r = self.post(SHARED.format(self.site.public_id) + "copy/", {"kind": "course", "ids": [src.public_id]})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.blob.calls, 0)
        delay.assert_called_once()
        self.assertEqual(self.list()["courses"][0]["copy_state"], "copying")
        self.assertFalse(Course.objects.filter(site=self.site).exists())
        # a second request while it is copying is skipped
        body = self.copy({"kind": "course", "ids": [src.public_id]})
        self.assertEqual(body["skipped"][0]["reason"], "copying")

    def test_a_copy_stuck_copying_reads_failed(self):
        src = self.course()
        SanteContentCopy.objects.create(
            site=self.site, kind="course", source_public_id=src.public_id,
            requested_at=timezone.now() - content_copy.STALE_AFTER - timedelta(minutes=1),
        )
        self.assertEqual(self.list()["courses"][0]["copy_state"], "failed")


class FailureTests(CopyCase):
    def setUp(self):
        super().setUp()
        self.set_source()

    def test_a_failed_media_copy_leaves_no_row_and_removes_the_files_it_stored(self):
        src = self.asset()
        self.blob = FakeBlob(self.blob.files, fail_on=3)  # file and thumb stored, the poster fails
        before = set(self.blob.files)
        self.copy({"kind": "media", "ids": [src.public_id]})
        self.assertFalse(MediaAsset.objects.filter(site=self.site).exists())
        self.assertEqual(set(self.blob.files), before)  # nothing left behind
        self.assertEqual(len(self.blob.deleted), 2)
        row = SanteContentCopy.objects.get()
        self.assertEqual(row.state, "failed")
        self.assertTrue(row.error)
        self.assertEqual(self.list()["media"][0]["copy_state"], "failed")

    def test_a_failed_course_copy_leaves_no_rows_and_no_files(self):
        src = self.course(image_key="training/src-cover.png")
        self.blob = FakeBlob(self.blob.files, fail_on=2)  # the cover is stored, a lesson picture fails
        before = set(self.blob.files)
        self.copy({"kind": "course", "ids": [src.public_id]})
        self.assertFalse(Course.objects.filter(site=self.site).exists())
        self.assertFalse(Module.objects.exclude(course=src).exists())
        self.assertEqual(set(self.blob.files), before)
        self.assertEqual(SanteContentCopy.objects.get().state, "failed")

    def test_a_database_failure_after_the_files_removes_them(self):
        src = self.asset()
        before = set(self.blob.files)
        with mock.patch("apps.sante.content_copy._finish_rows", side_effect=RuntimeError("db")):
            self.copy({"kind": "media", "ids": [src.public_id]})
        self.assertFalse(MediaAsset.objects.filter(site=self.site).exists())
        self.assertEqual(set(self.blob.files), before)
        self.assertEqual(SanteContentCopy.objects.get().state, "failed")

    def test_an_item_unshared_after_the_request_fails_cleanly(self):
        src = self.asset()
        with self.blob:
            with self.captureOnCommitCallbacks(execute=False):
                self.post(SHARED.format(self.site.public_id) + "copy/", {"kind": "media", "ids": [src.public_id]})
            MediaAsset.objects.filter(pk=src.pk).update(shared=False)
            content_copy.run_copy(SanteContentCopy.objects.get().pk)
        self.assertEqual(SanteContentCopy.objects.get().state, "failed")
        self.assertFalse(MediaAsset.objects.filter(site=self.site).exists())

    def test_an_unconfigured_store_fails_the_copy(self):
        src = self.asset()
        with mock.patch("apps.catalog.media.configured", return_value=False):
            with self.captureOnCommitCallbacks(execute=True):
                self.post(SHARED.format(self.site.public_id) + "copy/", {"kind": "media", "ids": [src.public_id]})
        self.assertEqual(SanteContentCopy.objects.get().state, "failed")

    def test_a_queue_failure_reads_failed(self):
        src = self.asset()
        with self.blob, mock.patch("apps.sante.tasks.copy_content.delay", side_effect=OSError("down")):
            with self.captureOnCommitCallbacks(execute=True):
                self.post(SHARED.format(self.site.public_id) + "copy/", {"kind": "media", "ids": [src.public_id]})
        self.assertEqual(SanteContentCopy.objects.get().state, "failed")


class DeleteGuardTests(CopyCase):
    def test_a_key_still_used_by_another_row_is_kept(self):
        self.asset("Shared key")  # registers its files in self.blob
        a = self.asset("A", site=self.site)
        b = MediaAsset.objects.create(site=self.cy_site, title="B", asset_type="image", file_key=a.file_key)
        with self.blob:
            file_refs.discard_if_unreferenced(a.file_key)
        self.assertEqual(self.blob.deleted, [])
        b.delete()
        a.delete()
        with self.blob:
            file_refs.discard_if_unreferenced(a.file_key)
        self.assertEqual(self.blob.deleted, [a.file_key])

    def test_every_column_that_holds_a_key_is_checked(self):
        cases = [
            lambda k: Course.objects.create(site=self.site, title="c", slug="c", image_key=k),
            lambda k: MediaAsset.objects.create(site=self.site, title="t", asset_type="image", thumb_key=k),
            lambda k: MediaAsset.objects.create(site=self.site, title="p", asset_type="video", poster_key=k),
        ]
        for n, make in enumerate(cases):
            key = f"training/guard-{n}.png"
            row = make(key)
            with self.subTest(n=n):
                self.assertTrue(file_refs.is_referenced(key))
                row.delete()
                self.assertFalse(file_refs.is_referenced(key))

    def test_deleting_a_copy_never_removes_the_source_file_and_the_other_way_round(self):
        self.set_source()
        src = self.asset(size=10)
        self.copy({"kind": "media", "ids": [src.public_id]})
        mine = MediaAsset.objects.get(site=self.site)
        source_keys = {src.file_key, src.thumb_key, src.poster_key}
        with self.blob:
            self.assertEqual(self.delete(f"/v1/sites/{self.site.public_id}/media/{mine.public_id}/").status_code, 200)
        self.assertFalse(source_keys & set(self.blob.deleted))
        # the other way round: the source deletes its original, a fresh copy keeps its own files
        self.copy({"kind": "media", "ids": [src.public_id], "again": True})
        mine = MediaAsset.objects.get(site=self.site)
        mine_keys = {mine.file_key, mine.thumb_key, mine.poster_key}
        with self.blob:
            self.assertEqual(self.delete(f"/v1/sites/{self.source.public_id}/media/{src.public_id}/", client=self.ben).status_code, 200)
        self.assertFalse(mine_keys & set(self.blob.deleted))

    def test_replacing_a_picture_that_another_row_uses_keeps_the_file(self):
        shared_key = "training/shared-picture.png"
        a = Course.objects.create(site=self.site, title="A", slug="a", image_key=shared_key)
        Course.objects.create(site=self.cy_site, title="B", slug="b", image_key=shared_key)
        with self.blob:
            r = self.delete(f"/v1/sites/{self.site.public_id}/training/courses/{a.public_id}/image/")
            self.assertEqual(r.status_code, 200)
            with self.captureOnCommitCallbacks(execute=True):
                pass
        self.assertNotIn(shared_key, self.blob.deleted)

    def test_deleting_a_course_discards_only_unreferenced_images(self):
        key_own, key_shared = "training/own.png", "training/dup.png"
        course = Course.objects.create(site=self.site, title="A", slug="a", image_key=key_own)
        module = Module.objects.create(course=course, title="m")
        Lesson.objects.create(module=module, title="l", image_key=key_shared, video_url="")
        Course.objects.create(site=self.cy_site, title="B", slug="b", image_key=key_shared)
        with self.blob, self.captureOnCommitCallbacks(execute=True):
            r = self.delete(f"/v1/sites/{self.site.public_id}/training/courses/{course.public_id}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(key_own, self.blob.deleted)
        self.assertNotIn(key_shared, self.blob.deleted)


class MediaCopyHelperTests(TestCase):
    @override_settings(**MEDIA)
    def test_media_copy_reads_the_public_file_and_puts_it_under_the_new_key(self):
        puts = []

        def fake_send(action, url, *, method, data=None, headers=None):
            puts.append((url, data, headers))
            return b'{"pathname": "training/new.png"}'

        with mock.patch.object(media, "_read_public", return_value=(b"bytes", "image/png")), \
                mock.patch.object(media, "_send", side_effect=fake_send):
            self.assertEqual(media.copy("training/old.png", "training/new.png"), "training/new.png")
        self.assertEqual(puts[0][1], b"bytes")
        self.assertEqual(puts[0][2]["x-content-type"], "image/png")
        self.assertIn("pathname=training%2Fnew.png", puts[0][0])

    @override_settings(**MEDIA)
    def test_media_copy_refuses_the_same_key_and_an_unconfirmed_put(self):
        with self.assertRaises(media.StoreError):
            media.copy("training/a.png", "training/a.png")
        with mock.patch.object(media, "_read_public", return_value=(b"b", "image/png")), \
                mock.patch.object(media, "_send", return_value=b'{"pathname": "other"}'):
            with self.assertRaises(media.StoreError):
                media.copy("training/a.png", "training/b.png")
