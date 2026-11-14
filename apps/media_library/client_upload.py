"""A video goes from the HQ browser straight to the public Vercel Blob store.

The backend signs a short-lived client token from the store's existing read-write token
(PUBLIC_BLOB_READ_WRITE_TOKEN); no Blob API call, dependency, callback URL or setting is needed. The
algorithm is @vercel/blob's generateClientTokenFromReadWriteToken (client.js, v2):

    store_id = rw_token.split("_")[3]            # vercel_blob_rw_<store id>_<random>
    payload  = base64(json({pathname, maximumSizeInBytes, allowedContentTypes, validUntil (ms),
                            addRandomSuffix, allowOverwrite}))
    token    = "vercel_blob_client_" + store_id + "_" + base64(hmac_sha256_hex(payload, rw_token)
                                                                + "." + payload)

The token is tied to one pathname, one content type and a size, and ends after TOKEN_MINUTES. The
browser then PUTs the file to UPLOAD_API (the same endpoint and `pathname` query the backend's own
puts use) and the backend later confirms the stored object (`confirm_object`).
"""
import base64
import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from apps.catalog import media

from . import store

TOKEN_MINUTES = 10
UPLOAD_API = media.BLOB_API  # https://vercel.com/api/blob
API_VERSION = media.BLOB_API_VERSION
PREFIX = f"{store.FOLDER}/"


def asset_prefix(asset):
    return f"{PREFIX}{asset.public_id}/"


def _store_id():
    parts = (settings.PUBLIC_BLOB_READ_WRITE_TOKEN or "").split("_")
    return parts[3] if len(parts) >= 5 and parts[3] else ""


def usable():
    return media.configured() and bool(_store_id())


def _sign(payload, secret):
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def client_token(pathname, *, now=None):
    """(token, claims) for one upload of `pathname`. Raises StoreError when it cannot be made."""
    if not usable():
        raise store.StoreError("The media store is not configured.")
    now = now or timezone.now()
    claims = {
        "pathname": pathname,
        "maximumSizeInBytes": store.VIDEO_MAX_BYTES,
        "allowedContentTypes": [store.VIDEO_TYPE],
        "validUntil": int((now + timedelta(minutes=TOKEN_MINUTES)).timestamp() * 1000),
        "addRandomSuffix": False,  # the name is already random
        "allowOverwrite": False,
    }
    payload = base64.b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode()
    signed = f"{_sign(payload, settings.PUBLIC_BLOB_READ_WRITE_TOKEN)}.{payload}"
    token = f"vercel_blob_client_{_store_id()}_{base64.b64encode(signed.encode()).decode()}"
    return token, claims


def new_pathname(asset):
    return f"{asset_prefix(asset)}{secrets.token_hex(16)}.mp4"


def upload_instructions(asset, now=None):
    pathname = new_pathname(asset)
    token, claims = client_token(pathname, now=now)
    return {
        "pathname": pathname,
        "client_token": token,
        "upload_url": f"{UPLOAD_API}/?{urllib.parse.urlencode({'pathname': pathname})}",
        "method": "PUT",
        "headers": {
            "authorization": f"Bearer {token}",
            "x-api-version": API_VERSION,
            "x-content-type": store.VIDEO_TYPE,
        },
        "max_bytes": store.VIDEO_MAX_BYTES,
        "content_type": store.VIDEO_TYPE,
        "expires_at": datetime.fromtimestamp(claims["validUntil"] / 1000, tz=dt_timezone.utc),
        "public_url": media.url_for(pathname),
    }


def key_from_url(asset, url):
    """The store key a URL names, or None unless it is https, on our public store's host, with no
    port, credentials, query or fragment, and exactly under this asset's prefix as an .mp4."""
    if not isinstance(url, str) or len(url) > 400:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    base = urllib.parse.urlsplit(settings.PUBLIC_MEDIA_BASE_URL or "")
    if (parts.scheme != "https" or not base.hostname or parts.hostname != base.hostname or port
            or parts.username or parts.password or parts.query or parts.fragment):
        return None
    key = urllib.parse.unquote(parts.path).lstrip("/")
    prefix = asset_prefix(asset)
    tail = key[len(prefix):]
    if (not key.startswith(prefix) or not tail.endswith(".mp4") or "/" in tail or ".." in key
            or media.url_for(key) != url):
        return None
    return key


def confirm_object(key):
    """(content type, size) of the stored object by a HEAD of its public URL, or raises StoreError."""
    request = urllib.request.Request(media.url_for(key), method="HEAD")
    request.add_header("user-agent", media.USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=media.TIMEOUT) as response:
            kind = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            size = int(response.headers.get("content-length") or 0)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise store.StoreError("The uploaded video could not be confirmed.") from None
    return kind, size
