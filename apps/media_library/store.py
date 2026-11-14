"""Where a media file is stored: the public Vercel Blob store of apps/catalog/media.py.

The existing helper stores JPEG, PNG and WebP only, so this adds MP4 on the same store, token and
Blob call (a PUT with a random key); it makes no new kind of Blob call and reads no new setting.
Bytes pass through this server (a multipart post), so the limits are tight; a browser-direct upload
token flow is not built (it would be a new Blob call).
"""
import json
import secrets
import urllib.parse

from apps.catalog import media
from apps.common import private_files

FOLDER = "media-library"
IMAGE_MAX_BYTES = 10 * 1024 * 1024  # TWCinc ML_MAX_IMAGE_BYTES
VIDEO_MAX_BYTES = 50 * 1024 * 1024  # TWCinc's proxied ceiling is 85 MB; lower while bytes cross this server
VIDEO_TYPE = "video/mp4"
EXT = {**private_files.EXT, VIDEO_TYPE: ".mp4"}

StoreError = media.StoreError


def configured():
    return media.configured()


def url_for(key):
    return media.url_for(key)


def check_image(upload):
    return private_files.check(upload, allowed=private_files.IMAGE_TYPES, max_bytes=IMAGE_MAX_BYTES)


def check_video(upload):
    """(content, "video/mp4") or FileRefused: an MP4 (declared as video/mp4 and `ftyp` in the first
    bytes), at most VIDEO_MAX_BYTES."""
    size = getattr(upload, "size", None)
    if not hasattr(upload, "read") or size is None:
        raise private_files.FileRefused(private_files.WRONG_TYPE)
    if size > VIDEO_MAX_BYTES:
        raise private_files.FileRefused(private_files.TOO_LARGE)
    upload.seek(0)
    content = upload.read(VIDEO_MAX_BYTES + 1)
    upload.seek(0)
    if len(content) > VIDEO_MAX_BYTES:
        raise private_files.FileRefused(private_files.TOO_LARGE)
    declared = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    if declared != VIDEO_TYPE or content[4:8] != b"ftyp":
        raise private_files.FileRefused(private_files.WRONG_TYPE)
    return content, VIDEO_TYPE


def save(content, content_type):
    """Store checked bytes under a random key and return it. Raises StoreError."""
    if not media.configured():
        raise StoreError("The media store is not configured.")
    key = f"{FOLDER}/{secrets.token_hex(16)}{EXT[content_type]}"
    query = urllib.parse.urlencode({"pathname": key})
    body = media._send(
        "put", f"{media.BLOB_API}/?{query}", method="PUT", data=content,
        headers={"x-vercel-blob-access": "public", "x-content-type": content_type,
                 "x-add-random-suffix": "0"},
    )
    try:
        stored = json.loads(body).get("pathname")
    except (ValueError, AttributeError):
        stored = None
    if stored != key:
        raise StoreError("The media store put was not confirmed.")
    return key


def discard(key):
    media.discard(key)
