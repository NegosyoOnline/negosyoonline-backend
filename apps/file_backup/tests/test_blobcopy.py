"""The Blob store copy (P7). Fakes only: no network and no real Blob store."""
import hashlib
import io
import json
import os
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.file_backup import blobcopy
from apps.file_backup.source import BlobSource, SourceError

CMD = "apps.file_backup.management.commands.copy_blob_store"
SRC_TOKEN = "vercel_blob_rw_srcstore01_SECRETSOURCEVALUE"
DST_TOKEN = "vercel_blob_rw_dststore02_SECRETDESTVALUE"
PATH_A = "sante/proofs/site-1/" + "a" * 32 + ".png"
PATH_B = "sante/proofs/site-1/" + "b" * 32 + ".pdf"
PATH_C = "media-library/site-2/" + "c" * 32 + ".webp"


class FakeStore:
    """An in-memory Blob store of one access. Like Vercel with no allow-overwrite, a put onto an
    existing name is refused."""

    def __init__(self, access, store_id="fake01"):
        self.access, self.store_id = access, store_id
        self.objects = {}  # pathname -> (bytes, content type)
        self.puts, self.deletes, self.fetches = [], [], []
        self.fail_fetch, self.corrupt_put = set(), set()

    def add(self, pathname, content, content_type="image/png"):
        self.objects[pathname] = (content, content_type)

    def list_objects(self, prefix=""):
        for pathname in sorted(self.objects):
            if pathname.startswith(prefix):
                yield {"pathname": pathname, "size": len(self.objects[pathname][0]),
                       "uploaded_at": "t", "etag": "e"}

    def fetch(self, pathname):
        self.fetches.append(pathname)
        if pathname in self.fail_fetch or pathname not in self.objects:
            raise SourceError("The private store get failed (404).")
        return self.objects[pathname]

    def put(self, pathname, content, content_type):
        if pathname in self.objects:
            raise SourceError("The private store put failed (400).")
        self.puts.append((pathname, content_type, self.access))
        if pathname in self.corrupt_put:
            content = content + b"!"
        self.objects[pathname] = (content, content_type)

    def delete(self, pathname):
        self.deletes.append(pathname)
        self.objects.pop(pathname, None)


def seeded(access="private"):
    source, dest = FakeStore(access, "src"), FakeStore(access, "dst")
    source.add(PATH_A, b"\x89PNG\r\n\x1a\n" + b"a" * 100, "image/png")
    source.add(PATH_B, b"%PDF-1.4 " + b"b" * 300, "application/pdf")
    source.add(PATH_C, b"RIFFxxxxWEBP" + b"c" * 50, "image/webp")
    return source, dest


def run_command(source, dest, *args, env=None):
    """Run the command with the two fake stores in place of the real ones. Returns (stdout,
    stderr, error or None)."""
    stores = {SRC_TOKEN: source, DST_TOKEN: dest}
    out, err = io.StringIO(), io.StringIO()
    environ = {"SRC_TOK": SRC_TOKEN, "DST_TOK": DST_TOKEN}
    environ.update(env or {})
    base = ["--access", source.access, "--source-token-env", "SRC_TOK", "--dest-token-env", "DST_TOK",
            "--rate", "100000"]
    error = None
    with mock.patch.dict(os.environ, environ), \
            mock.patch(f"{CMD}.build_store", side_effect=lambda token, access: stores[token]):
        try:
            call_command("copy_blob_store", *base, *args, stdout=out, stderr=err)
        except CommandError as caught:
            error = caught
    return out.getvalue(), err.getvalue(), error


class CopyTests(SimpleTestCase):
    def test_dry_run_counts_and_changes_nothing(self):
        source, dest = seeded()
        dest.add(PATH_A, source.objects[PATH_A][0])
        out, _, error = run_command(source, dest)
        self.assertIsNone(error)
        self.assertIn("Dry run, nothing copied", out)
        self.assertIn("listed: 3", out)
        self.assertIn("already at the destination: 1", out)
        self.assertIn("would copy: 2", out)
        self.assertEqual(dest.puts, [])
        self.assertEqual(source.fetches, [])
        self.assertEqual(dest.fetches, [])
        self.assertEqual(len(dest.objects), 1)

    def test_apply_copies_bytes_and_content_types(self):
        source, dest = seeded()
        out, _, error = run_command(source, dest, "--apply")
        self.assertIsNone(error)
        self.assertEqual(dest.objects, source.objects)
        for line in ("listed: 3", "copied: 3", "skipped_existing: 0", "conflict: 0", "failed: 0"):
            self.assertIn(line, out)
        total = sum(len(c) for c, _ in source.objects.values())
        self.assertIn(f"bytes: {total}", out)

    def test_rerun_copies_nothing(self):
        source, dest = seeded()
        run_command(source, dest, "--apply")
        puts = len(dest.puts)
        out, _, error = run_command(source, dest, "--apply")
        self.assertIsNone(error)
        self.assertIn("copied: 0", out)
        self.assertIn("skipped_existing: 3", out)
        self.assertIn("bytes: 0", out)
        self.assertEqual(len(dest.puts), puts)
        self.assertEqual(dest.deletes, [])

    def test_existing_different_object_is_a_conflict_and_not_overwritten(self):
        source, dest = seeded()
        same_size = b"X" * len(source.objects[PATH_A][0])
        dest.add(PATH_A, same_size)  # same size, different bytes
        dest.add(PATH_B, b"short")  # different size
        out, _, error = run_command(source, dest, "--apply")
        self.assertIn("conflict: 2", out)
        self.assertIn("copied: 1", out)
        self.assertIsNotNone(error)  # non-zero exit
        self.assertEqual(dest.objects[PATH_A][0], same_size)
        self.assertEqual(dest.objects[PATH_B][0], b"short")
        self.assertEqual([p[0] for p in dest.puts], [PATH_C])
        self.assertEqual(dest.deletes, [])

    def test_dry_run_shows_a_size_conflict(self):
        source, dest = seeded()
        dest.add(PATH_B, b"short")
        out, _, error = run_command(source, dest)
        self.assertIn("conflict (existing, size differs): 1", out)
        self.assertIsNotNone(error)

    def test_sha_mismatch_after_upload_is_failed_and_the_bad_copy_removed(self):
        source, dest = seeded()
        dest.corrupt_put.add(PATH_B)
        out, _, error = run_command(source, dest, "--apply")
        self.assertIn("failed: 1", out)
        self.assertIn("copied: 2", out)
        self.assertIsNotNone(error)
        self.assertIn("Not done:", str(error))
        self.assertNotIn(PATH_B, dest.objects)  # removed, so a re-run can try again
        self.assertEqual(dest.deletes, [PATH_B])
        dest.corrupt_put.clear()
        out, _, error = run_command(source, dest, "--apply")
        self.assertIsNone(error)
        self.assertIn("copied: 1", out)

    def test_failed_read_back_keeps_the_object_and_a_rerun_accepts_it(self):
        source, dest = seeded()
        real_fetch = dest.fetch

        def flaky(pathname):
            if pathname == PATH_A and PATH_A in dest.objects:
                raise SourceError("The private store get failed (500).")
            return real_fetch(pathname)

        with mock.patch.object(dest, "fetch", side_effect=flaky):
            out, _, error = run_command(source, dest, "--apply")
        self.assertIn("failed: 1", out)
        self.assertIsNotNone(error)
        self.assertEqual(dest.deletes, [])
        out, _, error = run_command(source, dest, "--apply")
        self.assertIsNone(error)
        self.assertIn("skipped_existing: 3", out)

    def test_source_fetch_failure_counts_failed_and_goes_on(self):
        source, dest = seeded()
        source.fail_fetch.add(PATH_A)
        out, _, error = run_command(source, dest, "--apply")
        self.assertIn("failed: 1", out)
        self.assertIn("copied: 2", out)
        self.assertIsNotNone(error)

    def test_a_put_the_store_refuses_counts_failed_and_never_overwrites(self):
        source, dest = seeded()
        real_list = dest.list_objects
        # The name appears after the destination was listed (a race): the store refuses the put.
        dest.list_objects = lambda prefix="": iter([])
        dest.add(PATH_A, b"someone else")
        out, _, error = run_command(source, dest, "--apply")
        del real_list
        self.assertIn("failed: 1", out)
        self.assertEqual(dest.objects[PATH_A][0], b"someone else")
        self.assertIsNotNone(error)

    def test_prefix_limits_the_copy(self):
        source, dest = seeded()
        out, _, error = run_command(source, dest, "--apply", "--prefix", "media-library/")
        self.assertIsNone(error)
        self.assertEqual(list(dest.objects), [PATH_C])
        self.assertIn("listed: 1", out)

    def test_oversize_is_failed_unless_max_mb_raises_the_limit(self):
        source, dest = seeded()
        source.add("big/" + "d" * 32 + ".pdf", b"d" * (1024 * 1024 + 1), "application/pdf")
        out, _, error = run_command(source, dest, "--apply", "--max-mb", "1")
        self.assertIn("failed: 1", out)
        self.assertIn("oversize (counted in failed): 1", out)
        self.assertIsNotNone(error)
        self.assertEqual(len(dest.objects), 3)
        out, _, error = run_command(source, dest, "--apply", "--max-mb", "2")
        self.assertIsNone(error)
        self.assertEqual(len(dest.objects), 4)

    def test_default_limit_is_50_mb(self):
        self.assertEqual(blobcopy.DEFAULT_MAX_BYTES, 50 * 1024 * 1024)
        source, dest = seeded()
        counts = blobcopy.copy_store(source, dest, apply=True, rate=0)
        self.assertEqual(counts["copied"], 3)


class AccessTests(SimpleTestCase):
    def test_private_and_public_access_are_kept_by_the_real_client(self):
        for access in ("private", "public"):
            sent = {}

            def fake_urlopen(request, timeout=None):
                sent["headers"] = {k.lower(): v for k, v in request.header_items()}
                sent["url"] = request.full_url
                response = mock.MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps({"pathname": PATH_A}).encode()
                return response

            with mock.patch("urllib.request.urlopen", fake_urlopen):
                BlobSource("vercel_blob_rw_abc123_SECRET", access).put(PATH_A, b"x", "image/png")
            self.assertEqual(sent["headers"]["x-vercel-blob-access"], access)
            self.assertEqual(sent["headers"]["x-add-random-suffix"], "0")
            self.assertEqual(sent["headers"]["x-content-type"], "image/png")
            self.assertNotIn("x-allow-overwrite", sent["headers"])
            self.assertIn("pathname=" + PATH_A.replace("/", "%2F"), sent["url"])

    def test_reads_use_the_store_kind_host(self):
        private = BlobSource("vercel_blob_rw_Abc123_SECRET", "private")
        public = BlobSource("vercel_blob_rw_Abc123_SECRET", "public")
        self.assertEqual(private._url("a/b.png"), "https://abc123.private.blob.vercel-storage.com/a/b.png")
        self.assertEqual(public._url("a/b.png"), "https://abc123.public.blob.vercel-storage.com/a/b.png")

    def test_default_access_stays_private_for_the_backup(self):
        self.assertEqual(BlobSource("vercel_blob_rw_abc123_S")._access, "private")

    def test_copy_puts_with_the_stores_access(self):
        for access in ("private", "public"):
            source, dest = seeded(access)
            run_command(source, dest, "--apply")
            self.assertEqual({a for _, _, a in dest.puts}, {access})
            self.assertEqual(
                {p: t for p, t, _ in dest.puts},
                {p: source.objects[p][1] for p in source.objects},
            )

    def test_bad_token_is_refused_without_echoing_it(self):
        with self.assertRaises(SourceError) as caught:
            BlobSource("not-a-token-SECRETVALUE", "public")
        self.assertNotIn("SECRETVALUE", str(caught.exception))


class SafetyTests(SimpleTestCase):
    def test_no_token_or_pathname_in_stdout_stderr_or_logs(self):
        source, dest = seeded()
        dest.corrupt_put.add(PATH_B)
        source.fail_fetch.add(PATH_C)
        dest.add(PATH_A, b"different")
        with self.assertLogs(level="DEBUG") as logs:
            import logging
            logging.getLogger("apps.file_backup").warning("sentinel")
            outputs = [run_command(source, dest, "--apply"), run_command(source, dest)]
        seen = "\n".join(logs.output)
        for out, err, error in outputs:
            seen += "\n".join([out, err, str(error or "")])
        for secret in (SRC_TOKEN, DST_TOKEN, "SECRETSOURCEVALUE", "SECRETDESTVALUE",
                       PATH_A, PATH_B, PATH_C, "sante/proofs", "media-library", "site-1"):
            self.assertNotIn(secret, seen)

    def test_real_client_failure_logs_no_token_or_pathname(self):
        import urllib.error

        def boom(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "no", {}, None)

        client = BlobSource(SRC_TOKEN, "private")
        with mock.patch("urllib.request.urlopen", boom), \
                mock.patch("apps.file_backup.source.time.sleep"), \
                self.assertLogs("apps.file_backup.source", level="WARNING") as logs:
            with self.assertRaises(SourceError) as caught:
                client.fetch(PATH_A)
        seen = "\n".join(logs.output) + str(caught.exception)
        for secret in ("SECRETSOURCEVALUE", PATH_A, "srcstore01"):
            self.assertNotIn(secret, seen)

    def test_tokens_only_from_the_named_variables(self):
        source, dest = seeded()
        _, _, error = run_command(source, dest, env={"DST_TOK": ""})
        self.assertIn("Not done:", str(error))
        self.assertIn("DST_TOK", str(error))
        self.assertNotIn("SECRET", str(error))
        with self.assertRaises(CommandError):
            call_command("copy_blob_store", "--access", "private", "--source-token", SRC_TOKEN,
                         "--dest-token-env", "DST_TOK")

    def test_same_variable_or_same_store_is_refused(self):
        source, dest = seeded()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"SRC_TOK": SRC_TOKEN}), \
                self.assertRaises(CommandError) as caught:
            call_command("copy_blob_store", "--access", "private", "--source-token-env", "SRC_TOK",
                         "--dest-token-env", "SRC_TOK", stdout=out)
        self.assertIn("Not done:", str(caught.exception))
        dest.store_id = source.store_id
        _, _, error = run_command(source, dest, "--apply")
        self.assertIn("same store", str(error))
        self.assertEqual(dest.puts, [])

    def test_a_listing_error_is_a_not_done_error(self):
        source, dest = seeded()

        def broken(prefix=""):
            raise SourceError("The private store list failed (403).")

        source.list_objects = broken
        _, _, error = run_command(source, dest, "--apply")
        self.assertIn("Not done: The private store list failed (403).", str(error))

    def test_requests_are_throttled(self):
        source, dest = seeded()
        now = [0.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        blobcopy.copy_store(source, dest, apply=True, rate=10, clock=lambda: now[0], sleep=sleep)
        self.assertTrue(sleeps)
        self.assertTrue(all(abs(s - 0.1) < 1e-9 for s in sleeps))
        # 3 objects x (fetch, put, read-back) = 9 requests, 8 gaps of 0.1 s.
        self.assertAlmostEqual(sum(sleeps), 0.8)

    def test_sha_is_of_the_bytes(self):
        self.assertEqual(blobcopy._sha(b"x"), hashlib.sha256(b"x").hexdigest())
