"""The media_ledger_backfill command (G2, ClickUp z8uq9mb384). No test reaches the network."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from apps.media_library import ledger, store
from apps.media_library.models import MediaObject
from apps.media_library.tests.base import MediaOwnerCase, make_asset


def key_of(site, asset, name):
    return f"media-library/{site.public_id}/{asset.public_id}/{name}.png"


class BackfillTests(MediaOwnerCase):
    def setUp(self):
        super().setUp()
        long_ago = timezone.now() - timedelta(days=40)
        self.image = make_asset(self.site, "Img", file_size=1234)
        self.image.file_key = key_of(self.site, self.image, "f")
        self.image.thumb_key = key_of(self.site, self.image, "t")
        self.image.created_at = long_ago
        self.image.save()
        self.video = make_asset(self.site, "Vid", asset_type="video", file_size=9999, file_key="", poster_key="")
        self.video.file_key = f"media-library/{self.site_id}/{self.video.public_id}/v.mp4"
        self.video.poster_key = key_of(self.site, self.video, "p")
        self.video.created_at = long_ago
        self.video.save()
        self.bens = make_asset(self.ben_site, "Bens", file_size=77)
        self.bens.created_at = long_ago
        self.bens.save()

    def run_command(self, *args):
        out = StringIO()
        call_command("media_ledger_backfill", *args, stdout=out)
        return out.getvalue()

    def head(self, key):
        return ("image/png", 321 if key.endswith("t.png") else 654)

    def test_the_dry_run_writes_nothing_and_makes_no_store_call(self):
        with mock.patch("apps.media_library.client_upload.confirm_object") as head:
            text = self.run_command()
        self.assertIn("DRY RUN", text)
        self.assertIn("file: to write 3", text)
        self.assertIn("thumb: to write 1", text)
        self.assertIn("poster: to write 1", text)
        self.assertEqual(MediaObject.objects.count(), 0)
        head.assert_not_called()

    def test_apply_writes_a_row_per_key_dated_at_the_asset_and_a_second_run_is_a_no_op(self):
        with mock.patch("apps.media_library.client_upload.confirm_object", side_effect=self.head):
            self.run_command("--apply")
            first = list(MediaObject.objects.order_by("pk").values_list("pk", flat=True))
            again = self.run_command("--apply")
        self.assertEqual(MediaObject.objects.count(), 5)
        self.assertEqual(list(MediaObject.objects.order_by("pk").values_list("pk", flat=True)), first)
        self.assertIn("file: to write 0, written 0", again)
        file_row = MediaObject.objects.get(key=self.image.file_key)
        self.assertEqual((file_row.bytes, file_row.role, file_row.content_type), (1234, "file", "image/png"))
        self.assertEqual(file_row.added_at, self.image.created_at)
        self.assertEqual(MediaObject.objects.get(key=self.image.thumb_key).bytes, 321)
        self.assertEqual(MediaObject.objects.get(key=self.video.poster_key).bytes, 654)
        video_row = MediaObject.objects.get(key=self.video.file_key)
        self.assertEqual((video_row.bytes, video_row.content_type), (9999, "video/mp4"))
        self.assertEqual(MediaObject.objects.get(key=self.bens.file_key).site_id, self.ben_site.pk)
        self.assertEqual(ledger.bytes_at(self.site, timezone.now()), 1234 + 9999)

    def test_a_thumb_whose_head_fails_is_reported_and_gets_no_row_until_a_later_run(self):
        def flaky(key):
            if key == self.image.thumb_key:
                raise store.StoreError("gone")
            return self.head(key)

        with mock.patch("apps.media_library.client_upload.confirm_object", side_effect=flaky):
            text = self.run_command("--apply")
        self.assertIn("size lookups failed (no row written, run again): 1", text)
        self.assertFalse(MediaObject.objects.filter(key=self.image.thumb_key).exists())
        self.assertEqual(MediaObject.objects.count(), 4)
        with mock.patch("apps.media_library.client_upload.confirm_object", side_effect=self.head):
            self.run_command("--apply")
        self.assertEqual(MediaObject.objects.count(), 5)

    def test_no_key_is_printed(self):
        with mock.patch("apps.media_library.client_upload.confirm_object", side_effect=self.head):
            text = self.run_command("--apply") + self.run_command()
        self.assertNotIn("media-library", text)
