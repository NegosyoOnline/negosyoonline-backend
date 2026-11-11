"""Private files: uploads that are personal data and are never public (a payment QR code today;
payment proofs and registration evidence later).

A stored file has a key and no URL. Nothing here builds a link for a browser: a caller that
must show a file reads its bytes with `read()` after its own check of who is asking, and
answers them itself.

Where the bytes go, decided by `store()`:
- PRIVATE_BLOB_READ_WRITE_TOKEN set: the private Vercel Blob store, over its HTTP API. The
  token is sent as a request header and appears in no key, URL, log line or error.
- No token and DEBUG on: the local folder PRIVATE_FILES_DIR, which Git ignores.
- No token and DEBUG off: no store. A shared environment never writes a private file to a
  disk that is lost on the next deploy.

The Blob calls follow Vercel's own client (@vercel/blob, API version 13, checked 2026-11-11):
PUT {api}/?pathname=<key> with `x-vercel-blob-access: private`; a private file is read from
https://<store id>.private.blob.vercel-storage.com/<key>; POST {api}/delete removes it.
"""
import json
import logging
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
WRONG_TYPE, TOO_LARGE = "type", "size"
# <folder>/<32 hex><ext>. A key is made here, never taken from a request.
_KEY = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)*/[0-9a-f]{32}\.(jpg|png|webp|pdf)$")
_FOLDER = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)*$")

BLOB_API = "https://vercel.com/api/blob"
BLOB_API_VERSION = "13"
BLOB_HOST = "{store_id}.private.blob.vercel-storage.com"
TIMEOUT = 15
USER_AGENT = "negosyoonline-backend"


class FileRefused(Exception):
    """The upload is not an allowed file. `reason` is WRONG_TYPE or TOO_LARGE."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class StoreError(Exception):
    """The store could not do what was asked. The message never holds the token."""


class StoreUnavailable(StoreError):
    """No store may be used here."""


def sniff(head):
    """The file's type by its first bytes, or "". The file name is not used."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return ""


def check(upload, *, allowed, max_bytes):
    """(content, content type) of an uploaded file, or FileRefused. The size is judged before
    the file is read; the type must be allowed both as the browser declared it and by the
    file's first bytes, and the stored type is the one the bytes show."""
    size = getattr(upload, "size", None)
    if not hasattr(upload, "read") or size is None:
        raise FileRefused(WRONG_TYPE)
    if size > max_bytes:
        raise FileRefused(TOO_LARGE)
    upload.seek(0)
    content = upload.read(max_bytes + 1)
    upload.seek(0)
    if len(content) > max_bytes:
        raise FileRefused(TOO_LARGE)
    declared = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    content_type = sniff(content[:16])
    if declared not in allowed or content_type not in allowed:
        raise FileRefused(WRONG_TYPE)
    return content, content_type


def new_key(folder, content_type):
    """A key under `folder` with a random name. It holds nothing a person typed."""
    if not _FOLDER.fullmatch(folder):
        raise ValueError("Not a private file folder.")
    return f"{folder}/{secrets.token_hex(16)}{EXT[content_type]}"


def _checked(key):
    if not _KEY.fullmatch(key or ""):
        raise ValueError("Not a private file key.")
    return key


class LocalStore:
    """Files under one local folder. For a developer's machine only."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, key):
        return self.directory / _checked(key)

    def put(self, key, content, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key):
        return self._path(key).read_bytes()

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)


class BlobStore:
    """The private Vercel Blob store named by its read-write token."""

    def __init__(self, token):
        # vercel_blob_rw_<store id>_<secret>: the store id is the fourth part.
        parts = token.split("_")
        if len(parts) < 5 or not parts[3].isalnum():
            raise StoreUnavailable("The private store token is not a read-write token.")
        self._token = token
        self._host = BLOB_HOST.format(store_id=parts[3].lower())

    def _url(self, key):
        return f"https://{self._host}/{urllib.parse.quote(_checked(key))}"

    def _send(self, action, url, *, method, data=None, headers=None):
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("authorization", f"Bearer {self._token}")
        request.add_header("user-agent", USER_AGENT)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            status = error.code
        except (urllib.error.URLError, TimeoutError, OSError):
            status = "no answer"
        # Raised outside the except block, so the failed request (and the header that carries
        # the token) is not chained to this error.
        logger.warning("Private store %s failed: %s", action, status)
        raise StoreError(f"The private store {action} failed ({status}).")

    def put(self, key, content, content_type):
        query = urllib.parse.urlencode({"pathname": _checked(key)})
        body = self._send(
            "put", f"{BLOB_API}/?{query}", method="PUT", data=content,
            headers={
                "x-api-version": BLOB_API_VERSION,
                "x-vercel-blob-access": "private",
                "x-content-type": content_type,
                "x-add-random-suffix": "0",
            },
        )
        try:
            stored = json.loads(body).get("pathname")
        except (ValueError, AttributeError):
            stored = None
        if stored != key:
            logger.warning("Private store put answered without the key it was given.")
            raise StoreError("The private store put was not confirmed.")

    def get(self, key):
        return self._send("get", self._url(key), method="GET")

    def delete(self, key):
        self._send(
            "delete", f"{BLOB_API}/delete", method="POST",
            data=json.dumps({"urls": [self._url(key)]}).encode(),
            headers={"x-api-version": BLOB_API_VERSION, "content-type": "application/json"},
        )


def available():
    """Whether a store may be used here."""
    return bool(
        settings.PRIVATE_BLOB_READ_WRITE_TOKEN or (settings.DEBUG and settings.PRIVATE_FILES_DIR)
    )


def store():
    if settings.PRIVATE_BLOB_READ_WRITE_TOKEN:
        return BlobStore(settings.PRIVATE_BLOB_READ_WRITE_TOKEN)
    if settings.DEBUG and settings.PRIVATE_FILES_DIR:
        return LocalStore(settings.PRIVATE_FILES_DIR)
    raise StoreUnavailable("No private file store is available.")


def save(folder, content, content_type):
    """Store checked bytes under `folder`. Returns the key, or raises StoreError. The caller
    removes the file with discard() if its own save then fails."""
    key = new_key(folder, content_type)
    store().put(key, content, content_type)
    return key


def read(key):
    return store().get(key)


def delete(key):
    """Remove one file, or raise StoreError. Nothing to do for an empty key."""
    if key:
        store().delete(key)


def discard(key):
    """Remove one file as a clean-up step. Never raises and never logs the key."""
    if not key:
        return
    try:
        store().delete(key)
    except Exception:  # noqa: BLE001 - a failed clean-up never hides the error that led to it
        logger.warning("A private file clean-up failed.")
