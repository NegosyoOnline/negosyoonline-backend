"""The site erasure removes the media library's public objects (G2, ClickUp z8uq9mb384)."""
from unittest import mock

from django.test import override_settings

from apps.catalog import media
from apps.lifecycle import dry_run, workflow
from apps.lifecycle.models import ErasureItem
from apps.lifecycle.tests.base import NOW, second_site
from apps.lifecycle.tests.run_base import ON, RunTestCase
from apps.media_library import ledger
from apps.media_library.models import MediaAsset, MediaObject
from apps.operators.models import OperatorAuditEntry


@override_settings(**ON)
class EraseMediaTests(RunTestCase):
    def setUp(self):
        super().setUp()
        self.other = second_site(self.sub, name="other-store")
        self.deleted = []
        patch = mock.patch.object(media, "delete", side_effect=self.deleted.append)
        patch.start()
        self.addCleanup(patch.stop)
        asset = MediaAsset.objects.create(site=self.site, title="Mine", asset_type="image")
        for name in ("a", "b"):
            ledger.record(self.site, asset=asset, role="file", key=f"media-library/{self.site.public_id}/{name}.png", content_type="image/png", size=7, added_at=NOW)
        ledger.record(self.site, asset=asset, role="thumb", key=f"media-library/{self.site.public_id}/t.png", content_type="image/png", size=3, added_at=NOW)
        other_asset = MediaAsset.objects.create(site=self.other, title="Theirs", asset_type="image")
        self.foreign = ledger.record(self.other, asset=other_asset, role="file", key=f"media-library/{self.other.public_id}/z.png", content_type="image/png", size=9, added_at=NOW
        )

    def test_the_dry_run_counts_the_live_objects(self):
        counts, files = dry_run.planned_counts(self.site)
        self.assertEqual(counts["media_files"], 3)
        self.assertGreaterEqual(files, 3)

    def test_the_run_deletes_the_objects_keeps_the_rows_and_counts_them_in_files_deleted(self):
        batch = self.approved()
        self.run_batch(batch)
        item = self.item(batch)
        self.assertEqual(item.status, ErasureItem.Status.ERASED)
        self.assertEqual(item.counts["media_files"], 3)
        self.assertEqual(sorted(self.deleted), sorted(MediaObject.objects.filter(site=self.site).values_list("key", flat=True)))
        self.assertEqual(MediaObject.objects.filter(site=self.site).count(), 3)  # rows are kept
        self.assertFalse(MediaObject.objects.filter(site=self.site, removed_at__isnull=True).exists())
        entry = OperatorAuditEntry.objects.get(action="site_data_erased")
        self.assertEqual(entry.detail["counts"]["media_files"], 3)
        self.assertGreaterEqual(entry.detail["files_deleted"], 3)

    def test_another_sites_objects_are_untouched(self):
        self.run_batch(self.approved())
        self.foreign.refresh_from_db()
        self.assertIsNone(self.foreign.removed_at)
        self.assertNotIn(self.foreign.key, self.deleted)

    def test_a_store_failure_fails_the_item_and_a_second_run_finishes_it(self):
        batch = self.approved()
        calls = []

        def flaky(key):
            calls.append(key)
            if len(calls) == 2:
                raise media.StoreError("down")
            self.deleted.append(key)

        with mock.patch.object(media, "delete", side_effect=flaky):
            result = self.run_batch(batch)
        item = self.item(batch)
        self.assertEqual((result["failed"], item.status, item.reason_code), (1, "failed", "store_unavailable"))
        self.assertIsNone(item.erased_at)
        self.run_batch(batch)
        self.assertEqual(self.item(batch).status, "erased")
        self.assertFalse(MediaObject.objects.filter(site=self.site, removed_at__isnull=True).exists())
