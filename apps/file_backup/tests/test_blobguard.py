"""P7-BLOBGUARD: copy_blob_store refuses a store-kind mismatch. Fakes only, no network."""
import io
import os
import urllib.error
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.file_backup import blobcopy
from apps.file_backup.source import BlobSource, SourceError
from apps.file_backup.tests.test_blobcopy import (
    CMD, DST_TOKEN, PATH_A, PATH_B, PATH_C, SRC_TOKEN, FakeStore, run_command, seeded,
)


def probe_paths(store, kind):
    return [p for k, p in store.probe_log if k == kind]


class NameRuleTests(SimpleTestCase):
    def _run(self, access, source_name, dest_name):
        out, err = io.StringIO(), io.StringIO()
        env = {source_name: SRC_TOKEN, dest_name: DST_TOKEN}
        with mock.patch.dict(os.environ, env), mock.patch(f"{CMD}.build_store") as build:
            try:
                call_command("copy_blob_store", "--access", access, "--source-token-env", source_name,
                             "--dest-token-env", dest_name, "--apply", stdout=out, stderr=err)
                error = None
            except CommandError as caught:
                error = caught
        return build, error

    def test_a_name_without_the_access_word_is_refused_before_any_store_is_built(self):
        for access, good, bad in (("private", "X_PRIVATE_TOK", "X_PUBLIC_TOK"),
                                  ("public", "X_PUBLIC_TOK", "X_PRIVATE_TOK")):
            for names in ((good, bad), (bad, good), ("PLAIN_SRC", "PLAIN_DST")):
                build, error = self._run(access, *names)
                self.assertIsNotNone(error)
                self.assertIn(f"Not done: --access {access} needs both token variable names to "
                              f"contain {access.upper()}.", str(error))
                build.assert_not_called()

    def test_the_word_is_matched_in_upper_case_on_the_name_only(self):
        build, error = self._run("private", "src_private_tok", "DST_private")
        self.assertIn("needs both token variable names", str(error))
        build.assert_not_called()
        self.assertNotIn("SECRET", str(error))

    def test_matching_names_pass_the_rule(self):
        source, dest = seeded("public")
        _, _, error = run_command(source, dest, "--apply")
        self.assertIsNone(error)


class KindProbeTests(SimpleTestCase):
    def test_a_public_destination_under_access_private_is_refused_before_any_copy(self):
        source, dest = seeded("private")
        dest.access = "public"  # really a public store, declared private
        for extra in ((), ("--apply",)):
            out, _, error = run_command(source, dest, *extra)
            self.assertIn("Not done: the destination store is not a private store.", str(error))
            self.assertEqual(dest.puts, [])
            self.assertEqual(dest.deletes, [])
            self.assertNotIn("copied", out)
        self.assertEqual(dest.objects, {})  # the probe is gone too

    def test_a_private_destination_that_the_public_host_serves_is_refused(self):
        source, dest = seeded("private")
        dest.served_publicly = lambda pathname: True
        _, _, error = run_command(source, dest, "--apply")
        self.assertIn("Not done: the destination store is not a private store.", str(error))
        self.assertEqual(dest.puts, [])
        self.assertEqual(dest.objects, {})

    def test_a_private_source_under_access_public_is_refused(self):
        source, dest = seeded("private")  # really private ...
        dest.access = "public"  # ... with a public destination, declared public
        _, _, error = run_command(source, dest, "--apply", access="public")
        self.assertIn("Not done: the source store is not a public store.", str(error))
        self.assertEqual(dest.puts, [])
        self.assertEqual(dest.probe_log, [])  # refused before the destination was touched

    def test_a_public_destination_must_be_served_by_the_public_host(self):
        source, dest = seeded("public")
        dest.served_publicly = lambda pathname: False
        _, _, error = run_command(source, dest, "--apply")
        self.assertIn("Not done: the destination store is not a public store.", str(error))
        self.assertEqual(dest.puts, [])

    def test_the_probe_is_deleted_on_success_in_a_dry_run_and_with_apply(self):
        for access in ("private", "public"):
            for extra in ((), ("--apply",)):
                source, dest = seeded(access)
                out, _, error = run_command(source, dest, *extra)
                self.assertIsNone(error)
                put, deleted = probe_paths(dest, "put"), probe_paths(dest, "delete")
                self.assertEqual(len(put), 1)
                self.assertEqual(put, deleted)
                self.assertRegex(put[0], r"^ops/blobguard-probe/[0-9a-f]{32}$")
                self.assertTrue(probe_paths(dest, "fetch"))
                self.assertTrue(probe_paths(dest, "public"))
                self.assertIn("probe deleted: yes", out)
                self.assertFalse([p for p in dest.objects if p.startswith(blobcopy.PROBE_PREFIX)])

    def test_the_probe_is_deleted_on_failure_too(self):
        source, dest = seeded("private")
        dest.access = "public"
        run_command(source, dest)
        put = probe_paths(dest, "put")
        self.assertEqual(len(put), 1)
        self.assertEqual(put, probe_paths(dest, "delete"))
        self.assertEqual(dest.objects, {})

    def test_a_probe_put_that_fails_is_refused_and_the_delete_is_still_tried(self):
        source, dest = seeded("private")
        dest.fail_probe_put = True
        _, _, error = run_command(source, dest, "--apply")
        self.assertIn("Not done: the destination store did not take the probe.", str(error))
        self.assertEqual(len(probe_paths(dest, "delete")), 1)
        self.assertEqual(dest.puts, [])

    def test_a_failed_probe_delete_is_reported_as_a_flag(self):
        source, dest = seeded("private")
        dest.fail_probe_delete = True
        out, _, error = run_command(source, dest)
        self.assertIsNone(error)
        self.assertIn("probe deleted: no", out)
        dest.access = "public"
        _, _, error = run_command(source, dest)
        self.assertIn("probe deleted: no", str(error))

    def test_an_empty_source_is_reported_and_exits_clean_without_touching_the_destination(self):
        source, dest = FakeStore("private", "src"), FakeStore("private", "dst")
        out, _, error = run_command(source, dest, "--apply")
        self.assertIsNone(error)
        self.assertIn("nothing to copy", out)
        self.assertEqual(dest.probe_log, [])
        self.assertEqual(dest.puts, [])

    def test_the_prefix_applies_to_the_source_probe(self):
        source, dest = seeded("private")
        out, _, error = run_command(source, dest, "--prefix", "nothing-here/")
        self.assertIsNone(error)
        self.assertIn("nothing to copy", out)


class FailFastTests(SimpleTestCase):
    def test_the_run_stops_at_the_first_put_failure(self):
        source, dest = seeded("private")
        real_put = dest.put

        def refusing(pathname, content, content_type):
            if pathname == PATH_C:  # the first object (listing order: C, A, B)
                raise SourceError("The private store put failed (403).")
            return real_put(pathname, content, content_type)

        with mock.patch.object(dest, "put", side_effect=refusing):
            out, _, error = run_command(source, dest, "--apply")
        self.assertIsNotNone(error)
        self.assertIn("Not done:", str(error))
        self.assertIn("copied: 0", out)
        self.assertIn("failed: 1", out)
        self.assertIn("stopped early: yes", out)
        self.assertEqual(set(dest.objects) & {PATH_A, PATH_B, PATH_C}, set())  # A, B never tried

    def test_a_failed_read_back_also_stops_the_run(self):
        source, dest = seeded("private")
        real_fetch = dest.fetch

        def flaky(pathname):
            if pathname == PATH_C and PATH_C in dest.objects:
                raise SourceError("The private store get failed (500).")
            return real_fetch(pathname)

        with mock.patch.object(dest, "fetch", side_effect=flaky):
            out, _, error = run_command(source, dest, "--apply")
        self.assertIsNotNone(error)
        self.assertIn("copied: 0", out)
        self.assertIn("stopped early: yes", out)
        self.assertEqual([p for p, _, _ in dest.puts], [PATH_C])

    def test_a_source_fetch_failure_does_not_stop_the_run(self):
        source, dest = seeded("private")
        source.fail_fetch.add(PATH_A)  # the middle object: the run goes on to B
        out, _, error = run_command(source, dest, "--apply")
        self.assertIn("copied: 2", out)
        self.assertIn("failed: 1", out)
        self.assertNotIn("stopped early: yes", out)
        self.assertIsNotNone(error)


class HappyPathTests(SimpleTestCase):
    def test_happy_path_is_unchanged_and_a_second_run_copies_nothing(self):
        for access in ("private", "public"):
            source, dest = seeded(access)
            out, _, error = run_command(source, dest, "--apply")
            self.assertIsNone(error)
            self.assertIn("copied: 3", out)
            self.assertIn("failed: 0", out)
            self.assertNotIn("stopped early: yes", out)
            self.assertEqual(dest.objects, source.objects)
            out, _, error = run_command(source, dest, "--apply")
            self.assertIsNone(error)
            self.assertIn("copied: 0", out)
            self.assertIn("skipped_existing: 3", out)
            self.assertEqual(len(dest.puts), 3)

    def test_no_token_value_or_variable_name_in_the_output_of_any_path(self):
        seen = ""
        runs = []
        for access, extra in (("private", ()), ("private", ("--apply",)), ("public", ("--apply",))):
            runs.append((seeded(access), extra))
        mismatch = seeded("private")
        mismatch[1].access = "public"
        runs.append((mismatch, ("--apply",)))
        for (source, dest), extra in runs:
            out, err, error = run_command(source, dest, *extra)
            seen += out + err + str(error or "")
        for secret in (SRC_TOKEN, DST_TOKEN, "SECRETSOURCEVALUE", "SECRETDESTVALUE", "srcstore01",
                       "dststore02", blobcopy.PROBE_PREFIX, PATH_A):
            self.assertNotIn(secret, seen)


class RealClientProbeTests(SimpleTestCase):
    def _served(self, status):
        sent = {}

        def fake(request, timeout=None):
            sent["url"] = request.full_url
            sent["auth"] = request.get_header("Authorization")
            if status == 200:
                return mock.MagicMock()
            raise urllib.error.HTTPError(request.full_url, status, "x", {}, None)

        with mock.patch("urllib.request.urlopen", fake):
            result = BlobSource("vercel_blob_rw_Abc123_SECRET", "private").served_publicly("ops/x")
        return result, sent

    def test_public_host_check_is_unauthenticated_and_reads_the_status(self):
        result, sent = self._served(200)
        self.assertTrue(result)
        self.assertEqual(sent["url"], "https://abc123.public.blob.vercel-storage.com/ops/x")
        self.assertIsNone(sent["auth"])
        for status in (403, 404):
            self.assertFalse(self._served(status)[0])

    def test_a_server_error_is_not_taken_as_not_served(self):
        with self.assertRaises(SourceError):
            self._served(500)
