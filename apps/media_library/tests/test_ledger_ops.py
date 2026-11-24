"""The ledger's operations: the backfill command, purge_site, the store list call and the daily
audit (G2, ClickUp z8uq9mb384). No test reaches the network."""
import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.catalog import media
from apps.catalog.tests.base import MEDIA
from apps.media_library import audit, ledger, services, store, tasks
from apps.media_library.models import MediaObject
from apps.media_library.tests.base import MediaOwnerCase, make_asset


class PurgeTests(MediaOwnerCase):
    def setUp(self):
        super().setUp()
        self.asset = make_asset(self.site, "P", file_key="")
        self.bens = make_asset(self.ben_site, "B", file_key="")
        self.now = timezone.now()
        for name in ("a", "b", "c"):
            key = f"media-library/{self.site_id}/{name}.png"
            ledger.record(self.site, asset=self.asset, role="file", key=key, content_type="image/png", size=10, added_at=self.now - timedelta(days=1))
            self.store.files[key] = b"x"
        self.ben_key = f"media-library/{self.ben_site_id}/b.png"
        ledger.record(self.ben_site, asset=self.bens, role="file", key=self.ben_key, content_type="image/png", size=5, added_at=self.now - timedelta(days=1))
        self.store.files[self.ben_key] = b"x"

    def test_it_removes_every_live_object_of_the_site_keeps_the_rows_and_a_second_run_counts_zero(self):
        self.assertEqual(services.purge_site(self.site, self.now), 3)
        self.assertEqual(MediaObject.objects.filter(site=self.site).count(), 3)
        self.assertFalse(MediaObject.objects.filter(site=self.site, removed_at__isnull=True).exists())
        self.assertEqual(sorted(self.store.files), [self.ben_key])  # the other site is untouched
        self.assertIsNone(MediaObject.objects.get(key=self.ben_key).removed_at)
        self.assertEqual(services.purge_site(self.site, self.now), 0)
        self.assertEqual(ledger.bytes_at(self.site, self.now + timedelta(seconds=1)), 0)

    def test_an_absent_key_counts_as_gone(self):
        self.store.files.clear()
        self.assertEqual(services.purge_site(self.site, self.now), 3)

    def test_a_store_failure_raises_and_closes_only_what_was_deleted(self):
        calls = []

        def flaky(key):
            calls.append(key)
            if len(calls) == 2:
                raise store.StoreError("down")
            self.store._discard(key)

        with mock.patch.object(store, "delete", side_effect=flaky):
            with self.assertRaises(store.StoreError):
                services.purge_site(self.site, self.now)
        self.assertEqual(MediaObject.objects.filter(site=self.site, removed_at__isnull=True).count(), 2)
        self.assertEqual(MediaObject.objects.filter(site=self.site, removed_at__isnull=False).count(), 1)
        self.assertEqual(services.purge_site(self.site, self.now), 2)  # a retry finishes the rest


class ListPrefixTests(MediaOwnerCase):
    def page(self, blobs, cursor=None):
        return json.dumps({"blobs": blobs, "hasMore": bool(cursor), **({"cursor": cursor} if cursor else {})}).encode()

    def test_it_lists_by_prefix_across_pages_and_sends_the_prefix_each_time(self):
        blob = lambda name, size: {"pathname": name, "size": size, "uploadedAt": "2026-11-01T00:00:00.000Z"}  # noqa: E731
        answers = [self.page([blob("media-library/a", 1)], "c1"), self.page([blob("media-library/b", 2)])]
        with override_settings(**MEDIA), mock.patch.object(media, "_send", side_effect=answers) as send:
            found = list(media.list_prefix("media-library/"))
        self.assertEqual([f["pathname"] for f in found], ["media-library/a", "media-library/b"])
        urls = [call.args[1] for call in send.call_args_list]
        self.assertIn("prefix=media-library%2F", urls[0])
        self.assertIn("prefix=media-library%2F", urls[1])
        self.assertIn("cursor=c1", urls[1])
        self.assertEqual(send.call_args_list[0].kwargs["method"], "GET")

    def test_an_unknown_shape_or_a_stuck_cursor_raises_and_an_empty_prefix_is_refused(self):
        with override_settings(**MEDIA):
            with mock.patch.object(media, "_send", return_value=b"{}"):
                with self.assertRaises(media.StoreError):
                    list(media.list_prefix("media-library/"))
            same = self.page([], "c1")
            with mock.patch.object(media, "_send", side_effect=[same, same, same]):
                with self.assertRaises(media.StoreError):
                    list(media.list_prefix("media-library/"))
            with self.assertRaises(media.StoreError):
                list(media.list_prefix(""))
        with self.assertRaises(media.StoreError):  # not configured
            list(media.list_prefix("media-library/"))


@override_settings(MEDIA_LEDGER_AUDIT_ENABLED=True)
class AuditTests(MediaOwnerCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.old = self.now - timedelta(days=2)

    def listing(self, *names, uploaded=None):
        uploaded = uploaded or self.old
        return [{"pathname": n, "size": 1, "uploaded_at": uploaded.isoformat()} for n in names]

    def run_audit(self, listed):
        with mock.patch.object(media, "list_prefix", return_value=iter(listed)) as listing:
            return audit.run(self.now), listing

    def asset_with_row(self, name="k", **asset_fields):
        key = f"media-library/{self.site_id}/{name}.png"
        asset = make_asset(self.site, name, file_key=key, **asset_fields)
        ledger.record(self.site, asset=asset, role="file", key=key, content_type="image/png", size=5, added_at=self.old)
        return asset, key

    def test_an_orphan_and_a_ghost_are_each_counted_and_nothing_is_changed(self):
        _, ghost_key = self.asset_with_row("ghost")
        _, fine_key = self.asset_with_row("fine")
        listed = self.listing(fine_key, f"media-library/{self.site_id}/orphan.mp4")
        before = list(MediaObject.objects.values_list("pk", "removed_at"))
        with self.assertLogs("security", "WARNING") as logs, self.assertLogs(audit.logger, "ERROR"):
            counts, _ = self.run_audit(listed)
        self.assertEqual((counts["orphans"], counts["ghosts"], counts["retried"]), (1, 1, 0))
        self.assertEqual(list(MediaObject.objects.values_list("pk", "removed_at")), before)  # a ghost is never auto-removed
        line = logs.output[0]
        self.assertIn("orphans=1", line)
        for secret in (ghost_key, fine_key, self.site_id, "orphan.mp4"):
            self.assertNotIn(secret, line)

    def test_keys_outside_media_library_are_ignored(self):
        listed = self.listing("catalog/products/abc.png", "sante/proofs/x.png", "media-librarian/x.png")
        counts, listing = self.run_audit(listed)
        self.assertEqual((counts["listed"], counts["orphans"], counts["ghosts"]), (0, 0, 0))
        listing.assert_called_once_with("media-library/")  # the audit lists this prefix only

    def test_an_object_still_in_its_grace_hour_is_not_an_orphan_and_a_new_row_is_not_a_ghost(self):
        counts, _ = self.run_audit(self.listing(f"media-library/{self.site_id}/fresh.png", uploaded=self.now - timedelta(minutes=5)))
        self.assertEqual(counts["orphans"], 0)
        asset = make_asset(self.site, "newrow", file_key=f"media-library/{self.site_id}/newrow.png")
        ledger.record(self.site, asset=asset, role="file", key=asset.file_key, content_type="image/png", size=1, added_at=self.now - timedelta(minutes=5))
        counts, _ = self.run_audit([])
        self.assertEqual(counts["ghosts"], 0)

    def test_a_pending_release_is_retried_and_closed_and_a_still_failing_one_stays_live(self):
        asset, key = self.asset_with_row("pending")
        asset.delete()  # the asset is gone, the delete never finished: the row is still live
        self.store.files[key] = b"x"
        self.store.fail_delete = True
        with self.assertLogs(audit.logger, "ERROR"), self.assertLogs("security", "WARNING"):
            counts, _ = self.run_audit(self.listing(key))
        self.assertEqual((counts["retried"], counts["released"], counts["still_pending"]), (1, 0, 1))
        self.assertIsNone(MediaObject.objects.get(key=key).removed_at)
        self.store.fail_delete = False
        with self.assertLogs("security", "WARNING"):
            counts, _ = self.run_audit(self.listing(key))
        self.assertEqual((counts["retried"], counts["released"]), (1, 1))
        self.assertIsNotNone(MediaObject.objects.get(key=key).removed_at)
        self.assertNotIn(key, self.store.files)

    def test_an_object_whose_row_was_removed_is_an_orphan_until_it_is_gone(self):
        asset, key = self.asset_with_row("lost")
        asset.delete()
        ledger.release(key, self.old)
        with self.assertLogs(audit.logger, "ERROR"), self.assertLogs("security", "WARNING"):
            counts, _ = self.run_audit(self.listing(key))
        self.assertEqual(counts["orphans"], 1)

    def test_while_the_switch_is_off_nothing_is_listed_and_the_task_returns_at_once(self):
        with override_settings(MEDIA_LEDGER_AUDIT_ENABLED=False), mock.patch.object(media, "list_prefix") as listing:
            self.assertEqual(audit.run(self.now), {"skipped": "switched off"})
            self.assertEqual(tasks.ledger_audit(), {"skipped": "switched off"})
        listing.assert_not_called()

    def test_the_beat_entry_is_daily_and_names_the_task(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["media-ledger-audit-daily"]
        self.assertEqual(entry["task"], "media_library.ledger_audit")
        self.assertEqual((entry["schedule"].hour, entry["schedule"].minute), ({5}, {0}))

    def test_a_store_that_cannot_be_listed_is_a_logged_error_not_a_crash(self):
        with mock.patch.object(media, "list_prefix", side_effect=media.StoreError("down")):
            with self.assertLogs(tasks.logger, "ERROR"):
                self.assertEqual(tasks.ledger_audit(), {"error": "StoreError"})


class SwitchDefaultTests(MediaOwnerCase):
    def test_the_audit_switch_ships_off(self):
        import os
        from unittest import mock as m

        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEDIA_LEDGER_AUDIT_ENABLED", None)
            from django.conf import settings

            self.assertFalse(settings.MEDIA_LEDGER_AUDIT_ENABLED)
