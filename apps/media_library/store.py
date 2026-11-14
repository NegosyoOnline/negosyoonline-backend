"""Where a media image is stored: the public Vercel Blob store of apps/catalog/media.py.

Images, posters and thumbnails pass through this server (one file per multipart request, at most
IMAGE_MAX_BYTES; RequestSizeCapMiddleware refuses a request above 21 MB). A video does not: the HQ
browser uploads it straight to the store with a client token (apps/media_library/client_upload.py).
"""
import json
import secrets
import urllib.parse

from apps.catalog import media
from apps.common import private_files

FOLDER = "media-library"
IMAGE_MAX_BYTES = 10 * 1024 * 1024  # TWCinc ML_MAX_IMAGE_BYTES
VIDEO_MAX_BYTES = 50 * 1024 * 1024  # browser to store, so TWCinc's proxied 85 MB cap does not bind
VIDEO_TYPE = "video/mp4"

StoreError = media.StoreError


def configured():
    return media.configured()


def url_for(key):
    return media.url_for(key)


def check_image(upload):
    return private_files.check(upload, allowed=private_files.IMAGE_TYPES, max_bytes=IMAGE_MAX_BYTES)


def save(content, content_type):
    """Store checked image bytes under a random key and return it. Raises StoreError."""
    if not media.configured():
        raise StoreError("The media store is not configured.")
    key = f"{FOLDER}/{secrets.token_hex(16)}{private_files.EXT[content_type]}"
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
