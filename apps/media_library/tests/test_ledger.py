"""The media library byte ledger (G2, ClickUp z8uq9mb384): rows, release, the cutoff sum, isolation,
exclusions, the source scan, the migration's constraints, the item cap and the upload throttle.
No test reaches the network: the store is media_library.tests.base.FakeStore."""
import re
import threading
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.billing import plan, usage
from apps.billing.models import UsageEntry
from apps.billing.tests.base import CYCLE_1_END, SECOND, days, make_active
from apps.media_library import ledger, services, store
from apps.media_library.models import MediaAsset, MediaObject
from apps.catalog.tests.base import MEDIA
from apps.media_library.tests.base import (
    BASE_URL, HEAD, MB, RW_SETTINGS, MediaOwnerCase, make_asset, png_file,
)

ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=dt_timezone.utc)
SEC = timedelta(seconds=1)


def rows(site, **filters):
    return MediaObject.objects.filter(site=site, **filters)


class LedgerCase(MediaOwnerCase):
    def new_asset(self, kind="image", site=None, client=None, **extra):
        response = self.send("post", self.url(site_id=(site or self.site).public_id),
                             {"title": "Item", "asset_type": kind, **extra}, client=client)
        self.assertEqual(response.status_code, 201, response.content)
        return MediaAsset.objects.get(public_id=response.json()["id"])

    def upload_to(self, asset, kind="file", file=None):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.upload(f"{asset.public_id}/image/", kind=kind, file=file)
        self.assertEqual(response.status_code, 200, response.content)
        return response


class RowTests(LedgerCase):
    def test_an_image_a_thumb_and_a_poster_each_get_one_row_with_their_stored_size(self):
        image = self.new_asset()
        self.upload_to(image, "file", png_file(b"\x89PNG\r\n\x1a\n" + b"1" * 100))
        self.upload_to(image, "thumb", png_file(b"\x89PNG\r\n\x1a\n" + b"2" * 40))
        video = self.new_asset("video")
        self.upload_to(video, "poster", png_file(b"\x89PNG\r\n\x1a\n" + b"3" * 60))
        found = {(r.role, r.bytes) for r in rows(self.site)}
        self.assertEqual(found, {("file", 108), ("thumb", 48), ("poster", 68)})
        for row in rows(self.site):
            self.assertIsNone(row.removed_at)
            self.assertEqual(row.content_type, "image/png")
            self.assertTrue(row.key.startswith(f"media-library/{self.site_id}/{row.asset.public_id}/"))
        self.assertEqual(MediaObject.objects.count(), 3)

    def test_a_replace_closes_the_old_row_and_leaves_the_new_one_live(self):
        asset = self.new_asset()
        self.upload_to(asset)
        old = MediaAsset.objects.get(pk=asset.pk).file_key
        self.upload_to(asset)
        new = MediaAsset.objects.get(pk=asset.pk).file_key
        self.assertNotEqual(old, new)
        self.assertIsNotNone(MediaObject.objects.get(key=old).removed_at)
        self.assertIsNone(MediaObject.objects.get(key=new).removed_at)
        self.assertEqual(MediaObject.objects.count(), 2)  # rows are never deleted

    def test_clearing_the_thumb_and_deleting_the_asset_close_their_rows_and_keep_them(self):
        asset = self.new_asset()
        self.upload_to(asset, "file")
        self.upload_to(asset, "thumb")
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.send("delete", self.url(f"{asset.public_id}/image/")).status_code, 200)
        self.assertEqual(rows(self.site, role="thumb", removed_at__isnull=False).count(), 1)
        self.assertEqual(rows(self.site, role="file", removed_at__isnull=True).count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.send("delete", self.url(asset.public_id + "/")).status_code, 200)
        self.assertFalse(MediaAsset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(rows(self.site).count(), 2)
        self.assertEqual(rows(self.site, removed_at__isnull=True).count(), 0)
        self.assertTrue(all(r.asset_id is None for r in rows(self.site, role="file")))  # the row outlives the asset
        self.assertEqual(self.store.files, {})

    def test_a_video_attach_records_the_size_from_the_head_and_a_replace_closes_the_old(self):
        video = self.new_asset("video")
        prefix = f"media-library/{self.site_id}/{video.public_id}/"
        with override_settings(**RW_SETTINGS):
            for name, size in (("a.mp4", 7 * MB), ("b.mp4", 3 * MB)):
                with mock.patch(HEAD, return_value=("video/mp4", size)), self.captureOnCommitCallbacks(execute=True):
                    response = self.send("post", self.url(f"{video.public_id}/video/attach/"),
                                         {"url": f"{BASE_URL}/{prefix}{name}"})
                self.assertEqual(response.status_code, 200, response.content)
        first, second = MediaObject.objects.get(key=prefix + "a.mp4"), MediaObject.objects.get(key=prefix + "b.mp4")
        self.assertEqual((first.bytes, first.role, first.content_type), (7 * MB, "file", "video/mp4"))
        self.assertIsNotNone(first.removed_at)
        self.assertEqual((second.bytes, second.removed_at), (3 * MB, None))

    def test_a_refused_video_adds_no_row(self):
        video = self.new_asset("video")
        prefix = f"media-library/{self.site_id}/{video.public_id}/"
        with override_settings(**RW_SETTINGS), mock.patch(HEAD, return_value=("video/webm", MB)):
            response = self.send("post", self.url(f"{video.public_id}/video/attach/"), {"url": f"{BASE_URL}/{prefix}a.mp4"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MediaObject.objects.count(), 0)

    def test_a_failed_save_adds_no_row(self):
        asset = self.new_asset()
        with mock.patch.object(store, "save", side_effect=store.StoreError("down")):
            response = self.upload(f"{asset.public_id}/image/")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(MediaObject.objects.count(), 0)

    def test_record_is_idempotent_on_the_key_and_refuses_bad_input(self):
        asset = self.new_asset()
        first = ledger.record(self.site, asset=asset, role="file", key="media-library/k1", content_type="image/png", size=5)
        again = ledger.record(self.site, asset=asset, role="file", key="media-library/k1", content_type="image/png", size=99)
        self.assertEqual((first.pk, again.bytes), (first.pk, 5))
        for bad in (("nope", "k2", 1), ("file", "", 1), ("file", "k3", -1), ("file", "k4", True)):
            with self.assertRaises(ValueError):
                ledger.record(self.site, asset=asset, role=bad[0], key=bad[1], content_type="image/png", size=bad[2])


class ReleaseTests(LedgerCase):
    def make_live(self, role="file", key="media-library/x/y/z.png", size=10, **extra):
        asset = make_asset(self.site, "Rel", file_key="")
        row = ledger.record(self.site, asset=asset, role=role, key=key, content_type="image/png", size=size, added_at=T0, **extra)
        self.store.files[key] = b"x"
        return row

    def test_a_failed_delete_leaves_the_row_live_and_still_counting(self):
        row = self.make_live()
        self.store.fail_delete = True
        self.assertFalse(ledger.release(row.key, T0 + SEC))
        row.refresh_from_db()
        self.assertIsNone(row.removed_at)
        self.assertEqual(ledger.bytes_at(self.site, T0 + 5 * SEC), 10)
        self.store.fail_delete = False
        self.assertTrue(ledger.release(row.key, T0 + 2 * SEC))
        row.refresh_from_db()
        self.assertEqual(row.removed_at, T0 + 2 * SEC)
        self.assertEqual(ledger.bytes_at(self.site, T0 + 5 * SEC), 0)

    def test_a_key_another_row_still_names_is_not_deleted_and_stays_live(self):
        asset = make_asset(self.site, "Named", file_key="media-library/x/named.png")
        row = ledger.record(self.site, asset=asset, role="file", key=asset.file_key, content_type="image/png", size=4, added_at=T0)
        self.assertFalse(ledger.release(asset.file_key, T0 + SEC))
        row.refresh_from_db()
        self.assertIsNone(row.removed_at)
        self.assertEqual(self.store.deleted, [])

    def test_release_never_raises_and_a_key_with_no_row_is_still_removed(self):
        with mock.patch.object(ledger.file_refs, "is_referenced", side_effect=RuntimeError("db")):
            self.assertFalse(ledger.release("media-library/x/y/q.png"))
        self.store.files["media-library/x/y/q.png"] = b"x"
        self.assertTrue(ledger.release("media-library/x/y/q.png"))
        self.assertIn("media-library/x/y/q.png", self.store.deleted)
        self.assertTrue(ledger.release(""))

    def test_removed_at_is_never_before_added_at(self):
        row = self.make_live()
        self.assertTrue(ledger.release(row.key, T0 - timedelta(days=1)))
        row.refresh_from_db()
        self.assertEqual(row.removed_at, row.added_at)

    def test_the_owner_still_gets_the_same_answer_when_the_delete_fails(self):
        asset = self.new_asset()
        self.upload_to(asset)
        key = MediaAsset.objects.get(pk=asset.pk).file_key
        self.store.fail_delete = True
        with self.captureOnCommitCallbacks(execute=True):
            response = self.send("delete", self.url(asset.public_id + "/"))
        self.assertEqual((response.status_code, response.json()), (200, {"status": "deleted"}))
        self.assertIsNone(MediaObject.objects.get(key=key).removed_at)  # pending: the audit retries it


class CutoffTests(LedgerCase):
    def test_the_sum_is_exact_at_each_edge_and_reads_only_the_ledger(self):
        asset = make_asset(self.site, "Sum", file_key="")
        added, removed = T0, T0 + 10 * SEC
        row = ledger.record(self.site, asset=asset, role="file", key="media-library/s/a.png", content_type="image/png", size=1000, added_at=added)
        self.assertEqual(ledger.bytes_at(self.site, added - SEC), 0)       # before the add
        self.assertEqual(ledger.bytes_at(self.site, added), 1000)          # exactly at the add: counted
        self.assertEqual(ledger.bytes_at(self.site, added + 5 * SEC), 1000)  # between
        ledger.release(row.key, removed)
        self.assertEqual(ledger.bytes_at(self.site, removed - SEC), 1000)
        self.assertEqual(ledger.bytes_at(self.site, removed), 0)           # exactly at the remove: not counted
        self.assertEqual(ledger.bytes_at(self.site, removed + SEC), 0)

    def test_it_is_reproducible_after_the_asset_row_is_deleted_and_the_store_is_wiped(self):
        asset = make_asset(self.site, "Wipe", file_key="")
        ledger.record(self.site, asset=asset, role="file", key="media-library/s/w.png", content_type="image/png", size=500, added_at=T0)
        before = ledger.bytes_at(self.site, T0 + 3600 * SEC)
        asset.delete()
        self.store.files.clear()
        self.assertEqual(MediaAsset.objects.filter(site=self.site).count(), 0)
        self.assertEqual(ledger.bytes_at(self.site, T0 + 3600 * SEC), before)
        self.assertEqual(before, 500)

    def test_only_counted_roles_are_summed_unless_the_caller_passes_roles(self):
        asset = make_asset(self.site, "Roles", asset_type="video", file_key="")
        for role, key, size in (("file", "k/f", 100), ("thumb", "k/t", 20), ("poster", "k/p", 3)):
            ledger.record(self.site, asset=asset, role=role, key=f"media-library/{key}", content_type="image/png", size=size, added_at=T0)
        cutoff = T0 + SEC
        self.assertEqual(ledger.COUNTED_ROLES, ("file",))
        self.assertEqual(ledger.bytes_at(self.site, cutoff), 100)
        self.assertEqual(ledger.bytes_at(self.site, cutoff, roles=("file", "thumb")), 120)
        self.assertEqual(ledger.bytes_at(self.site, cutoff, roles=ledger.ROLES), 123)
        breakdown = ledger.breakdown_at(self.site, cutoff)
        self.assertEqual({r: breakdown[r]["bytes"] for r in breakdown}, {"file": 100, "thumb": 20, "poster": 3})
        self.assertEqual({r: breakdown[r]["objects"] for r in breakdown}, {"file": 1, "thumb": 1, "poster": 1})

    def test_the_sum_of_an_empty_ledger_is_a_plain_zero(self):
        self.assertEqual(ledger.bytes_at(self.site, T0), 0)
        self.assertEqual(ledger.breakdown_at(self.site, T0)["file"], {"objects": 0, "bytes": 0})

    def test_the_display_meter_is_not_switched_to_the_ledger(self):
        from apps.billing import meters

        make_asset(self.site, "Meter", file_size=4321)
        self.assertEqual(meters.storage_used_bytes(self.site), 4321)  # still MediaAsset.file_size
        self.assertEqual(ledger.bytes_at(self.site, timezone.now()), 0)


class IsolationTests(LedgerCase):
    def test_another_sites_sum_is_zero_and_every_key_is_under_its_own_prefix(self):
        asset = self.new_asset()
        self.upload_to(asset)
        self.upload_to(asset, "thumb")
        now = timezone.now() + SEC
        self.assertGreater(ledger.bytes_at(self.site, now), 0)
        self.assertEqual(ledger.bytes_at(self.ben_site, now), 0)
        self.assertEqual(ledger.breakdown_at(self.ben_site, now)["file"]["objects"], 0)
        for row in MediaObject.objects.all():
            self.assertEqual(row.site_id, self.site.pk)
            self.assertTrue(row.key.startswith(f"media-library/{self.site_id}/"))

    def test_two_sites_keys_and_sums_stay_apart(self):
        a = make_asset(self.site, "A", file_key="")
        b = make_asset(self.ben_site, "B", file_key="")
        ledger.record(self.site, asset=a, role="file", key=f"media-library/{self.site_id}/a", content_type="image/png", size=11, added_at=T0)
        ledger.record(self.ben_site, asset=b, role="file", key=f"media-library/{self.ben_site_id}/b", content_type="image/png", size=22, added_at=T0)
        self.assertEqual((ledger.bytes_at(self.site, T0), ledger.bytes_at(self.ben_site, T0)), (11, 22))


class ExclusionTests(MediaOwnerCase):
    """The seal, QR, order and billing proofs (private store), a catalog photo (public store), a
    training image and a notice image are written by other code and never get a ledger row."""

    def test_with_every_excluded_kind_of_file_present_the_ledger_is_empty(self):
        import json

        from apps.catalog import media
        from apps.common import private_files
        from apps.billing import proofs
        from apps.members.models import MemberNotice
        from apps.payments.tests.base import PNG, use_local_private_store
        from apps.training.models import Course

        use_local_private_store(self)
        private = [
            private_files.save("sites/seal", PNG, "image/png"),
            private_files.save("sante/qr", PNG, "image/png"),
            private_files.save("sante/proofs", PNG, "image/png"),
            proofs.save(PNG, "image/png"),
        ]
        self.assertEqual(len(set(private)), 4)

        def put(action, url, **kwargs):
            return json.dumps({"pathname": dict(item.split("=") for item in url.split("?")[1].split("&"))["pathname"].replace("%2F", "/")}).encode()

        with override_settings(**MEDIA), mock.patch.object(media, "_send", side_effect=put):
            photo = media.save(PNG, "image/png")
        self.assertTrue(photo.startswith("catalog/products/"))
        Course.objects.create(site=self.site, title="C", slug="c", image_key="training/abc.png")
        MemberNotice.objects.create(site=self.site, title="N", body="b", image_key="members/notices/abc.png")
        self.assertEqual(MediaObject.objects.count(), 0)
        self.assertEqual(ledger.bytes_at(self.site, timezone.now(), roles=ledger.ROLES), 0)


class SourceScanTests(MediaOwnerCase):
    def sources(self):
        for path in sorted((ROOT / "apps").rglob("*.py")):
            parts = path.relative_to(ROOT).parts
            if "tests" in parts or "migrations" in parts or path.name.startswith("test_"):
                continue
            yield path

    def test_media_object_is_created_and_removed_at_is_written_only_in_ledger_py(self):
        create = re.compile(r"MediaObject\s*\(|MediaObject\.objects\.(?:create|get_or_create|bulk_create|update_or_create)")
        assign = re.compile(r"removed_at\s*=(?!=)|\.update\([^)]*removed_at\s*=|removed_at\s*:")
        offenders = []
        for path in self.sources():
            if path.name == "ledger.py" and path.parent.name == "media_library":
                continue
            text = path.read_text()
            if path.name == "models.py" and path.parent.name == "media_library":
                continue
            if "MediaObject" in text and create.search(text):
                offenders.append(f"create: {path.relative_to(ROOT)}")
            if "media_library" in str(path) or "MediaObject" in text:
                if assign.search(text):
                    offenders.append(f"removed_at: {path.relative_to(ROOT)}")
        self.assertEqual(offenders, [])

    def test_the_scan_sees_the_one_writer(self):
        text = (ROOT / "apps/media_library/ledger.py").read_text()
        self.assertIn("MediaObject.objects.get_or_create", text)
        self.assertIn("removed_at=when", text)


class MigrationConstraintTests(MediaOwnerCase):
    def test_the_table_refuses_a_negative_size_a_removal_before_the_add_and_a_second_key(self):
        asset = make_asset(self.site, "Cons", file_key="")
        base = dict(site=self.site, asset=asset, role="file", content_type="image/png", added_at=T0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MediaObject.objects.create(key="k/neg", bytes=-1, **base)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MediaObject.objects.create(key="k/early", bytes=1, removed_at=T0 - SEC, **base)
        MediaObject.objects.create(key="k/ok", bytes=0, removed_at=T0, **base)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MediaObject.objects.create(key="k/ok", bytes=1, **base)

    def test_a_site_with_ledger_rows_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        asset = make_asset(self.site, "Prot", file_key="")
        ledger.record(self.site, asset=asset, role="file", key="k/p", content_type="image/png", size=1)
        with self.assertRaises(ProtectedError):
            self.site.delete()


@override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True)
class FeedShapeTests(TestCase):
    """What P6.2d will pass to record_storage_snapshot: ledger.bytes_at(...) as measured_bytes."""

    def feed(self, subscription, ref="ledger-1"):
        site = subscription.site
        asset = MediaAsset.objects.create(site=site, title="Big", asset_type="image")
        ledger.record(site, asset=asset, role="file", key="media-library/feed/big.png", content_type="image/png", size=7 * plan.GB_BYTES,
                      added_at=CYCLE_1_END - days(10))
        return usage.record_storage_snapshot(
            site, source_reference=ref, period_end_at=CYCLE_1_END,
            measured_bytes=ledger.bytes_at(site, CYCLE_1_END), measured_at=CYCLE_1_END,
            now=CYCLE_1_END + SECOND,
        )

    def test_the_snapshot_takes_the_ledger_sum_only_with_storage_metered_patched_in(self):
        subscription = make_active()
        self.assertFalse(plan.STORAGE_METERED)
        self.assertEqual(self.feed(subscription).status, "not_metered")
        self.assertEqual(UsageEntry.objects.count(), 0)
        with mock.patch.object(plan, "STORAGE_METERED", True):
            result = self.feed(subscription, "ledger-2")
        self.assertEqual(result.status, "counted")
        self.assertEqual(UsageEntry.objects.get().quantity, 7 * plan.GB_BYTES)

    def test_storage_stays_unmetered_in_code(self):
        self.assertFalse(plan.STORAGE_METERED)


class CapTests(LedgerCase):
    def fill_library(self, count):
        MediaAsset.objects.bulk_create(
            [MediaAsset(site=self.site, title=f"T{i}", asset_type="image", order=i + 1) for i in range(count)]
        )

    def create(self, **extra):
        return self.send("post", self.url(), {"title": "More", "asset_type": "image", **extra})

    def test_the_cap_is_one_constant_of_one_thousand_with_the_approved_words(self):
        self.assertEqual(services.MAX_ITEMS, 1000)
        self.assertEqual(
            services.full_message(), "Your Media Library is full (1,000 items). Delete some items to add more."
        )

    @override_settings()
    def test_the_next_item_after_the_last_free_slot_is_409_and_stores_nothing(self):
        with mock.patch.object(services, "MAX_ITEMS", 3):
            self.fill_library(2)
            self.assertEqual(self.create().status_code, 201)
            before = MediaAsset.objects.count()
            response = self.create()
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(
                response.json(),
                {"error": {"code": "media_library_full", "message": "Your Media Library is full (3 items). Delete some items to add more."}},
            )
            self.assertEqual(MediaAsset.objects.count(), before)
            self.assertEqual(self.store.files, {})
            self.assertEqual(self.create().status_code, 409)
            # Deleting an item frees a slot.
            victim = MediaAsset.objects.filter(site=self.site).first()
            self.assertEqual(self.send("delete", self.url(victim.public_id + "/")).status_code, 200)
            self.assertEqual(self.create().status_code, 201)

    def test_the_real_cap_counts_one_thousand_items_of_this_site_only(self):
        self.fill_library(999)
        MediaAsset.objects.bulk_create([MediaAsset(site=self.ben_site, title="b", asset_type="image") for _ in range(5)])
        self.assertEqual(self.create().status_code, 201)  # the 1,000th
        response = self.create()  # the 1,001st
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["message"], services.full_message())
        self.assertEqual(MediaAsset.objects.filter(site=self.site).count(), 1000)
        ben = self.send("post", self.url(site_id=self.ben_site_id), {"title": "x", "asset_type": "image"}, client=self.ben)
        self.assertEqual(ben.status_code, 201)

    def test_with_the_cap_set_to_none_there_is_no_limit(self):
        with mock.patch.object(services, "MAX_ITEMS", None):
            self.fill_library(1001)
            self.assertFalse(services.library_full(self.site))
            self.assertEqual(self.create().status_code, 201)

    def test_a_slot_taken_between_the_first_check_and_the_lock_is_caught_under_the_lock(self):
        with mock.patch.object(services, "MAX_ITEMS", 2):
            self.fill_library(1)
            real = services.library_full
            calls = []

            def racing(site):
                calls.append(1)
                if len(calls) == 1:
                    result = real(site)  # the first look: one slot free
                    self.fill_library(1)  # another request takes it before the lock
                    return result
                return real(site)

            with mock.patch.object(services, "library_full", side_effect=racing):
                response = self.create()
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(MediaAsset.objects.filter(site=self.site).count(), 2)

    def test_the_full_message_is_shown_when_a_copy_finds_no_room(self):
        # The content copy has its own test (apps/sante/tests/test_content_copy.py).
        with mock.patch.object(services, "MAX_ITEMS", 1):
            self.fill_library(1)
            self.assertTrue(services.library_full(self.site))


class UploadThrottleTests(LedgerCase):
    def test_the_thirty_first_image_upload_in_ten_minutes_is_429(self):
        asset = self.new_asset()
        for _ in range(30):
            self.assertEqual(self.upload(f"{asset.public_id}/image/").status_code, 200)
        self.assertEqual(self.upload(f"{asset.public_id}/image/").status_code, 429)
        # Another account is not affected.
        other = self.new_asset(site=self.ben_site, client=self.ben)
        self.assertEqual(
            self.upload(f"{other.public_id}/image/", client=self.ben, site_id=self.ben_site_id).status_code, 200
        )


@override_settings(BILLING_ENABLED=True)
class CapRaceTests(TransactionTestCase):
    """Two requests racing for the last slot. Needs row locks, so it runs on PostgreSQL only."""

    def test_two_creates_for_the_last_slot_give_exactly_one_winner(self):
        if connection.vendor != "postgresql":
            self.skipTest("select_for_update is a no-op on SQLite")
        site = make_active().site
        MediaAsset.objects.create(site=site, title="one", asset_type="image")
        outcomes = []

        def attempt():
            try:
                services.create_asset(site, {"title": "x", "asset_type": "image"})
                outcomes.append("ok")
            except Exception as error:  # noqa: BLE001
                outcomes.append(getattr(error, "code", type(error).__name__))
            finally:
                connection.close()

        with mock.patch.object(services, "MAX_ITEMS", 2):
            threads = [threading.Thread(target=attempt) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(sorted(outcomes), ["media_library_full", "ok"])
