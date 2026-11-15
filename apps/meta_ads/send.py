"""The one HTTP call to Meta's Conversions API. Standard library only.

The access token travels in the POST body, never the query string. Nothing from Meta's answer
is kept but the HTTP status and the numeric error code; exception text, response bodies and the
request are never logged or returned."""
import json
import re
import urllib.error
import urllib.request
from typing import NamedTuple

# Meta's versions page (developers.facebook.com/docs/graph-api/changelog/versions, read 2026-11-14): Marketing API
# v23.0 expired 2026-06-09, v24.0 expired 2026-10-06, v25.0 is current. The Conversions API is a Marketing API endpoint.
GRAPH_VERSION = "v25.0"
TIMEOUT_SECONDS = 5
MAX_ERROR_BODY = 20_000
FBTRACE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")

OK, BAD_TOKEN, BAD_PIXEL, RATE_LIMITED, META_DOWN, FAILED = (
    "ok", "bad_token", "bad_pixel", "rate_limited", "meta_down", "failed",
)
RETRYABLE = (RATE_LIMITED, META_DOWN)

# Meta's numeric error codes (error.code in an error answer).
TOKEN_CODES = (190, 102, 200)  # invalid or expired token, session, permission
PIXEL_CODES = (100, 803, 2804001)  # unknown or unusable dataset
RATE_CODES = (4, 17, 32, 613, 80004)


class Result(NamedTuple):
    outcome: str
    http_status: int | None = None
    error_code: int | None = None
    events_received: int | None = None  # Meta's count of events it took; a number, never text
    fbtrace_id: str = ""  # Meta's trace id for support; not personal


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 32_000 else None


def _trace(value):
    return value if isinstance(value, str) and FBTRACE_RE.match(value) else ""


def _read(source):
    """The parsed JSON body of an answer, or {}. Nothing but numbers and a short id leaves here."""
    try:
        body = json.loads(source.read(MAX_ERROR_BODY).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - an unreadable answer is just "nothing recorded"
        return {}
    return body if isinstance(body, dict) else {}


def _open(request, timeout):
    """The single place the network is touched, so tests replace it."""
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - a fixed https address


def _error_parts(error):
    """(numeric error code or None, fbtrace_id or "") of an error answer."""
    err = _read(error).get("error")
    err = err if isinstance(err, dict) else {}
    code = err.get("code")
    code = code if isinstance(code, int) and not isinstance(code, bool) else None
    return code, _trace(err.get("fbtrace_id"))


def post_event(pixel_id, token, event, test_event_code=""):
    """Send one event. Returns a Result; never raises."""
    payload = {"data": [event], "access_token": token}
    if test_event_code:
        payload["test_event_code"] = test_event_code
    request = urllib.request.Request(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{pixel_id}/events",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _open(request, TIMEOUT_SECONDS) as response:
            body = _read(response)
            return Result(OK, response.status, None, _int(body.get("events_received")), _trace(body.get("fbtrace_id")))
    except urllib.error.HTTPError as error:
        status = error.code
        code, trace = _error_parts(error)
        if status >= 500:
            return Result(META_DOWN, status, code, None, trace)
        if status == 429 or code in RATE_CODES:
            return Result(RATE_LIMITED, status, code, None, trace)
        if code in TOKEN_CODES or status in (401, 403):
            return Result(BAD_TOKEN, status, code, None, trace)
        if code in PIXEL_CODES:
            return Result(BAD_PIXEL, status, code, None, trace)
        return Result(FAILED, status, code, None, trace)
    except Exception:  # noqa: BLE001 - timeout, DNS, TLS: the text is never kept
        return Result(META_DOWN)
