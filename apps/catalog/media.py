"""Product photos: the public Vercel Blob media store.

A product photo is public by design (it is shown on every Santé storefront), so it lives in the
public store, not the private one (apps/common/private_files.py). Two settings, both read in
config/settings.py and both optional:

- PUBLIC_BLOB_READ_WRITE_TOKEN: the read-write token of the public store. Sent as a request
  header only; it appears in no key, URL, log line or error.
- PUBLIC_MEDIA_BASE_URL: the public host of that store, with no trailing slash. It is not a
  secret. The public URL of a photo is this plus its key.

With either missing, nothing can be stored: `configured()` is False and the upload route
answers a clear "not configured" error. Reading and showing products never needs the store.

The Blob calls follow the private store's (same client, API version 13), with
`x-vercel-blob-access: public`.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from apps.common import private_files

logger = logging.getLogger(__name__)

FOLDER = "catalog/products"
MAX_BYTES = 2 * 1024 * 1024
BLOB_API = "https://vercel.com/api/blob"
BLOB_API_VERSION = "13"
TIMEOUT = 15
USER_AGENT = "negosyoonline-backend"


class StoreError(Exception):
    """The store could not do what was asked. The message never holds the token."""


def configured():
    return bool(settings.PUBLIC_BLOB_READ_WRITE_TOKEN and settings.PUBLIC_MEDIA_BASE_URL)


def url_for(key):
    """The public URL of a stored photo, or None for no photo or no media host set."""
    if not key or not settings.PUBLIC_MEDIA_BASE_URL:
        return None
    return f"{settings.PUBLIC_MEDIA_BASE_URL.rstrip('/')}/{urllib.parse.quote(key)}"


def _send(action, url, *, method, data=None, headers=None):
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("authorization", f"Bearer {settings.PUBLIC_BLOB_READ_WRITE_TOKEN}")
    request.add_header("user-agent", USER_AGENT)
    request.add_header("x-api-version", BLOB_API_VERSION)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        status = error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        status = "no answer"
    # Raised outside the except block so the request (and its token header) is not chained.
    logger.warning("Public media store %s failed: %s", action, status)
    raise StoreError(f"The media store {action} failed ({status}).")


def save(content, content_type, folder=FOLDER):
    """Store checked bytes under a random key in `folder` and return the key. Raises StoreError."""
    if not configured():
        raise StoreError("The media store is not configured.")
    key = private_files.new_key(folder, content_type)
    query = urllib.parse.urlencode({"pathname": key})
    body = _send(
        "put", f"{BLOB_API}/?{query}", method="PUT", data=content,
        headers={
            "x-vercel-blob-access": "public",
            "x-content-type": content_type,
            "x-add-random-suffix": "0",
        },
    )
    try:
        stored = json.loads(body).get("pathname")
    except (ValueError, AttributeError):
        stored = None
    if stored != key:
        logger.warning("Public media store put answered without the key it was given.")
        raise StoreError("The media store put was not confirmed.")
    return key


def delete(key):
    """Remove one stored photo, or raise StoreError. Nothing to do for an empty key."""
    if not key:
        return
    if not configured():
        raise StoreError("The media store is not configured.")
    _send(
        "delete", f"{BLOB_API}/delete", method="POST",
        data=json.dumps({"urls": [url_for(key)]}).encode(),
        headers={"content-type": "application/json"},
    )


def discard(key):
    """Remove a photo as a clean-up step. Never raises and never logs the key."""
    if not key:
        return
    try:
        delete(key)
    except Exception:  # noqa: BLE001 - a failed clean-up never hides the error that led to it
        logger.warning("A product photo clean-up failed.")
