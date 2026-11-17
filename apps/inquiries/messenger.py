"""The only module that talks to Meta's Graph API for the Messenger inbox.

Every call goes through `_transport` (the one network function; tests replace it with recorded
fake payloads, never live Meta). Tokens travel in the Authorization header or the POST body, never in
a URL, and no token, Meta response body or URL is ever logged or returned: a failure is a fixed
outcome code (apps/inquiries/outcomes.py). The Graph API version is pinned here, in one place, and
recorded in docs/runbooks/inquiries.md.

Meta sources (checked 2026-11-17): Graph versions
https://developers.facebook.com/docs/graph-api/changelog/versions ; webhooks
https://developers.facebook.com/docs/messenger-platform/webhooks ; Send API
https://developers.facebook.com/docs/messenger-platform/send-messages ; error codes
https://developers.facebook.com/docs/messenger-platform/error-codes ; subscribed_apps
https://developers.facebook.com/docs/graph-api/reference/page/subscribed_apps/ ; Facebook Login for
Business https://developers.facebook.com/docs/facebook-login/facebook-login-for-business ."""
import hashlib
import hmac
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from apps.inquiries import outcomes
from apps.inquiries.adapters.base import Result

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v26.0"  # the one place the Graph API version is set
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
TIMEOUT = 10  # seconds
# Used when no META_MESSENGER_CONFIG_ID is set.
SCOPES = "pages_show_list,pages_manage_metadata,pages_messaging"  # exactly the three App Review covers
REQUIRED_PERMISSIONS = ("pages_messaging", "pages_manage_metadata", "pages_show_list")
SUBSCRIBED_FIELDS = "messages,messaging_postbacks,message_echoes,message_deliveries,message_reads"
MAX_TEXT = 2000
MAX_GRANTED_PAGES = 100  # cap on Page ids taken from debug_token's granular scopes
PAGE_ID_RE = re.compile(r"^[0-9]{1,64}$")


def app_secret():
    return settings.META_MESSENGER_APP_SECRET


def configured():
    return bool(settings.META_MESSENGER_APP_ID and settings.META_MESSENGER_APP_SECRET)


def authorization_url(redirect_uri, state):
    params = {
        "client_id": settings.META_MESSENGER_APP_ID, "redirect_uri": redirect_uri, "state": state,
        "response_type": "code",
    }
    if settings.META_MESSENGER_CONFIG_ID:  # Facebook Login for Business: config_id replaces scope
        params["config_id"] = settings.META_MESSENGER_CONFIG_ID
    else:
        params["scope"] = SCOPES
    return f"{DIALOG_URL}?{urllib.parse.urlencode(params)}"


def _proof(token):
    return hmac.new(app_secret().encode(), token.encode(), hashlib.sha256).hexdigest()


def _transport(method, path, *, params=None, form=None, token=None):
    """The one network call: (http_status, parsed JSON or {}). Never raises for an HTTP error status;
    raises OSError for no answer. The token goes in the Authorization header."""
    url = f"{GRAPH_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https host
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    return status, body if isinstance(body, dict) else {}


# error.code -> outcome. Meta's table: https://developers.facebook.com/docs/messenger-platform/error-codes
WINDOW_SUBCODES = {2534022, 2018278, 2018065}  # code 10: outside the standard messaging window
UNREACHABLE = {(551, None), (200, 1545041), (100, 2018001), (10, 2018108)}


def map_error(status, body):
    """The outcome code for a failed Graph answer. Reads only the numeric code and subcode."""
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    code, sub = error.get("code"), error.get("error_subcode")
    code = code if isinstance(code, int) else None
    sub = sub if isinstance(sub, int) else None
    if code == 190:
        return outcomes.TOKEN_INVALID
    if code == 10 and sub in WINDOW_SUBCODES:
        return outcomes.WINDOW_CLOSED
    if (code, sub) in UNREACHABLE or (code, None) in UNREACHABLE:
        return outcomes.NOT_FOUND
    if code in (10, 200, 299) or (code is not None and 200 <= code <= 299):
        return outcomes.FORBIDDEN_PAGE
    if code in (4, 9, 17, 32, 613) or status == 429:
        return outcomes.RATE_LIMITED
    if code in (1, 2) or status >= 500:
        return outcomes.PROVIDER_DOWN
    return outcomes.BAD_RESPONSE


def call(method, path, *, params=None, form=None, token=None):
    """Result(code, body): `ok` with the parsed body, or a fixed outcome code. Never raises."""
    try:
        status, body = _transport(method, path, params=params, form=form, token=token)
    except (OSError, ValueError) as error:
        logger.warning("Messenger call failed (%s)", type(error).__name__)
        return Result(outcomes.PROVIDER_DOWN)
    if status >= 400 or "error" in body:
        code = map_error(status, body)
        logger.warning("Messenger call refused (%s)", code)
        return Result(code)
    return Result(outcomes.OK, body)


def _app_token():
    return f"{settings.META_MESSENGER_APP_ID}|{settings.META_MESSENGER_APP_SECRET}"


# -- connecting -----------------------------------------------------------------------------

def exchange_code(code, redirect_uri):
    """The long-lived user token for a one-time login code. Result(ok, token). The token is used
    once by the caller and never stored."""
    first = call("POST", "/oauth/access_token", form={
        "client_id": settings.META_MESSENGER_APP_ID, "client_secret": app_secret(),
        "redirect_uri": redirect_uri, "code": code,
    })
    token = first.value.get("access_token") if first.ok else None
    if not isinstance(token, str) or not token:
        return Result(first.code if not first.ok else outcomes.BAD_RESPONSE)
    longer = call("POST", "/oauth/access_token", form={
        "grant_type": "fb_exchange_token", "client_id": settings.META_MESSENGER_APP_ID,
        "client_secret": app_secret(), "fb_exchange_token": token,
    })
    long_token = longer.value.get("access_token") if longer.ok else None
    return Result(outcomes.OK, long_token) if isinstance(long_token, str) and long_token else Result(
        longer.code if not longer.ok else outcomes.BAD_RESPONSE
    )


def granted_permissions(user_token):
    """Result(ok, set of granted permission names)."""
    result = call("GET", "/me/permissions", params={"appsecret_proof": _proof(user_token)}, token=user_token)
    if not result.ok:
        return result
    rows = result.value.get("data")
    if not isinstance(rows, list):
        return Result(outcomes.BAD_RESPONSE)
    return Result(outcomes.OK, {r.get("permission") for r in rows if isinstance(r, dict) and r.get("status") == "granted"})


def user_id(user_token):
    result = call("GET", "/me", params={"fields": "id", "appsecret_proof": _proof(user_token)}, token=user_token)
    value = str(result.value.get("id", "")) if result.ok else ""
    return Result(outcomes.OK, value[:64]) if value else Result(result.code if not result.ok else outcomes.BAD_RESPONSE)


def list_pages(user_token):
    """Result(ok, [{"id", "name", "access_token"}]) for the Pages the person manages."""
    pages, after = [], None
    for _ in range(5):  # at most 5 pages of results
        params = {"fields": "id,name,access_token", "limit": "100", "appsecret_proof": _proof(user_token)}
        if after:
            params["after"] = after
        result = call("GET", "/me/accounts", params=params, token=user_token)
        if not result.ok:
            return result
        for row in result.value.get("data") or []:
            if isinstance(row, dict) and row.get("id") and row.get("access_token"):
                pages.append({
                    "id": str(row["id"])[:64], "name": str(row.get("name", ""))[:60],
                    "access_token": str(row["access_token"]),
                })
        after = ((result.value.get("paging") or {}).get("cursors") or {}).get("after")
        if not after or not (result.value.get("paging") or {}).get("next"):
            break
    if pages:
        return Result(outcomes.OK, pages)
    return _granted_pages(user_token)


def _granted_target_ids(user_token):
    """Result(ok, [Page ids]) the person ticked in the Login for Business picker, read from
    debug_token's granular scopes (pages_messaging, else pages_show_list). Deduped, in order, capped,
    digits only. debug_token takes both tokens as query parameters (that is how Graph defines it); this
    URL is never logged, and `call` logs only a fixed outcome code."""
    result = call("GET", "/debug_token", params={"input_token": user_token, "access_token": _app_token()})
    if not result.ok:
        return result
    data = result.value.get("data")
    scopes = data.get("granular_scopes") if isinstance(data, dict) else None
    if not isinstance(scopes, list):
        return Result(outcomes.OK, [])
    by_scope = {}
    for entry in scopes:
        if isinstance(entry, dict) and isinstance(entry.get("target_ids"), list):
            by_scope.setdefault(entry.get("scope"), []).extend(entry["target_ids"])
    ids = []
    for scope in ("pages_messaging", "pages_show_list"):
        for raw in by_scope.get(scope, []):
            page_id = str(raw) if isinstance(raw, (str, int)) and not isinstance(raw, bool) else ""
            if PAGE_ID_RE.match(page_id) and page_id not in ids:
                ids.append(page_id)
        if ids:
            break
    return Result(outcomes.OK, ids[:MAX_GRANTED_PAGES])


def _granted_pages(user_token):
    """Fallback for Pages owned by a business portfolio, which /me/accounts leaves out under Login for
    Business without business_management (not requested, never to be added). Reads each granted Page
    directly. Any failure (debug_token or one Page read) fails the whole call with that error's mapped
    code, so a partial list is never shown as the full one."""
    ids = _granted_target_ids(user_token)
    if not ids.ok:
        return ids
    pages = []
    for page_id in ids.value:
        result = call("GET", f"/{page_id}", params={"fields": "id,name,access_token", "appsecret_proof": _proof(user_token)}, token=user_token)
        if not result.ok:
            return result
        row = result.value
        if row.get("id") and row.get("access_token"):
            pages.append({
                "id": str(row["id"])[:64], "name": str(row.get("name", ""))[:60],
                "access_token": str(row["access_token"]),
            })
    return Result(outcomes.OK, pages)


def subscribe_page(page_id, page_token):
    result = call("POST", f"/{page_id}/subscribed_apps", form={
        "subscribed_fields": SUBSCRIBED_FIELDS, "appsecret_proof": _proof(page_token),
    }, token=page_token)
    if result.ok and result.value.get("success") is not True:
        return Result(outcomes.BAD_RESPONSE)
    return result


def unsubscribe_page(page_id, page_token):
    """Best effort: removes this app's subscription, with the Page's own token (the caller decrypts it)."""
    return call("DELETE", f"/{page_id}/subscribed_apps", params={"appsecret_proof": _proof(page_token)}, token=page_token)


# -- sending --------------------------------------------------------------------------------

def send_text(page_id, page_token, psid, text):
    """Send API reply inside the standard window. Result(ok, Meta's message id)."""
    result = call("POST", f"/{page_id}/messages", form={
        "recipient": json.dumps({"id": psid}), "messaging_type": "RESPONSE",
        "message": json.dumps({"text": text[:MAX_TEXT]}), "appsecret_proof": _proof(page_token),
    }, token=page_token)
    if not result.ok:
        return result
    mid = result.value.get("message_id")
    return Result(outcomes.OK, str(mid)[:128]) if mid else Result(outcomes.BAD_RESPONSE)
