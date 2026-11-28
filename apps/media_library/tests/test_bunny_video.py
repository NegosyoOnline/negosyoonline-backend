"""Media Library video in the store's own Bunny Stream library (V-1b, MEDIA_VIDEO_BUNNY_ENABLED).
The Stream calls are the training tests' FakeBunny: no test reaches the network and every key is a
test value."""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.media_library import audit, ledger, services, store
from apps.media_library.models import MediaAsset, MediaObject
from apps.media_library.tests.base import MediaOwnerCase, make_asset
from apps.media_library.tests.test_video_upload import SETTINGS
from apps.training import stream
from apps.training.tests.base import make_course_with_lessons
from apps.training.tests.stream_fake import FakeBunny, set_library

LIBRARY = 816560


@override_settings(MEDIA_VIDEO_BUNNY_ENABLED=True)
class BunnyVideoCase(MediaOwnerCase):
    def setUp(self):
        super().setUp()
        enter = override_settings(**SETTINGS)  # a token needs a store id (FakeStore's settings lack one)
        enter.enable()
        self.addCleanup(enter.disable)
        self.bunny = FakeBunny().__enter__()
        self.addCleanup(self.bunny.__exit__, None, None, None)
        set_library(self.site, LIBRARY, "ANA")
        set_library(self.ben_site, 222222, "BEN")
        self.video = make_asset(
            self.site, "Vid", asset_type="video", file_key="", published_at=None,
            poster_key=f"media-library/{self.site_id}/poster.png",
        )

    def token(self, asset=None):
        return self.send("post", self.url(f"{(asset or self.video).public_id}/video/token/"), None)

    def attach(self, body, asset=None):
        return self.send("post", self.url(f"{(asset or self.video).public_id}/video/attach/"), body)

    def started(self, asset=None):
        """Token then a finished upload: the video id the browser would send."""
        response = self.token(asset)
        self.assertEqual(response.status_code, 200, response.content)
        guid = response.json()["upload"]["video_id"]
        self.bunny.videos[(LIBRARY, guid)]["bytes"] = b"video"
        self.bunny.videos[(LIBRARY, guid)]["storage"] = 4096
        return guid


class TokenTests(BunnyVideoCase):
    def test_it_creates_the_video_in_the_stores_own_library_and_signs_a_tus_upload(self):
        response = self.token()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"host", "upload"})
        self.assertEqual(body["host"], "bunny")
        upload = body["upload"]
        self.assertEqual(set(upload), {"endpoint", "library_id", "video_id", "signature", "expires"})
        self.assertEqual(upload["library_id"], LIBRARY)
        self.assertIn((LIBRARY, upload["video_id"]), self.bunny.videos)
        self.assertEqual([c for c in self.bunny.calls if c[0] == "create"], [("create", LIBRARY)])
        self.assertNotIn("ana-local-test-key", response.content.decode())  # the key is only hashed

    @override_settings(MEDIA_VIDEO_BUNNY_ENABLED=False)
    def test_switched_off_the_video_goes_to_the_public_store_exactly_as_before(self):
        body = self.token().json()
        self.assertEqual(set(body), {
            "pathname", "client_token", "upload_url", "method", "headers", "max_bytes", "content_type",
            "expires_at", "public_url",
        })
        self.assertEqual(self.bunny.calls, [])

    def test_a_store_with_no_library_or_no_key_keeps_the_public_store_path(self):
        from apps.training.models import StreamLibrary

        StreamLibrary.objects.filter(site=self.site).delete()
        self.assertIn("client_token", self.token().json())
        set_library(self.site, LIBRARY, "ANA")
        with mock.patch.dict("os.environ", {"BUNNY_STREAM_KEY_ANA": ""}):
            self.assertIn("client_token", self.token().json())
        self.assertEqual(self.bunny.calls, [])

    def test_a_bunny_failure_is_a_502_with_no_key(self):
        self.bunny.fail.add("create")
        response = self.token()
        self.assertEqual(response.status_code, 502, response.content)
        self.assertEqual(response.json()["error"]["code"], "video_failed")


class AttachTests(BunnyVideoCase):
    def test_a_finished_video_is_recorded_on_the_asset_and_in_the_ledger(self):
        guid = self.started()
        response = self.attach({"video_id": guid})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsNone(body["file_url"])
        self.assertEqual(body["embed_url"], f"https://iframe.mediadelivery.net/embed/{LIBRARY}/{guid}")
        self.assertEqual((body["file_size"], body["duration_seconds"]), (4096, 12))
        key = f"bunny:{LIBRARY}:{guid}"
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, key)
        row = MediaObject.objects.get(key=key)
        self.assertEqual((row.site, row.asset, row.role, row.bytes, row.removed_at), (self.site, self.video, "file", 4096, None))
        self.assertEqual(ledger.bytes_at(self.site, timezone.now() + timedelta(seconds=1)), 4096)

    def test_a_video_still_encoding_is_409_and_nothing_is_recorded(self):
        guid = self.started()
        self.bunny.videos[(LIBRARY, guid)]["status_override"] = stream.TRANSCODING
        response = self.attach({"video_id": guid})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "video_processing")
        self.assertEqual(MediaObject.objects.filter(removed_at__isnull=True, bytes__gt=0).count(), 0)  # only the 0-byte pending row
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")

    def test_a_failed_video_is_refused_and_deleted(self):
        guid = self.started()
        self.bunny.videos[(LIBRARY, guid)]["status_override"] = stream.ERROR
        response = self.attach({"video_id": guid})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn((LIBRARY, guid), self.bunny.deleted)
        self.assertEqual(MediaObject.objects.filter(removed_at__isnull=True).count(), 0)  # closed with the Bunny delete

    def test_bunny_not_answering_is_502(self):
        guid = self.started()
        self.bunny.fail.add("check")
        self.assertEqual(self.attach({"video_id": guid}).status_code, 502)

    def test_it_needs_the_switch_a_library_and_a_proper_id(self):
        guid = self.started()
        with override_settings(MEDIA_VIDEO_BUNNY_ENABLED=False):
            self.assertEqual(self.attach({"video_id": guid}).status_code, 400)
        self.assertEqual(self.attach({"video_id": "no way!"}).status_code, 400)
        self.assertEqual(self.attach({}).status_code, 400)
        self.assertEqual(self.attach({"video_id": guid, "url": "https://x.test/a.mp4"}).status_code, 400)
        self.assertEqual(MediaObject.objects.filter(bytes__gt=0).count(), 0)

    def test_a_video_of_another_stores_library_is_not_found(self):
        self.bunny.put_ready(222222, "bens-guid-0001")
        self.bunny.fail.add("check")  # asked in Ana's library the real API answers 404: a failed check
        response = self.attach({"video_id": "bens-guid-0001"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MediaObject.objects.filter(bytes__gt=0).count(), 0)

    def test_a_video_a_lesson_or_another_asset_already_names_is_refused(self):
        course, lessons = make_course_with_lessons(self.site, (1,))
        lesson = lessons[0]
        lesson.stream_library_id, lesson.stream_video_id, lesson.stream_status = LIBRARY, "lesson-guid-0001", "ready"
        lesson.save()
        self.bunny.put_ready(LIBRARY, "lesson-guid-0001")
        self.assertEqual(self.attach({"video_id": "lesson-guid-0001"}).status_code, 400)
        guid = self.started()
        self.assertEqual(self.attach({"video_id": guid}).status_code, 200)
        other = make_asset(self.site, "Two", asset_type="video", file_key="", published_at=None)
        self.assertEqual(self.attach({"video_id": guid}, other).status_code, 400)

    def test_a_replacement_releases_the_old_public_store_file(self):
        old = f"media-library/{self.site_id}/{self.video.public_id}/old.mp4"
        MediaAsset.objects.filter(pk=self.video.pk).update(file_key=old, file_size=10)
        ledger.record(self.site, asset=self.video, role="file", key=old, content_type="video/mp4", size=10)
        self.store.files[old] = b"x"
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.attach({"video_id": self.started()}).status_code, 200)
        self.assertIsNotNone(MediaObject.objects.get(key=old).removed_at)
        self.assertNotIn(old, self.store.files)

    def test_the_public_store_attach_still_works_with_the_switch_on(self):
        url = f"https://store123.public.blob.vercel-storage.com/media-library/{self.site_id}/{self.video.public_id}/abc.mp4"
        with mock.patch("apps.media_library.client_upload.confirm_object", return_value=("video/mp4", 1000)):
            response = self.attach({"url": url})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["file_url"], url)
        self.assertNotIn("embed_url", body)


class VideoHostTests(BunnyVideoCase):
    def listing(self):
        response = self.get(self.url())
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_the_list_says_bunny_only_when_the_token_route_would(self):
        self.assertEqual(self.listing()["video_host"], "bunny")
        self.assertEqual(self.token().json()["host"], "bunny")  # the same predicate
        with override_settings(MEDIA_VIDEO_BUNNY_ENABLED=False):
            self.assertEqual(set(self.listing()), {"assets"})
        from apps.training.models import StreamLibrary

        StreamLibrary.objects.filter(site=self.site).delete()
        self.assertEqual(set(self.listing()), {"assets"})

    def test_a_key_less_library_gives_no_key(self):
        with mock.patch.dict("os.environ", {"BUNNY_STREAM_KEY_ANA": ""}):
            self.assertEqual(set(self.listing()), {"assets"})


class OrphanTests(BunnyVideoCase):
    def key(self, guid):
        return f"bunny:{LIBRARY}:{guid}"

    def test_the_token_puts_the_video_in_the_ledger_at_once_with_no_bytes(self):
        guid = self.token().json()["upload"]["video_id"]
        row = MediaObject.objects.get(key=self.key(guid))
        self.assertEqual((row.site, row.asset, row.role, row.bytes, row.removed_at), (self.site, self.video, "file", 0, None))
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")  # not attached yet

    def test_a_token_with_no_attach_is_removed_by_site_erasure(self):
        guid = self.token().json()["upload"]["video_id"]
        self.assertEqual(services.purge_site(self.site, timezone.now()), 1)
        self.assertIn((LIBRARY, guid), self.bunny.deleted)
        self.assertIsNotNone(MediaObject.objects.get(key=self.key(guid)).removed_at)

    def test_a_new_token_deletes_the_earlier_unattached_video_but_not_the_attached_one(self):
        first = self.token().json()["upload"]["video_id"]
        with self.captureOnCommitCallbacks(execute=True):
            second = self.token().json()["upload"]["video_id"]
        self.assertIn((LIBRARY, first), self.bunny.deleted)
        self.assertIsNotNone(MediaObject.objects.get(key=self.key(first)).removed_at)
        self.assertIsNone(MediaObject.objects.get(key=self.key(second)).removed_at)
        self.assertEqual(self.attach({"video_id": first}).status_code, 400)  # no longer this asset's
        self.bunny.videos[(LIBRARY, second)]["bytes"] = b"x"
        self.assertEqual(self.attach({"video_id": second}).status_code, 200)
        with self.captureOnCommitCallbacks(execute=True):
            third = self.token().json()["upload"]["video_id"]  # a replacement in progress
        self.assertNotIn((LIBRARY, second), self.bunny.deleted)  # the attached one keeps playing
        self.assertIsNone(MediaObject.objects.get(key=self.key(third)).removed_at)

    def test_attach_only_takes_a_video_this_assets_token_made(self):
        guid = self.started()
        other = make_asset(self.site, "Two", asset_type="video", file_key="", published_at=None)
        self.assertEqual(self.attach({"video_id": guid}, other).status_code, 400)
        self.bunny.put_ready(LIBRARY, "stranger-guid-01")  # in the library, but no token made it
        self.assertEqual(self.attach({"video_id": "stranger-guid-01"}).status_code, 400)

    def test_attach_gives_the_row_its_real_bytes(self):
        guid = self.started()
        self.assertEqual(self.attach({"video_id": guid}).status_code, 200)
        self.assertEqual(MediaObject.objects.get(key=self.key(guid)).bytes, 4096)

    def test_deleting_the_asset_also_deletes_an_unattached_video(self):
        guid = self.token().json()["upload"]["video_id"]
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.send("delete", self.url(f"{self.video.public_id}/")).status_code, 200)
        self.assertIn((LIBRARY, guid), self.bunny.deleted)

    def test_the_audit_retries_a_failed_delete_of_an_unattached_video_after_a_day(self):
        guid = self.token().json()["upload"]["video_id"]
        key = self.key(guid)
        MediaObject.objects.filter(key=key).update(added_at=timezone.now() - timedelta(hours=2))
        with override_settings(MEDIA_LEDGER_AUDIT_ENABLED=True), mock.patch("apps.media_library.audit.media.list_prefix", return_value=[]):
            self.assertEqual(audit.run()["retried"], 0)  # still uploading, perhaps
            MediaObject.objects.filter(key=key).update(added_at=timezone.now() - timedelta(days=2))
            self.bunny.fail.add("delete")
            counts = audit.run()
            self.assertEqual((counts["retried"], counts["released"], counts["still_pending"]), (1, 0, 1))
            self.bunny.fail.discard("delete")
            counts = audit.run()
        self.assertEqual((counts["retried"], counts["released"]), (1, 1))
        self.assertIn((LIBRARY, guid), self.bunny.deleted)
        self.assertIsNotNone(MediaObject.objects.get(key=key).removed_at)

    def test_a_video_over_the_size_setting_is_refused_deleted_and_not_attached(self):
        guid = self.started()
        with override_settings(MEDIA_VIDEO_MAX_BYTES=4095):
            response = self.attach({"video_id": guid})
        self.assertEqual(response.status_code, 400, response.content)
        error = response.json()["error"]
        self.assertEqual(error["code"], "video_too_large")
        self.assertEqual(error["message"], "This video is too large to upload.")
        self.assertIn((LIBRARY, guid), self.bunny.deleted)
        self.assertIsNotNone(MediaObject.objects.get(key=self.key(guid)).removed_at)
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")

    def test_a_video_at_the_size_setting_is_accepted_and_the_default_is_the_placeholder(self):
        from django.conf import settings

        self.assertEqual(settings.MEDIA_VIDEO_MAX_BYTES, 2_000_000_000)
        guid = self.started()
        with override_settings(MEDIA_VIDEO_MAX_BYTES=4096):
            self.assertEqual(self.attach({"video_id": guid}).status_code, 200)


class PlaybackAndLifecycleTests(BunnyVideoCase):
    def attached(self):
        guid = self.started()
        self.assertEqual(self.attach({"video_id": guid}).status_code, 200)
        return guid

    def test_a_published_bunny_video_reaches_the_member_with_the_player_url_and_no_file(self):
        guid = self.attached()
        self.assertEqual(self.send("post", self.url(f"{self.video.public_id}/publish/"), {}).status_code, 200)
        item = services.asset_out(MediaAsset.objects.get(pk=self.video.pk))
        self.assertIsNone(item["file_url"])
        self.assertEqual(item["embed_url"], stream.embed_url(LIBRARY, guid))
        self.assertTrue(item["poster_url"])

    def test_it_still_needs_a_poster_to_publish(self):
        self.attached()
        MediaAsset.objects.filter(pk=self.video.pk).update(poster_key="")
        self.assertEqual(self.send("post", self.url(f"{self.video.public_id}/publish/"), {}).status_code, 400)

    def test_a_blob_asset_has_no_embed_url_key(self):
        self.assertNotIn("embed_url", services.asset_out(make_asset(self.site, "Img")))

    def test_deleting_the_asset_deletes_the_video_and_closes_the_ledger_row(self):
        guid = self.attached()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.send("delete", self.url(f"{self.video.public_id}/")).status_code, 200)
        self.assertIn((LIBRARY, guid), self.bunny.deleted)
        self.assertIsNotNone(MediaObject.objects.get(key=f"bunny:{LIBRARY}:{guid}").removed_at)

    def test_site_erasure_removes_the_bunny_videos_of_that_site_only(self):
        guid = self.attached()
        ben = make_asset(self.ben_site, "BV", asset_type="video", file_key="bunny:222222:bens-guid-0001")
        ledger.record(self.ben_site, asset=ben, role="file", key=ben.file_key, content_type="video/mp4", size=5)
        self.bunny.put_ready(222222, "bens-guid-0001")
        self.assertEqual(services.purge_site(self.site, timezone.now()), 1)
        self.assertIn((LIBRARY, guid), self.bunny.deleted)
        self.assertNotIn((222222, "bens-guid-0001"), self.bunny.deleted)
        self.assertIsNone(MediaObject.objects.get(key=ben.file_key).removed_at)
        self.assertEqual(services.purge_site(self.site, timezone.now()), 0)

    def test_an_already_absent_video_counts_as_gone_and_a_failure_closes_nothing(self):
        guid = self.attached()
        gone = stream.StreamError("Video delete failed (404).")
        gone.status = 404
        with mock.patch.object(stream, "delete_video", side_effect=gone):
            self.assertEqual(services.purge_site(self.site, timezone.now()), 1)
        self.assertIsNotNone(MediaObject.objects.get(key=f"bunny:{LIBRARY}:{guid}").removed_at)

    def test_a_bunny_failure_during_erasure_raises_and_keeps_the_row_live(self):
        guid = self.attached()
        self.bunny.fail.add("delete")
        with self.assertRaises(store.StoreError):
            services.purge_site(self.site, timezone.now())
        self.assertIsNone(MediaObject.objects.get(key=f"bunny:{LIBRARY}:{guid}").removed_at)

    def test_a_missing_library_row_blocks_erasure_with_a_store_error_not_a_crash(self):
        from apps.training.models import StreamLibrary

        self.attached()
        StreamLibrary.objects.filter(site=self.site).delete()
        with self.assertRaises(store.StoreError):
            services.purge_site(self.site, timezone.now())

    def test_a_bunny_video_cannot_be_shared_with_other_stores(self):
        self.attached()
        with mock.patch("apps.sante.services.is_content_source", return_value=True):
            response = self.send("patch", self.url(f"{self.video.public_id}/"), {"shared": True})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("shared", response.json()["error"]["fields"])
        self.assertFalse(MediaAsset.objects.get(pk=self.video.pk).shared)

    def test_the_daily_audit_retries_a_bunny_delete_that_did_not_finish(self):
        guid = self.attached()
        key = f"bunny:{LIBRARY}:{guid}"
        MediaAsset.objects.filter(pk=self.video.pk).delete()  # the row is gone, the video is not
        MediaObject.objects.filter(key=key).update(added_at=timezone.now() - timedelta(days=2))
        with override_settings(MEDIA_LEDGER_AUDIT_ENABLED=True), mock.patch("apps.media_library.audit.media.list_prefix", return_value=[]):
            counts = audit.run()
        self.assertEqual((counts["retried"], counts["released"], counts["listed"]), (1, 1, 0))
        self.assertIn((LIBRARY, guid), self.bunny.deleted)
        self.assertIsNotNone(MediaObject.objects.get(key=key).removed_at)
