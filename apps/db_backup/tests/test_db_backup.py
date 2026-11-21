"""No network, no pg_dump, no Blob: a fake store and a fake dump."""
import base64
import io
import os
from datetime import datetime, timedelta, timezone as tz
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.db_backup import constants, dump, service, tasks
from apps.file_backup import crypto
from apps.file_backup.source import SourceError
from apps.health.tasks import HEARTBEAT_KEY

KEY = os.urandom(32)
KEY_B64 = base64.b64encode(KEY).decode()
SECRET_DUMP = b"PGDMP-super-secret-row-content"
UTC = tz.utc


class FakeStore:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_list = False

    def list_objects(self, prefix=""):
        self.calls.append("list")
        if self.fail_list:
            raise SourceError("list failed")
        for pathname in sorted(self.objects):
            if pathname.startswith(prefix):
                yield {"pathname": pathname, "size": len(self.objects[pathname]), "uploaded_at": "", "etag": ""}

    def put(self, pathname, content, content_type):
        self.calls.append("put")
        self.objects[pathname] = content

    def fetch(self, pathname):
        self.calls.append("fetch")
        return self.objects[pathname], "application/octet-stream"

    def delete(self, pathname):
        self.calls.append("delete")
        self.objects.pop(pathname, None)


def at(year, month, day, hour=4):
    return datetime(year, month, day, hour, tzinfo=UTC)


class CryptoTests(SimpleTestCase):
    def test_round_trip_through_the_service(self):
        store, name = FakeStore(), service.pathname_for(at(2026, 11, 22))
        service.run_backup(store, KEY, lambda: SECRET_DUMP, at(2026, 11, 22))
        stored = store.objects[name]
        self.assertNotIn(SECRET_DUMP, stored)
        self.assertEqual(len(stored), len(SECRET_DUMP) + crypto.OVERHEAD)
        self.assertEqual(service.fetch_plain(store, KEY, name), SECRET_DUMP)

    def test_wrong_key_swapped_name_and_tamper_are_refused(self):
        store = FakeStore()
        service.run_backup(store, KEY, lambda: SECRET_DUMP, at(2026, 11, 22))
        service.run_backup(store, KEY, lambda: b"other", at(2026, 11, 23))
        first, second = sorted(store.objects)
        with self.assertRaises(crypto.CryptoError):
            service.fetch_plain(store, os.urandom(32), first)
        store.objects[second] = store.objects[first]  # a copy swapped under another name
        with self.assertRaises(crypto.CryptoError):
            service.fetch_plain(store, KEY, second)

    def test_only_our_names_can_be_fetched(self):
        with self.assertRaises(service.BackupError):
            service.fetch_plain(FakeStore(), KEY, "private/proof.png")

    def test_empty_dump_is_not_uploaded(self):
        store = FakeStore()
        with self.assertRaises(service.BackupError):
            service.run_backup(store, KEY, lambda: b"", at(2026, 11, 22))
        self.assertEqual(store.objects, {})


class RetentionTests(SimpleTestCase):
    def copies(self, moments):
        return [(service.pathname_for(m), m) for m in sorted(moments, reverse=True)]

    def test_keeps_14_daily_and_8_weekly_and_deletes_the_rest(self):
        start = at(2026, 1, 1)
        days = [start + timedelta(days=n) for n in range(120)]  # 120 consecutive nightly copies
        keep = service.select_keep(self.copies(days))
        newest14 = {service.pathname_for(m) for m in days[-14:]}
        self.assertTrue(newest14 <= keep)
        weeks = {}
        for m in days:
            weeks[m.isocalendar()[:2]] = service.pathname_for(m)  # newest of each week
        newest8 = [weeks[w] for w in sorted(weeks)[-8:]]
        self.assertTrue(set(newest8) <= keep)
        self.assertEqual(keep, newest14 | set(newest8))
        self.assertLessEqual(len(keep), 14 + 8)
        self.assertGreater(len(keep), 14)

    def test_two_copies_in_a_day_keep_only_the_newest(self):
        morning, evening = at(2026, 11, 22, 4), at(2026, 11, 22, 18)
        keep = service.select_keep(self.copies([morning, evening]))
        self.assertEqual(keep, {service.pathname_for(evening)})

    def test_a_stopped_backup_never_empties_the_store(self):
        old = [at(2026, 1, 1) + timedelta(days=n) for n in range(5)]
        self.assertEqual(len(service.select_keep(self.copies(old))), 5)

    def test_apply_retention_deletes_only_our_named_copies(self):
        store = FakeStore()
        for n in range(30):
            store.objects[service.pathname_for(at(2026, 10, 1) + timedelta(days=n))] = b"x"
        store.objects["private/proof.png"] = b"keep me"
        store.objects["db-backups/readme.txt"] = b"keep me too"
        deleted = service.apply_retention(store)
        self.assertGreater(deleted, 0)
        self.assertIn("private/proof.png", store.objects)
        self.assertIn("db-backups/readme.txt", store.objects)
        self.assertEqual(len([p for p in store.objects if service.parse_name(p)]), 30 - deleted)

    def test_run_applies_retention_after_the_upload(self):
        store = FakeStore()
        for n in range(40):
            store.objects[service.pathname_for(at(2026, 9, 1) + timedelta(days=n))] = b"x"
        result = service.run_backup(store, KEY, lambda: SECRET_DUMP, at(2026, 11, 22))
        self.assertGreater(result["deleted"], 0)
        self.assertIn(service.pathname_for(at(2026, 11, 22)), store.objects)
        self.assertLess(store.calls.index("put"), store.calls.index("delete"))


@override_settings(DB_BACKUP_ENABLED=True, DB_BACKUP_ENCRYPTION_KEY=KEY_B64,
                   PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_secret")
class TaskTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.store = FakeStore()
        patches = [
            mock.patch("apps.db_backup.clients.build_store", return_value=self.store),
            mock.patch("apps.db_backup.dump.dump_database", return_value=SECRET_DUMP),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    @override_settings(DB_BACKUP_ENABLED=False)
    def test_off_runs_nothing(self):
        with mock.patch("apps.db_backup.clients.build_store") as build:
            self.assertEqual(tasks.run(), {"skipped": "switched off"})
        build.assert_not_called()
        self.assertEqual(self.store.calls, [])

    def test_run_uploads_once_per_day_and_marks_it_good(self):
        self.assertEqual(tasks.run()["uploaded"], 1)
        self.assertIsNotNone(cache.get(constants.LAST_OK_KEY))
        self.assertEqual(tasks.run(), {"skipped": "already ran today"})
        self.assertEqual(self.store.calls.count("put"), 1)

    def test_failure_logs_the_type_only_and_allows_a_retry(self):
        with mock.patch("apps.db_backup.dump.dump_database", side_effect=dump.DumpError("host=secret.internal")):
            with self.assertLogs("apps.db_backup.tasks", "ERROR") as logs:
                result = tasks.run()
        self.assertEqual(result, {"error": "DumpError"})
        output = "\n".join(logs.output)
        self.assertNotIn("secret.internal", output)
        self.assertIsNone(cache.get(constants.LAST_OK_KEY))
        self.assertEqual(tasks.run()["uploaded"], 1)  # the day lock was released

    def test_logs_hold_no_key_dump_or_pathname(self):
        with self.assertLogs("apps.db_backup.tasks", "INFO") as logs:
            tasks.run()
        output = "\n".join(logs.output)
        for forbidden in (KEY_B64, SECRET_DUMP.decode(), "db-backups/", "vercel_blob"):
            self.assertNotIn(forbidden, output)


class CommandTests(SimpleTestCase):
    def run_command(self, command="backup_database", **kwargs):
        out = io.StringIO()
        call_command(command, stdout=out, **kwargs)
        return out.getvalue()

    @override_settings(DB_BACKUP_ENCRYPTION_KEY="", PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_s")
    def test_backup_refuses_without_the_key_and_dumps_nothing(self):
        with mock.patch("apps.db_backup.dump.dump_database") as dumper:
            with self.assertRaisesMessage(CommandError, "Refusing to run"):
                self.run_command()
        dumper.assert_not_called()

    @override_settings(DB_BACKUP_ENCRYPTION_KEY="not base64!!", PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_s")
    def test_backup_refuses_a_bad_key(self):
        with self.assertRaises(CommandError):
            self.run_command()

    @override_settings(DB_BACKUP_ENABLED=False, DB_BACKUP_ENCRYPTION_KEY=KEY_B64,
                       PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_s")
    def test_backup_runs_with_the_switch_off_and_prints_counts_only(self):
        store = FakeStore()
        with mock.patch("apps.db_backup.clients.build_store", return_value=store), \
                mock.patch("apps.db_backup.dump.dump_database", return_value=SECRET_DUMP):
            text = self.run_command()
        self.assertIn("Backup stored", text)
        self.assertNotIn("db-backups/", text)
        self.assertEqual(len(store.objects), 1)

    @override_settings(DB_BACKUP_ENCRYPTION_KEY=KEY_B64, PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_s")
    def test_restore_needs_an_explicit_target(self):
        with mock.patch("apps.db_backup.clients.build_store", return_value=FakeStore()):
            with self.assertRaisesMessage(CommandError, "both --name and --target-url"):
                self.run_command("restore_database", name="db-backups/x")

    @override_settings(DB_BACKUP_ENCRYPTION_KEY=KEY_B64, PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_s",
                       DATABASES={"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "app",
                                               "HOST": "db.internal", "PORT": "5432", "USER": "u", "PASSWORD": "p"}})
    def test_restore_refuses_the_running_database(self):
        store = FakeStore()
        service.run_backup(store, KEY, lambda: SECRET_DUMP, at(2026, 11, 22))
        name = next(iter(store.objects))
        with mock.patch("apps.db_backup.clients.build_store", return_value=store), \
                mock.patch("apps.db_backup.dump.restore_database") as restore:
            with self.assertRaisesMessage(CommandError, "the database this deployment runs on"):
                self.run_command("restore_database", name=name, target_url="postgresql://u:p@db.internal:5432/app")
        restore.assert_not_called()

    @override_settings(DB_BACKUP_ENCRYPTION_KEY=KEY_B64, PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_s")
    def test_restore_decrypts_and_restores_into_the_target(self):
        store = FakeStore()
        service.run_backup(store, KEY, lambda: SECRET_DUMP, at(2026, 11, 22))
        name = next(iter(store.objects))
        with mock.patch("apps.db_backup.clients.build_store", return_value=store), \
                mock.patch("apps.db_backup.dump.restore_database") as restore:
            text = self.run_command("restore_database", name=name,
                                    target_url="postgresql://u:pw@other.host:5433/scratch")
        data, conn = restore.call_args.args
        self.assertEqual(data, SECRET_DUMP)
        self.assertEqual((conn["host"], conn["port"], conn["name"]), ("other.host", "5433", "scratch"))
        self.assertNotIn("pw", text)

    @override_settings(DB_BACKUP_ENCRYPTION_KEY=KEY_B64, PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_s")
    def test_restore_list_prints_copies(self):
        store = FakeStore()
        service.run_backup(store, KEY, lambda: SECRET_DUMP, at(2026, 11, 22))
        with mock.patch("apps.db_backup.clients.build_store", return_value=store):
            self.assertIn("20261122T040000Z", self.run_command("restore_database", list=True))


class DumpTests(SimpleTestCase):
    def test_target_url_parts_and_validation(self):
        conn = dump.connection_from_url("postgresql://us%40er:p%2Fw@h.example:6543/db1?sslmode=require")
        self.assertEqual(conn, {"host": "h.example", "port": "6543", "user": "us@er", "password": "p/w",
                                "name": "db1", "sslmode": "require"})
        for bad in ("", "mysql://h/db", "postgresql://h/"):
            with self.assertRaises(dump.DumpError):
                dump.connection_from_url(bad)

    def test_password_goes_in_the_environment_not_the_arguments(self):
        conn = dump.connection_from_url("postgresql://u:topsecret@h/db")
        with mock.patch("apps.db_backup.dump.subprocess.run") as run:
            run.return_value.returncode = 1
            with self.assertRaises(dump.DumpError) as caught:
                dump.restore_database(b"x", conn)
        args, kwargs = run.call_args
        self.assertNotIn("topsecret", " ".join(args[0]))
        self.assertEqual(kwargs["env"]["PGPASSWORD"], "topsecret")
        self.assertNotIn("topsecret", str(caught.exception))

    @override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
    def test_sqlite_is_refused(self):
        with self.assertRaises(dump.DumpError):
            dump.connection_from_settings()


@override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_s")
class ReadyzTests(TestCase):
    def setUp(self):
        cache.clear()
        # A fresh beat heartbeat, as a running worker leaves; without Redis the check is skipped (eager mode).
        cache.set(HEARTBEAT_KEY, timezone.now().timestamp(), 600)
        self.store = FakeStore()
        patch = mock.patch("apps.db_backup.clients.build_store", return_value=self.store)
        patch.start()
        self.addCleanup(patch.stop)

    def get(self):
        return self.client.get("/readyz/", secure=True)

    def test_off_does_not_report_the_field_or_call_the_store(self):
        response = self.get()
        self.assertNotIn("db_backup", response.json())
        self.assertEqual(self.store.calls, [])

    @override_settings(DB_BACKUP_ENABLED=True)
    def test_on_with_no_copy_is_false_and_503(self):
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertIs(response.json()["db_backup"], False)

    @override_settings(DB_BACKUP_ENABLED=True)
    def test_fresh_from_cache_is_true(self):
        cache.set(constants.LAST_OK_KEY, (timezone.now() - timedelta(hours=29)).timestamp(), None)
        response = self.get()
        self.assertIs(response.json()["db_backup"], True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.calls, [])

    @override_settings(DB_BACKUP_ENABLED=True)
    def test_older_than_30_hours_is_false(self):
        cache.set(constants.LAST_OK_KEY, (timezone.now() - timedelta(hours=31)).timestamp(), None)
        response = self.get()
        self.assertIs(response.json()["db_backup"], False)
        self.assertEqual(response.status_code, 503)

    @override_settings(DB_BACKUP_ENABLED=True)
    def test_empty_cache_falls_back_to_the_newest_copy_in_the_store(self):
        stamp = timezone.now() - timedelta(hours=5)
        self.store.objects[service.pathname_for(stamp)] = b"x"
        self.assertIs(self.get().json()["db_backup"], True)
        self.assertIsNotNone(cache.get(constants.LAST_OK_KEY))

    @override_settings(DB_BACKUP_ENABLED=True)
    def test_stale_copy_in_the_store_is_false(self):
        self.store.objects[service.pathname_for(timezone.now() - timedelta(hours=40))] = b"x"
        self.assertIs(self.get().json()["db_backup"], False)

    @override_settings(DB_BACKUP_ENABLED=True)
    def test_store_down_is_false_and_not_asked_again_at_once(self):
        self.store.fail_list = True
        self.assertIs(self.get().json()["db_backup"], False)
        self.store.calls.clear()
        self.assertIs(self.get().json()["db_backup"], False)
        self.assertEqual(self.store.calls, [])

    @override_settings(DB_BACKUP_ENABLED=True)
    def test_other_fields_and_codes_are_unchanged(self):
        cache.set(constants.LAST_OK_KEY, timezone.now().timestamp(), None)
        body = self.get().json()
        for field in ("db", "cache", "beat", "sms_queue", "email", "files_backup", "meta_queue", "xendit_queue"):
            self.assertIn(field, body)


class ChecksTests(SimpleTestCase):
    @override_settings(DB_BACKUP_ENABLED=True, DB_BACKUP_ENCRYPTION_KEY="", PRIVATE_BLOB_READ_WRITE_TOKEN="")
    def test_warns_when_on_and_unconfigured(self):
        from apps.db_backup.checks import check_db_backup

        self.assertEqual({w.id for w in check_db_backup(None)}, {"db_backup.W001", "db_backup.W002"})

    @override_settings(DB_BACKUP_ENABLED=False, DB_BACKUP_ENCRYPTION_KEY="")
    def test_silent_when_off(self):
        from apps.db_backup.checks import check_db_backup

        self.assertEqual(check_db_backup(None), [])
