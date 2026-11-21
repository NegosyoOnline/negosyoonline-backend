"""Bunny Stream: the one place every Stream call is made. Each store has its own library.

Every call takes a `Library` (id, CDN host, key) read from the store's StreamLibrary row and the
variable BUNNY_STREAM_KEY_<KEY_REF>; there is no global library and no global key. Only the two
public API hosts are global settings (BUNNY_STREAM_API_BASE, BUNNY_STREAM_EMBED_BASE).

The key goes into the AccessKey header of API calls and into the upload signature hash, and
nowhere else: not a log line, an error message, a response, the database or the CDN host (the
source download is a plain GET with no key). Errors carry a short status only.

Calls (Bunny Stream API): Create Video, Get Video (status 0-6, length, storageSize), Delete
Video, PUT upload of the bytes, the signed TUS upload for the browser, and a download of the
source from the library's own CDN host (original, else play_720p.mp4).
"""
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT = 20
TRANSFER_TIMEOUT = 600
CHUNK = 1024 * 1024
SIGNATURE_TTL = 6 * 60 * 60  # seconds an upload signature stays valid
MAX_COPY_BYTES = 4 * 1024 * 1024 * 1024
USER_AGENT = "negosyoonline-backend"
KEY_ENV_PREFIX = "BUNNY_STREAM_KEY_"
KEY_REF_RE = re.compile(r"^[A-Z0-9_]{1,40}$")
NOT_AVAILABLE_MSG = "Video uploads are not available yet."

# Bunny's video status values mapped to the lesson's stream_status.
CREATED, UPLOADED, PROCESSING, TRANSCODING, FINISHED, ERROR, UPLOAD_FAILED = range(7)


class StreamError(Exception):
    """A Stream call failed. The message never holds the key."""


class NotConfigured(Exception):
    """The store has no usable library: no row, or its key variable is missing."""


@dataclass(frozen=True)
class Library:
    id: int
    cdn_host: str
    key: str = field(repr=False)  # never printed, logged or returned


def key_ref_ok(value):
    return isinstance(value, str) and bool(KEY_REF_RE.match(value))


def key_present(key_ref):
    return key_ref_ok(key_ref) and bool(os.environ.get(KEY_ENV_PREFIX + key_ref, "").strip())


def library_from_row(row):
    """The Library of a StreamLibrary row, or raise NotConfigured when its key is not set."""
    if row is None or not key_ref_ok(row.key_ref):
        raise NotConfigured()
    key = os.environ.get(KEY_ENV_PREFIX + row.key_ref, "").strip()
    if not key:
        raise NotConfigured()
    return Library(id=row.library_id, cdn_host=row.cdn_host, key=key)


def library_for_site(site):
    from .models import StreamLibrary

    return library_from_row(StreamLibrary.objects.filter(site=site).first())


def library_by_id(library_id):
    """The Library with this id (any store's), for the delete guard and the copy source."""
    from .models import StreamLibrary

    return library_from_row(StreamLibrary.objects.filter(library_id=library_id).first())


def _api(library, path):
    return f"{settings.BUNNY_STREAM_API_BASE}/library/{int(library.id)}{path}"


def _call(action, library, path, *, method, body=None, content_type=None, length=None):
    """One API call with this library's key. Returns the response body (bytes)."""
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    request = urllib.request.Request(_api(library, path), data=data, method=method)
    request.add_header("AccessKey", library.key)
    request.add_header("accept", "application/json")
    request.add_header("user-agent", USER_AGENT)
    if isinstance(body, dict):
        request.add_header("content-type", "application/json")
    elif content_type:
        request.add_header("content-type", content_type)
    if length is not None:
        request.add_header("content-length", str(length))
    timeout = TRANSFER_TIMEOUT if length is not None else TIMEOUT
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        status = error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        status = "no answer"
    # Raised outside the except block so the request (and its key header) is not chained.
    logger.warning("Bunny Stream %s failed: %s", action, status)
    raise StreamError(f"Video {action} failed ({status}).")


def _json(action, raw):
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise StreamError(f"Video {action} answered unexpectedly.")
    return data


def create_video(library, title):
    """Create an empty video in this library and return its guid."""
    data = _json("create", _call("create", library, "/videos", method="POST", body={"title": title[:200] or "Lesson"}))
    guid = data.get("guid")
    if not isinstance(guid, str) or not re.match(r"^[A-Za-z0-9-]{8,64}$", guid):
        raise StreamError("Video create answered without an id.")
    return guid


def get_video(library, guid):
    """{status, length, storage_size} of a video (status is Bunny's 0-6)."""
    data = _json("check", _call("check", library, f"/videos/{guid}", method="GET"))
    status = data.get("status")
    if not isinstance(status, int):
        raise StreamError("Video check answered unexpectedly.")
    return {
        "status": status,
        "length": int(data.get("length") or 0),
        "storage_size": int(data.get("storageSize") or 0),
    }


def delete_video(library, guid):
    _call("delete", library, f"/videos/{guid}", method="DELETE")


def upload_bytes(library, guid, source, length):
    """PUT the bytes of a video (a file object of `length` bytes) into an existing video."""
    _call(
        "upload", library, f"/videos/{guid}", method="PUT", body=source, length=length,
        content_type="application/octet-stream",
    )


def status_name(code):
    """Bunny's status number as the lesson's stream_status."""
    if code == FINISHED:
        return "ready"
    if code in (ERROR, UPLOAD_FAILED):
        return "failed"
    if code in (UPLOADED, PROCESSING, TRANSCODING):
        return "processing"
    return "uploading"


def tus_signature(library, guid, now=None):
    """What the browser needs for a direct TUS upload. The key is only hashed, never sent."""
    expires = int(now if now is not None else time.time()) + SIGNATURE_TTL
    raw = f"{library.id}{library.key}{expires}{guid}".encode()
    return {
        "endpoint": f"{settings.BUNNY_STREAM_API_BASE}/tusupload",
        "library_id": int(library.id),
        "video_id": guid,
        "signature": hashlib.sha256(raw).hexdigest(),
        "expires": expires,
    }


def embed_url(library_id, guid):
    return f"{settings.BUNNY_STREAM_EMBED_BASE}/{int(library_id)}/{guid}"


def download_source(library, guid):
    """Download a video's source from the library's CDN host into a temporary file, rewound and
    ready to read, and return (file, length). The original is tried first, then play_720p.mp4.
    No key is sent. The caller closes the file."""
    last = None
    for name in ("original", "play_720p.mp4"):
        url = f"https://{library.cdn_host}/{guid}/{name}"
        try:
            return _download(url)
        except StreamError as error:
            last = error
    raise last or StreamError("Video download failed.")


def _download(url):
    request = urllib.request.Request(url, method="GET")
    request.add_header("user-agent", USER_AGENT)
    temp = tempfile.TemporaryFile()
    try:
        with urllib.request.urlopen(request, timeout=TRANSFER_TIMEOUT) as response:
            total = 0
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_COPY_BYTES:
                    raise StreamError("The video is too large to copy.")
                temp.write(chunk)
    except StreamError:
        temp.close()
        raise
    except (urllib.error.URLError, TimeoutError, OSError):
        temp.close()
        logger.warning("Bunny Stream source download failed.")
        raise StreamError("Video download failed.") from None
    if total == 0:
        temp.close()
        raise StreamError("Video download was empty.")
    temp.seek(0)
    return temp, total
