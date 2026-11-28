"""The one-time copy of Media Library videos to the store's Bunny library: dry run by default, counts
only, the Blob copy kept until the second step. Every Bunny and store call is a stand-in."""
import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.media_library import store
from apps.media_library.models import MediaAsset, MediaObject
from apps.media_library.tests.base import MediaOwnerCase, make_asset
from apps.media_library.tests.test_video_upload import SETTINGS
from apps.training import stream
from apps.training.tests.stream_fake import FakeBunny, set_library

LIBRARY = 816560


@override_settings(MEDIA_VIDEO_BUNNY_ENABLED=True)
class CopyCase(MediaOwnerCase):
    def setUp(self):
        super().setUp()
        enter = override_settings(**SETTINGS)
        enter.enable()
        self.addCleanup(enter.disable)
        self.bunny = FakeBunny().__enter__()
        self.addCleanup(self.bunny.__exit__, None, None, None)
        set_library(self.site, LIBRARY, "ANA")
        self.old = f"media-library/{self.site_id}/clip.mp4"
        self.video = make_asset(self.site, "Clip", asset_type="video", file_key=self.old, file_size=900)
        make_asset(self.site, "Pic")  # an image: never copied
        self.fetched = []
        download = mock.patch.object(stream, "download_url", side_effect=self._download)
        download.start()
        self.addCleanup(download.stop)
        sleep = mock.patch("apps.media_library.management.commands.media_video_to_bunny.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def _download(self, url):
        self.fetched.append(url)
        return io.BytesIO(b"mp4-bytes"), 9

    def run_cmd(self, *args):
        out = io.StringIO()
        call_command("media_video_to_bunny", "--site", self.site_id, *args, stdout=out)
        return out.getvalue()


class DryRunTests(CopyCase):
    def test_the_default_prints_counts_only_and_calls_nothing(self):
        text = self.run_cmd()
        self.assertEqual(
            text.strip(),
            "DRY RUN: videos 1, on the public store 1, already in the library 0, shared (left alone) 0, to copy 1",
        )
        self.assertNotIn("clip", text)  # no key, title or URL
        self.assertEqual((self.fetched, self.bunny.calls), ([], []))
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, self.old)

    @override_settings(MEDIA_VIDEO_BUNNY_ENABLED=False)
    def test_apply_needs_the_switch(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--apply")
        self.assertEqual(self.bunny.calls, [])

    def test_apply_needs_a_library_with_its_key(self):
        with mock.patch.dict("os.environ", {"BUNNY_STREAM_KEY_ANA": ""}), self.assertRaises(CommandError):
            self.run_cmd("--apply")
        self.assertEqual(self.fetched, [])


class ApplyTests(CopyCase):
    def test_a_finished_video_is_switched_over_the_ledger_records_it_and_the_blob_copy_is_kept(self):
        from apps.media_library import ledger

        ledger.record(self.site, asset=self.video, role="file", key=self.old, content_type="video/mp4", size=900)
        text = self.run_cmd("--apply")
        self.assertIn("copied 1, not ready 0, failed 0", text)
        asset = MediaAsset.objects.get(pk=self.video.pk)
        ref = store.bunny_ref(asset.file_key)
        self.assertEqual(ref[0], LIBRARY)
        self.assertEqual(self.bunny.videos[(LIBRARY, ref[1])]["bytes"], b"mp4-bytes")
        self.assertEqual(asset.file_size, 9)  # what the stand-in Bunny reports as stored
        self.assertTrue(self.fetched[0].endswith("clip.mp4"))
        self.assertIsNone(MediaObject.objects.get(key=self.old).removed_at)  # kept
        self.assertIsNone(MediaObject.objects.get(key=asset.file_key).removed_at)
        self.assertEqual(self.bunny.deleted, [])
        self.assertEqual(self.store.deleted, [])
        self.assertIn("already in the library 1", self.run_cmd())  # a second run has nothing to do
        self.assertEqual(len(self.fetched), 1)

    def test_a_video_bunny_does_not_finish_is_removed_again_and_the_asset_is_untouched(self):
        original = self.bunny.get_video

        def never_ready(library, guid):
            self.bunny.videos[(library.id, guid)]["status_override"] = stream.TRANSCODING
            return original(library, guid)

        with mock.patch.object(stream, "get_video", side_effect=never_ready):
            text = self.run_cmd("--apply", "--wait", "0")
        self.assertIn("copied 0, not ready 1, failed 0", text)
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, self.old)
        self.assertEqual(len(self.bunny.deleted), 1)  # the half-made Bunny video is gone
        self.assertEqual(MediaObject.objects.count(), 0)

    def test_a_failed_download_or_upload_is_counted_and_changes_nothing(self):
        with mock.patch.object(stream, "download_url", side_effect=stream.StreamError("x")):
            self.assertIn("failed 1", self.run_cmd("--apply"))
        self.bunny.fail.add("upload")
        self.assertIn("failed 1", self.run_cmd("--apply"))
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, self.old)

    def test_a_shared_asset_is_left_alone(self):
        MediaAsset.objects.filter(pk=self.video.pk).update(shared=True)
        text = self.run_cmd("--apply")
        self.assertIn("shared (left alone) 1", text)
        self.assertIn("copied 0", text)
        self.assertEqual(self.fetched, [])

    def test_another_stores_videos_are_never_touched(self):
        theirs = make_asset(self.ben_site, "Theirs", asset_type="video", file_key=f"media-library/{self.ben_site_id}/b.mp4")
        self.run_cmd("--apply")
        self.assertEqual(MediaAsset.objects.get(pk=theirs.pk).file_key, f"media-library/{self.ben_site_id}/b.mp4")


class ReleaseOldTests(CopyCase):
    def test_step_two_removes_only_the_kept_copies_of_videos_that_now_play_from_bunny(self):
        from apps.media_library import ledger

        ledger.record(self.site, asset=self.video, role="file", key=self.old, content_type="video/mp4", size=900)
        self.store.files[self.old] = b"x"
        pending = make_asset(self.site, "Later", asset_type="video", file_key=f"media-library/{self.site_id}/later.mp4")
        ledger.record(self.site, asset=pending, role="file", key=pending.file_key, content_type="video/mp4", size=5)
        self.run_cmd("--apply")  # copies both
        MediaAsset.objects.filter(pk=pending.pk).update(file_key=pending.file_key)  # one is put back on the store
        self.assertEqual(self.run_cmd("--release-old").strip(), "DRY RUN: old public-store copies kept after a copy 1")
        self.assertIn(self.old, self.store.files)
        self.assertEqual(self.run_cmd("--release-old", "--apply").strip().splitlines()[-1], "released 1, still kept 0")
        self.assertNotIn(self.old, self.store.files)
        self.assertIsNotNone(MediaObject.objects.get(key=self.old).removed_at)
        self.assertEqual(self.bunny.deleted, [])  # the Bunny videos are never touched by step two
