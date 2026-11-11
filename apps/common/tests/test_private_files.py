"""Private files: the type and size check, the keys, the local folder, and the calls made to
the private Vercel Blob store. No test here reaches the network: urlopen is replaced."""
import io
import json
import shutil
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.common import private_files as files

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"1" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"2" * 32
PDF = b"%PDF-1.7 " + b"3" * 32
SECRET = "S3cretPartOfTheToken"
TOKEN = f"vercel_blob_rw_StoreId123_{SECRET}"
KEY = "sante/qr/site-7/" + "ab" * 16 + ".png"


def upload(content=PNG, content_type="image/png"):
    return SimpleUploadedFile("anything.exe", content, content_type=content_type)


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CheckTests(SimpleTestCase):
    def check(self, file, allowed=files.IMAGE_TYPES, max_bytes=1024):
        return files.check(file, allowed=allowed, max_bytes=max_bytes)

    def refused(self, file, **kwargs):
        with self.assertRaises(files.FileRefused) as refused:
            self.check(file, **kwargs)
        return refused.exception.reason

    def test_sniff_reads_the_first_bytes(self):
        for content, expected in (
            (PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp"),
            (PDF, "application/pdf"), (b"GIF89a", ""), (b"RIFF\x00\x00\x00\x00WAVE", ""), (b"", ""),
        ):
            self.assertEqual(files.sniff(content[:16]), expected)

    def test_an_allowed_file_answers_its_content_and_the_type_its_bytes_show(self):
        self.assertEqual(self.check(upload()), (PNG, "image/png"))
        self.assertEqual(self.check(upload(WEBP, "image/webp")), (WEBP, "image/webp"))
        self.assertEqual(self.check(upload(JPEG, "image/png")), (JPEG, "image/jpeg"))
        self.assertEqual(self.check(upload(PNG, "IMAGE/PNG; charset=x")), (PNG, "image/png"))

    def test_the_declared_type_and_the_bytes_must_both_be_allowed(self):
        self.assertEqual(self.refused(upload(PNG, "text/plain")), files.WRONG_TYPE)
        self.assertEqual(self.refused(upload(PNG, "")), files.WRONG_TYPE)
        self.assertEqual(self.refused(upload(b"<svg>", "image/png")), files.WRONG_TYPE)
        self.assertEqual(self.refused(upload(PDF, "application/pdf")), files.WRONG_TYPE)
        self.assertEqual(self.refused(upload(b"", "image/png")), files.WRONG_TYPE)
        allowed = ("application/pdf",)
        self.assertEqual(self.check(upload(PDF, "application/pdf"), allowed=allowed)[1], "application/pdf")

    def test_what_is_not_a_file_is_refused(self):
        for value in (None, "text", object()):
            self.assertEqual(self.refused(value), files.WRONG_TYPE)

    def test_the_size_limit_is_inclusive_and_judged_before_the_type(self):
        self.assertEqual(self.check(upload(), max_bytes=len(PNG))[0], PNG)
        self.assertEqual(self.refused(upload(), max_bytes=len(PNG) - 1), files.TOO_LARGE)
        self.assertEqual(self.refused(upload(b"x" * 50, "text/plain"), max_bytes=10), files.TOO_LARGE)

    def test_a_file_that_understates_its_size_is_still_refused(self):
        lying = upload()
        lying.size = 1
        self.assertEqual(self.refused(lying, max_bytes=10), files.TOO_LARGE)


class KeyTests(SimpleTestCase):
    def test_a_new_key_is_the_folder_a_random_name_and_the_types_ending(self):
        first = files.new_key("sante/qr/site-7", "image/jpeg")
        self.assertRegex(first, r"^sante/qr/site-7/[0-9a-f]{32}\.jpg$")
        self.assertNotEqual(first, files.new_key("sante/qr/site-7", "image/jpeg"))

    def test_a_folder_that_could_leave_its_place_is_refused(self):
        for folder in ("", "/sante", "sante/", "sante/../x", "sante//qr", "Sante", "a b", "a?b"):
            with self.subTest(folder), self.assertRaises(ValueError):
                files.new_key(folder, "image/png")

    def test_a_store_takes_only_a_key_made_here(self):
        store = files.LocalStore("/nonexistent")
        blob = files.BlobStore(TOKEN)
        bad = ("", None, "../etc/passwd", "sante/qr/../../x.png", "/" + KEY, KEY + "?x=1",
               "sante/qr/site-7/short.png", KEY.replace(".png", ".exe"), "https://evil.example/" + KEY)
        for key in bad:
            for call in (store.get, store.delete, blob.get, blob.delete):
                with self.subTest(key=key, call=call), self.assertRaises(ValueError):
                    call(key)
            with self.assertRaises(ValueError):
                store.put(key, PNG, "image/png")
            with self.assertRaises(ValueError):
                blob.put(key, PNG, "image/png")


class LocalStoreTests(SimpleTestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="private-files-test-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def test_put_get_and_delete(self):
        with override_settings(
            PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=True, PRIVATE_FILES_DIR=str(self.directory)
        ):
            key = files.save("sante/qr/site-7", PNG, "image/png")
            self.assertEqual((self.directory / key).read_bytes(), PNG)
            self.assertEqual(files.read(key), PNG)
            files.delete(key)
            self.assertFalse((self.directory / key).exists())
            files.delete(key)  # already gone: nothing to do
            files.delete("")


class WhichStoreTests(SimpleTestCase):
    def test_the_token_selects_the_blob_store_even_while_debug_is_on(self):
        with override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN=TOKEN, DEBUG=True, PRIVATE_FILES_DIR="/x"):
            self.assertIsInstance(files.store(), files.BlobStore)
            self.assertTrue(files.available())

    def test_no_token_uses_the_local_folder_only_while_debug_is_on(self):
        with override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=True, PRIVATE_FILES_DIR="/x"):
            self.assertIsInstance(files.store(), files.LocalStore)
        for debug, directory in ((False, "/x"), (True, "")):
            with override_settings(
                PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=debug, PRIVATE_FILES_DIR=directory
            ):
                self.assertFalse(files.available())
                with self.assertRaises(files.StoreUnavailable):
                    files.store()
                with self.assertRaises(files.StoreUnavailable):
                    files.save("sante/qr/site-7", PNG, "image/png")
                with self.assertLogs(files.logger, "WARNING"):
                    files.discard(KEY)  # a clean-up never raises

    def test_a_value_that_is_not_a_read_write_token_is_no_store_and_is_not_repeated(self):
        for value in ("store_tTysBPnfIvbWsMW4", "vercel_blob_rw_", "vercel_blob_rw__secret", SECRET):
            with override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN=value):
                with self.assertRaises(files.StoreUnavailable) as refused:
                    files.store()
                self.assertNotIn(value, str(refused.exception))


@override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN=TOKEN)
class BlobStoreTests(SimpleTestCase):
    """The requests made to Vercel Blob, as its own client makes them."""

    def setUp(self):
        patcher = mock.patch.object(files.urllib.request, "urlopen")
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self):
        request = self.urlopen.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        return request, headers

    def test_put_sends_the_file_as_private_under_its_key(self):
        self.urlopen.return_value = Answer(json.dumps({"pathname": KEY, "url": "https://x/"}).encode())
        with mock.patch.object(files, "new_key", return_value=KEY):
            self.assertEqual(files.save("sante/qr/site-7", PNG, "image/png"), KEY)
        request, headers = self.sent()
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(
            request.full_url,
            "https://vercel.com/api/blob/?pathname=sante%2Fqr%2Fsite-7%2F" + "ab" * 16 + ".png",
        )
        self.assertEqual(request.data, PNG)
        self.assertEqual(headers["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["x-vercel-blob-access"], "private")
        self.assertEqual(headers["x-content-type"], "image/png")
        self.assertEqual(headers["x-add-random-suffix"], "0")
        self.assertEqual(headers["x-api-version"], "13")
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], files.TIMEOUT)
        self.assertNotIn(SECRET, request.full_url)

    def test_put_is_not_confirmed_unless_the_answer_names_the_key(self):
        for body in (b"{}", b"not json", b"[]", json.dumps({"pathname": KEY + "x"}).encode()):
            self.urlopen.return_value = Answer(body)
            with self.subTest(body=body), self.assertLogs(files.logger, "WARNING"):
                with self.assertRaises(files.StoreError):
                    files.store().put(KEY, PNG, "image/png")

    def test_get_reads_from_the_stores_private_host_with_the_token(self):
        self.urlopen.return_value = Answer(PNG)
        self.assertEqual(files.read(KEY), PNG)
        request, headers = self.sent()
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, f"https://storeid123.private.blob.vercel-storage.com/{KEY}")
        self.assertEqual(headers["authorization"], f"Bearer {TOKEN}")

    def test_delete_names_the_file_by_its_private_address(self):
        self.urlopen.return_value = Answer(b"{}")
        files.delete(KEY)
        request, headers = self.sent()
        self.assertEqual((request.get_method(), request.full_url), ("POST", "https://vercel.com/api/blob/delete"))
        self.assertEqual(
            json.loads(request.data),
            {"urls": [f"https://storeid123.private.blob.vercel-storage.com/{KEY}"]},
        )
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["authorization"], f"Bearer {TOKEN}")

    def test_a_failure_is_a_store_error_that_never_holds_the_token(self):
        refusal = urllib.error.HTTPError("https://vercel.com/api/blob/", 403, "Forbidden", {}, io.BytesIO(TOKEN.encode()))
        for failure in (refusal, urllib.error.URLError("unreachable"), TimeoutError(), ConnectionResetError()):
            self.urlopen.side_effect = failure
            for call in (
                lambda: files.save("sante/qr/site-7", PNG, "image/png"),
                lambda: files.read(KEY),
                lambda: files.delete(KEY),
            ):
                with self.subTest(failure=failure), self.assertLogs(files.logger, "WARNING") as logs:
                    with self.assertRaises(files.StoreError) as raised:
                        call()
                    error = raised.exception
                    self.assertIsNone(error.__cause__)
                    self.assertTrue(error.__suppress_context__ or error.__context__ is None)
                    for text in (str(error), repr(error), *logs.output):
                        self.assertNotIn(SECRET, text)
                        self.assertNotIn(KEY, text)

    def test_discard_never_raises_and_logs_neither_the_key_nor_the_token(self):
        self.urlopen.side_effect = urllib.error.URLError("unreachable")
        with self.assertLogs(files.logger, "WARNING") as logs:
            files.discard(KEY)
        for line in logs.output:
            self.assertNotIn(SECRET, line)
            self.assertNotIn(KEY, line)
        self.urlopen.reset_mock()
        files.discard("")
        self.urlopen.assert_not_called()
