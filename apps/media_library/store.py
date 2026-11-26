"""Where a media image is stored: the public Vercel Blob store of apps/catalog/media.py.

Images, posters and thumbnails pass through this server (one file per multipart request, at most
IMAGE_MAX_BYTES; RequestSizeCapMiddleware refuses a request above 21 MB). A video does not: the
owner's browser uploads it straight to the store with a client token (apps/media_library/client_upload.py).
Every key is media-library/<site id>/<asset id>/<random>, so a file is always under its own site.
"""
import json
import re
import secrets
import urllib.parse

from apps.catalog import media
from apps.common import file_refs, private_files
from apps.training import stream

FOLDER = "media-library"
IMAGE_MAX_BYTES = 10 * 1024 * 1024  # TWCinc ML_MAX_IMAGE_BYTES
VIDEO_MAX_BYTES = 50 * 1024 * 1024  # browser to store, so TWCinc's proxied 85 MB cap does not bind
VIDEO_TYPE = "video/mp4"

StoreError = media.StoreError


def configured():
    return media.configured()


BUNNY_PREFIX = "bunny:"
BUNNY_RE = re.compile(r"^bunny:([0-9]{1,10}):([A-Za-z0-9-]{8,64})$")


def bunny_key(library_id, guid):
    """The file_key (and ledger key) of a video held in the store's own Bunny Stream library
    (MEDIA_VIDEO_BUNNY_ENABLED). It is no Blob path: there is no file URL, the player plays it."""
    return f"{BUNNY_PREFIX}{int(library_id)}:{guid}"


def bunny_ref(key):
    """(library id, video guid) when `key` names a Bunny Stream video, else None."""
    found = BUNNY_RE.match(key) if isinstance(key, str) else None
    return (int(found.group(1)), found.group(2)) if found else None


def url_for(key):
    """The public Blob URL of a key; a Bunny video has none (None)."""
    return None if bunny_ref(key) else media.url_for(key)


def check_image(upload):
    return private_files.check(upload, allowed=private_files.IMAGE_TYPES, max_bytes=IMAGE_MAX_BYTES)


def asset_prefix(asset):
    """media-library/<site id>/<asset id>/ (asset.site is the site row)."""
    return f"{FOLDER}/{asset.site.public_id}/{asset.public_id}/"


def save(asset, content, content_type):
    """Store checked image bytes under a random key in the asset's prefix and return it. Raises StoreError."""
    if not media.configured():
        raise StoreError("The media store is not configured.")
    key = f"{asset_prefix(asset)}{secrets.token_hex(16)}{private_files.EXT[content_type]}"
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
    """Remove the file unless another row still names its key (apps/common/file_refs.py)."""
    file_refs.discard_if_unreferenced(key)


def delete(key):
    """Remove the file, raising StoreError when the store cannot. A key already absent is gone, not
    an error (Vercel Blob answers a delete of an absent key with success). Used by the ledger. A
    Bunny video is deleted from its library; one Bunny no longer has (404) is gone too."""
    ref = bunny_ref(key)
    if ref is None:
        media.delete(key)
        return
    try:
        stream.delete_video(stream.library_by_id(ref[0]), ref[1])
    except stream.NotConfigured:
        raise StoreError("The video library is not configured.") from None
    except stream.StreamError as error:
        if error.status != 404:
            raise StoreError("The video could not be deleted.") from None
