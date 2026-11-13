import base64
import io
import os
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.checks import run_checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.file_backup import checks, clients, constants, crypto, restore, selftest, service, tasks
from apps.file_backup.destination import DestinationError
from apps.file_backup.tests.fakes import FakeDestination, FakeSource

KEY = os.urandom(32)
KEY_B64 = base64.b64encode(KEY).decode()
PNG = b"\x89PNG\r\n\x1a\n"


def cfg(**kwargs):
    kwargs.setdefault("rate_per_second", 0)
    return service.Config(key=KEY, **kwargs)


def make_source(n=3, folder="sante/proofs/site-1"):
    source = FakeSource()
    for i in range(n):
        source.add(f"{folder}/{i:032x}.png", PNG + bytes([i]) * 50)
    return source


class CryptoTests(SimpleTestCase):
    def test_round_trip_and_overhead(self):
        stored = crypto.encrypt(KEY, "a/b.png", b"hello")
        self.assertEqual(len(stored), 5 + crypto.OVERHEAD)
        self.assertEqual(crypto.decrypt(KEY, "a/b.png", stored), b"hello")

    def test_tamper_wrong_key_and_other_name_are_refused(self):
        stored = bytearray(crypto.encrypt(KEY, "a.png", b"hello"))
        with self.assertRaises(crypto.CryptoError):
            crypto.decrypt(os.urandom(32), "a.png", bytes(stored))
        with self.assertRaises(crypto.CryptoError):
            crypto.decrypt(KEY, "b.png", bytes(stored))  # swapped under another name
        stored[-1] ^= 1
        with self.assertRaises(crypto.CryptoError):
            crypto.decrypt(KEY, "a.png", bytes(stored))

    def test_key_must_be_base64_of_32_bytes(self):
        self.assertEqual(crypto.load_key(KEY_B64), KEY)
        for bad in ("", "not base64!!", base64.b64encode(b"short").decode()):
            with self.assertRaises(crypto.CryptoError):
                crypto.load_key(bad)


class RunTests(SimpleTestCase):
    def setUp(self):
        self.source, self.dest = make_source(), FakeDestination()

    def run_backup(self, **kwargs):
        return service.run(self.source, self.dest, cfg(**kwargs.pop("config", {})), **kwargs)

    def test_copies_new_objects_encrypted_and_records_them(self):
        status = self.run_backup()
        self.assertTrue(status["ok"])
        self.assertEqual((status["copied"], status["skipped"], status["backup_count"]), (3, 0, 3))
        for pathname, (content, _) in self.source.objects.items():
            stored = self.dest.objects["data/" + pathname]
            self.assertNotEqual(stored, content)
            self.assertEqual(crypto.decrypt(KEY, pathname, stored), content)
        index = service.load_index(self.dest, cfg())
        self.assertEqual(set(index), set(self.source.objects))
        self.assertEqual(next(iter(index.values()))["content_type"], "image/png")
        self.assertIn("status.json", self.dest.objects)

    def test_second_run_copies_nothing(self):
        self.run_backup()
        self.source.calls.clear()
        status = self.run_backup()
        self.assertEqual((status["copied"], status["skipped"]), (0, 3))
        self.assertNotIn("fetch", self.source.calls)

    def test_changed_etag_is_copied_again(self):
        self.run_backup()
        pathname = sorted(self.source.objects)[0]
        self.source.add(pathname, PNG + b"changed")
        status = self.run_backup()
        self.assertEqual(status["copied"], 1)
        index = service.load_index(self.dest, cfg())
        self.assertEqual(index[pathname]["size"], len(PNG + b"changed"))

    def test_missing_data_object_is_copied_again(self):
        self.run_backup()
        del self.dest.objects["data/" + sorted(self.source.objects)[0]]
        self.assertEqual(self.run_backup()["copied"], 1)

    def test_crash_between_data_and_index_recovers(self):
        # Data object stored, index never saved: the next run copies it again.
        pathname = sorted(self.source.objects)[0]
        self.dest.objects["data/" + pathname] = b"orphan"
        status = self.run_backup()
        self.assertEqual(status["copied"], 3)
        self.assertEqual(
            crypto.decrypt(KEY, pathname, self.dest.objects["data/" + pathname]),
            self.source.objects[pathname][0],
        )

    def test_failed_fetch_is_counted_and_the_run_goes_on(self):
        self.source.fail_fetch.add(sorted(self.source.objects)[0])
        status = self.run_backup()
        self.assertEqual((status["copied"], status["failed"], status["ok"]), (2, 1, False))

    def test_oversize_object_is_skipped_and_reported(self):
        status = self.run_backup(config={"max_bytes": 10})
        self.assertEqual((status["copied"], status["oversize"], status["ok"]), (0, 3, False))
        self.assertNotIn("fetch", self.source.calls)

    def test_time_budget_leaves_the_rest_for_the_next_chunk(self):
        ticks = iter(range(0, 1000, 100))
        status = self.run_backup(config={"budget_seconds": 150}, clock=lambda: next(ticks))
        self.assertGreater(status["remaining"], 0)
        self.assertFalse(status["ok"])
        self.assertEqual(status["copied"] + status["remaining"], 3)

    def test_content_type_falls_back_to_sniffing(self):
        self.source.add("x/" + "a" * 32 + ".pdf", b"%PDF-1.4 hi", "")
        self.run_backup()
        index = service.load_index(self.dest, cfg())
        self.assertEqual(index["x/" + "a" * 32 + ".pdf"]["content_type"], "application/pdf")

    def test_prefix_limits_what_is_read(self):
        self.source.add("other/" + "b" * 32 + ".png", PNG)
        status = self.run_backup(config={"prefix": "other/"})
        self.assertEqual(status["copied"], 1)

    def test_pagination_is_the_sources_job_but_all_pages_are_used(self):
        big = FakeSource()
        for i in range(1203):
            big.add(f"f/{i:032x}.png", PNG + str(i).encode())
        status = service.run(big, self.dest, cfg(sample=0))
        self.assertEqual(status["copied"], 1203)


class DeletionTests(SimpleTestCase):
    def setUp(self):
        self.source, self.dest = make_source(), FakeDestination()
        self.t0 = timezone.now()
        service.run(self.source, self.dest, cfg(), now=self.t0)
        self.gone = sorted(self.source.objects)[0]

    def test_deleted_source_object_is_tombstoned_and_kept(self):
        del self.source.objects[self.gone]
        status = service.run(self.source, self.dest, cfg(), now=self.t0 + timedelta(days=1))
        self.assertEqual((status["tombstoned"], status["purged"]), (1, 0))
        self.assertIn("data/" + self.gone, self.dest.objects)
        index = service.load_index(self.dest, cfg())
        self.assertTrue(index[self.gone]["deleted_seen_at"])

    def test_purged_only_after_the_keep_window(self):
        del self.source.objects[self.gone]
        service.run(self.source, self.dest, cfg(), now=self.t0 + timedelta(days=1))
        early = service.run(self.source, self.dest, cfg(), now=self.t0 + timedelta(days=35))
        self.assertEqual(early["purged"], 0)
        late = service.run(self.source, self.dest, cfg(), now=self.t0 + timedelta(days=37))
        self.assertEqual(late["purged"], 1)
        self.assertNotIn("data/" + self.gone, self.dest.objects)
        self.assertNotIn(self.gone, service.load_index(self.dest, cfg()))

    def test_empty_source_trips_the_brake(self):
        self.source.objects.clear()
        status = service.run(self.source, self.dest, cfg(), now=self.t0 + timedelta(days=1))
        self.assertTrue(status["anomaly"])
        self.assertFalse(status["ok"])
        self.assertEqual(status["tombstoned"], 0)
        # even long after the window, nothing is purged while the source looks wrong
        status = service.run(self.source, self.dest, cfg(), now=self.t0 + timedelta(days=90))
        self.assertEqual(status["purged"], 0)
        self.assertEqual(len([k for k in self.dest.objects if k.startswith("data/")]), 3)

    def test_more_than_a_fifth_missing_trips_the_brake(self):
        source, dest = make_source(40), FakeDestination()
        service.run(source, dest, cfg(sample=0), now=self.t0)
        for pathname in sorted(source.objects)[:12]:
            del source.objects[pathname]
        status = service.run(source, dest, cfg(sample=0), now=self.t0 + timedelta(days=1))
        self.assertTrue(status["anomaly"])
        self.assertEqual(status["tombstoned"], 0)

    def test_a_few_missing_in_a_large_store_is_not_an_anomaly(self):
        source, dest = make_source(40), FakeDestination()
        service.run(source, dest, cfg(sample=0), now=self.t0)
        for pathname in sorted(source.objects)[:5]:
            del source.objects[pathname]
        status = service.run(source, dest, cfg(sample=0), now=self.t0 + timedelta(days=1))
        self.assertEqual((status["anomaly"], status["tombstoned"]), (False, 5))

    def test_integrity_sample_catches_a_damaged_copy(self):
        key = "data/" + self.gone
        damaged = bytearray(self.dest.objects[key])
        damaged[-1] ^= 1
        self.dest.objects[key] = bytes(damaged)
        status = service.run(self.source, self.dest, cfg(), now=self.t0 + timedelta(days=1))
        self.assertEqual(status["verify_failed"], 1)
        self.assertFalse(status["ok"])

    def test_old_index_objects_are_pruned_to_thirty(self):
        for day in range(1, 36):
            service.run(self.source, self.dest, cfg(), now=self.t0 + timedelta(days=day))
            # changes nothing, so make each run write an index: verification stamps do that
        names = [k for k in self.dest.objects if k.startswith("index/") and k != "index/latest"]
        self.assertLessEqual(len(names), service.KEEP_INDEXES)


class RestoreTests(SimpleTestCase):
    def setUp(self):
        self.source, self.dest = make_source(), FakeDestination()
        service.run(self.source, self.dest, cfg())
        self.originals = dict(self.source.objects)
        self.gone = sorted(self.source.objects)[0]
        del self.source.objects[self.gone]

    def test_dry_run_writes_nothing(self):
        counts = restore.restore(self.source, self.dest, cfg())
        self.assertEqual(counts["would_restore"], 1)
        self.assertNotIn("put", self.source.calls)
        self.assertNotIn(self.gone, self.source.objects)

    def test_missing_objects_come_back_and_existing_are_skipped(self):
        counts = restore.restore(self.source, self.dest, cfg(), apply=True)
        self.assertEqual((counts["restored"], counts["exists"]), (1, 2))
        self.assertEqual(self.source.objects[self.gone], self.originals[self.gone])
        again = restore.restore(self.source, self.dest, cfg(), apply=True)
        self.assertEqual((again["restored"], again["exists"]), (0, 3))

    def test_never_overwrites_a_live_object(self):
        live = sorted(self.source.objects)[0]
        self.source.add(live, b"newer")
        restore.restore(self.source, self.dest, cfg(), apply=True)
        self.assertEqual(self.source.objects[live][0], b"newer")

    def test_tombstoned_objects_need_the_flag(self):
        service.run(self.source, self.dest, cfg(), now=timezone.now())  # tombstones self.gone
        counts = restore.restore(self.source, self.dest, cfg(), apply=True)
        self.assertEqual((counts["restored"], counts["tombstoned_skipped"]), (0, 1))
        counts = restore.restore(self.source, self.dest, cfg(), include_deleted=True, apply=True)
        self.assertEqual(counts["restored"], 1)

    def test_a_copy_that_fails_its_hash_is_refused(self):
        index = service.load_index(self.dest, cfg())
        index[self.gone]["sha256"] = "0" * 64
        service.save_index(self.dest, cfg(), index, "z.json.gz.enc")
        counts = restore.restore(self.source, self.dest, cfg(), apply=True)
        self.assertEqual((counts["mismatch"], counts["restored"]), (1, 0))
        self.assertNotIn(self.gone, self.source.objects)

    def test_key_and_prefix_select(self):
        self.assertEqual(restore.restore(self.source, self.dest, cfg(), keys=["nope"])["considered"], 0)
        self.assertEqual(restore.restore(self.source, self.dest, cfg(), prefix="sante/")["considered"], 3)

    def test_listing_shows_folders_not_pathnames(self):
        self.assertEqual(restore.listing(self.dest, cfg()), {"sante": [3, sum(len(c) for c, _ in self.originals.values()), 0]})

    def test_purge_prefix_removes_copies_of_erased_files_only(self):
        counts = restore.purge_prefix(self.source, self.dest, cfg(), "sante/", apply=False)
        self.assertEqual((counts["matched"], counts["still_live"], counts["purged"]), (3, 2, 0))
        counts = restore.purge_prefix(self.source, self.dest, cfg(), "sante/", apply=True)
        self.assertEqual(counts["purged"], 1)
        self.assertNotIn("data/" + self.gone, self.dest.objects)
        self.assertNotIn(self.gone, service.load_index(self.dest, cfg()))
        with self.assertRaises(ValueError):
            restore.purge_prefix(self.source, self.dest, cfg(), "", apply=True)


class SelftestTests(SimpleTestCase):
    def test_passes_and_cleans_up_and_never_touches_real_objects(self):
        source, dest = make_source(), FakeDestination()
        before = dict(source.objects)
        results = selftest.run(source, dest, KEY)
        self.assertTrue(all(passed for _, passed in results), results)
        self.assertEqual(source.objects, before)
        self.assertEqual(dest.objects, {})
        self.assertNotIn("sante/proofs/site-1", " ".join(name for name, _ in results))

    def test_command_runs_the_selftest_through_the_clients(self):
        source, dest = FakeSource(), FakeDestination()
        out = io.StringIO()
        with override_settings(FILE_BACKUP_ENCRYPTION_KEY=KEY_B64), mock.patch.object(
            clients, "build_source", return_value=source
        ), mock.patch.object(clients, "build_destination", return_value=dest):
            call_command("backup_private_files", "--selftest", stdout=out)
        self.assertIn("Self-test passed.", out.getvalue())


class CommandTests(SimpleTestCase):
    def test_exactly_one_mode(self):
        with self.assertRaises(CommandError):
            call_command("backup_private_files")
        with self.assertRaises(CommandError):
            call_command("backup_private_files", "--measure", "--status")

    def test_measure_prints_counts_and_no_pathname(self):
        source, out = make_source(), io.StringIO()
        with mock.patch.object(clients, "build_source", return_value=source):
            call_command("backup_private_files", "--measure", stdout=out)
        text = out.getvalue()
        self.assertIn("3 objects", text)
        self.assertIn("sante: 3 objects", text)
        self.assertNotIn("proofs", text)

    def test_status_compares_the_counts(self):
        source, dest, out = make_source(), FakeDestination(), io.StringIO()
        service.run(source, dest, cfg())
        with override_settings(FILE_BACKUP_ENCRYPTION_KEY=KEY_B64), mock.patch.object(
            clients, "build_destination", return_value=dest
        ):
            call_command("backup_private_files", "--status", stdout=out)
        self.assertIn("source count equals backup count: yes", out.getvalue())

    def test_purge_apply_needs_actor_and_reference(self):
        with override_settings(FILE_BACKUP_ENCRYPTION_KEY=KEY_B64), self.assertRaises(CommandError):
            call_command("backup_private_files", "--purge-prefix", "sante/", "--apply")

    def test_restore_command_dry_run_and_apply(self):
        source, dest = make_source(), FakeDestination()
        service.run(source, dest, cfg())
        gone = sorted(source.objects)[0]
        del source.objects[gone]
        out = io.StringIO()
        with override_settings(FILE_BACKUP_ENCRYPTION_KEY=KEY_B64), mock.patch.object(
            clients, "build_source", return_value=source
        ), mock.patch.object(clients, "build_destination", return_value=dest):
            call_command("restore_private_files", "--missing", stdout=out)
            self.assertNotIn(gone, source.objects)
            call_command("restore_private_files", "--missing", "--apply", stdout=out)
            call_command("restore_private_files", "--list", stdout=out)
            with self.assertRaises(CommandError):
                call_command("restore_private_files")
        self.assertIn(gone, source.objects)
        self.assertNotIn(gone, out.getvalue())


class TaskTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_switch_off_builds_and_calls_no_client(self):
        with mock.patch.object(clients, "build_source") as source, mock.patch.object(
            clients, "build_destination"
        ) as dest, mock.patch.object(service, "run") as run:
            self.assertEqual(tasks.run(), {"skipped": "switched off"})
        source.assert_not_called()
        dest.assert_not_called()
        run.assert_not_called()
        self.assertIsNone(cache.get(constants.LAST_OK_KEY))

    def patched(self):
        self.source, self.dest = make_source(), FakeDestination()
        return override_settings(FILE_BACKUP_ENABLED=True, FILE_BACKUP_ENCRYPTION_KEY=KEY_B64)

    def test_a_good_run_sets_the_last_ok_key_and_runs_once_a_day(self):
        with self.patched(), mock.patch.object(clients, "build_source", return_value=self.source), \
                mock.patch.object(clients, "build_destination", return_value=self.dest):
            counts = tasks.run()
            self.assertEqual(counts["copied"], 3)
            self.assertIsNotNone(cache.get(constants.LAST_OK_KEY))
            self.assertEqual(tasks.run(), {"skipped": "already ran today"})

    def test_a_run_never_overlaps_another(self):
        cache.set(constants.RUNNING_KEY, 1, 60)
        with self.patched(), mock.patch.object(service, "run") as run:
            self.assertEqual(tasks.run(), {"skipped": "already running"})
        run.assert_not_called()

    def test_a_failed_run_logs_by_type_and_can_be_started_again(self):
        with self.patched(), mock.patch.object(clients, "build_source", side_effect=ValueError("secret")), \
                self.assertLogs("apps.file_backup.tasks", "ERROR") as logs:
            self.assertEqual(tasks.run(), {"error": "ValueError"})
        self.assertNotIn("secret", "".join(logs.output))
        self.assertIsNone(cache.get(constants.RUNNING_KEY))
        with self.patched(), mock.patch.object(clients, "build_source", return_value=self.source), \
                mock.patch.object(clients, "build_destination", return_value=self.dest):
            self.assertEqual(tasks.run()["copied"], 3)

    def test_an_unfinished_run_queues_the_next_chunk_up_to_the_limit(self):
        unfinished = {"ok": False, "anomaly": False, "remaining": 5}
        with self.patched(), mock.patch.object(clients, "build_source"), \
                mock.patch.object(clients, "build_destination"), \
                mock.patch.object(clients, "build_config"), \
                mock.patch.object(service, "run", return_value=unfinished), \
                mock.patch.object(tasks.run, "apply_async") as queue:
            tasks.run()
            queue.assert_called_once_with(kwargs={"chain": 1}, countdown=constants.CHAIN_COUNTDOWN)
            queue.reset_mock()
            tasks.run(chain=constants.MAX_CHAIN)
            queue.assert_not_called()
        self.assertIsNone(cache.get(constants.LAST_OK_KEY))

    def test_an_anomaly_is_not_chained(self):
        anomaly = {"ok": False, "anomaly": True, "remaining": 0}
        with self.patched(), mock.patch.object(clients, "build_source"), \
                mock.patch.object(clients, "build_destination"), \
                mock.patch.object(clients, "build_config"), \
                mock.patch.object(service, "run", return_value=anomaly), \
                mock.patch.object(tasks.run, "apply_async") as queue, \
                self.assertLogs("apps.file_backup.tasks", "ERROR"):
            tasks.run()
        queue.assert_not_called()


class ReadyzFieldTests(TestCase):
    def setUp(self):
        cache.clear()

    def field(self):
        from apps.health import views

        return views.check_files_backup()

    def test_off_is_true_and_reads_no_cache_key(self):
        with mock.patch("apps.health.views.cache") as fake_cache:
            self.assertTrue(self.field())
        fake_cache.get.assert_not_called()

    @override_settings(FILE_BACKUP_ENABLED=True)
    def test_on_needs_a_fresh_good_run(self):
        self.assertFalse(self.field())  # no key: never ran, or Redis was lost
        cache.set(constants.LAST_OK_KEY, timezone.now().timestamp(), None)
        self.assertTrue(self.field())
        cache.set(constants.LAST_OK_KEY, (timezone.now() - timedelta(hours=37)).timestamp(), None)
        self.assertFalse(self.field())
        cache.set(constants.LAST_OK_KEY, (timezone.now() - timedelta(hours=35)).timestamp(), None)
        self.assertTrue(self.field())


class SystemCheckTests(SimpleTestCase):
    def test_nothing_while_off(self):
        self.assertEqual(checks.check_file_backup(None), [])

    @override_settings(FILE_BACKUP_ENABLED=True, FILE_BACKUP_BUCKET="", FILE_BACKUP_ENCRYPTION_KEY="x")
    def test_warns_about_each_missing_setting(self):
        ids = {w.id for w in checks.check_file_backup(None)}
        self.assertTrue({"file_backup.W001", "file_backup.W002", "file_backup.W003"} <= ids)

    @override_settings(
        FILE_BACKUP_ENABLED=True, PRIVATE_BLOB_READ_WRITE_TOKEN="t", FILE_BACKUP_BUCKET="b",
        FILE_BACKUP_ENDPOINT="https://e", FILE_BACKUP_ACCESS_KEY_ID="a",
        FILE_BACKUP_SECRET_ACCESS_KEY="s", FILE_BACKUP_ENCRYPTION_KEY=KEY_B64,
    )
    def test_complete_settings_pass(self):
        self.assertEqual(checks.check_file_backup(None), [])

    def test_registered_with_the_framework(self):
        self.assertIsInstance(run_checks(), list)


class NoSecretsInErrorsTests(SimpleTestCase):
    def test_source_and_destination_errors_hold_neither_token_nor_name(self):
        from apps.file_backup.source import BlobSource, SourceError

        token = "vercel_blob_rw_abc123_SECRETVALUE"
        source = BlobSource(token)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")), mock.patch(
            "time.sleep"
        ), self.assertLogs("apps.file_backup.source", "WARNING") as logs, self.assertRaises(SourceError) as caught:
            source.fetch("sante/secret-name.png")
        shown = str(caught.exception) + "".join(logs.output)
        self.assertNotIn("SECRETVALUE", shown)
        self.assertNotIn("secret-name", shown)
        with self.assertRaises(SourceError):
            BlobSource("not-a-token")

    def test_destination_errors_are_not_chained_to_the_raw_error(self):
        from apps.file_backup.destination import S3Destination

        destination = S3Destination.__new__(S3Destination)
        destination._bucket = "b"
        with self.assertRaises(DestinationError) as caught:
            destination._call("get", mock.Mock(side_effect=RuntimeError("key=SECRET")), Key="k")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_lister_follows_cursors(self):
        from apps.file_backup.source import BlobSource

        pages = [
            b'{"blobs":[{"pathname":"a","size":1,"uploadedAt":"t","etag":"e"}],"cursor":"c1","hasMore":true}',
            b'{"blobs":[{"pathname":"b","size":2}],"hasMore":false}',
        ]
        source = BlobSource("vercel_blob_rw_abc123_SECRETVALUE")
        with mock.patch.object(source, "_send", side_effect=pages) as send:
            names = [o["pathname"] for o in source.list_objects("p/")]
        self.assertEqual(names, ["a", "b"])
        self.assertIn("cursor=c1", send.call_args_list[1].args[1])
        self.assertIn("prefix=p%2F", send.call_args_list[0].args[1])
