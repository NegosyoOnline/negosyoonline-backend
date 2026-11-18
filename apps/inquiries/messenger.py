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
Business https://developers.facebook.com/docs/facebook-login/facebook-login-for-business .

Page comments (task z8uq9mg9pe; Meta sources checked 2026-11-18, see the comment block above
COMMENT_PERMISSIONS)."""
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

PROVIDER = "messenger"  # InquiryConnection.provider of the direct Messenger inbox
GRAPH_VERSION = "v26.0"  # the one place the Graph API version is set
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
TIMEOUT = 10  # seconds
# Used when no META_MESSENGER_CONFIG_ID is set.
SCOPES = "pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement"  # exactly the four App Review covers
REQUIRED_PERMISSIONS = ("pages_messaging", "pages_manage_metadata", "pages_show_list", "pages_read_engagement")
SUBSCRIBED_FIELDS = "messages,messaging_postbacks,message_echoes,message_deliveries,message_reads"
MAX_TEXT = 2000
MAX_GRANTED_PAGES = 100  # cap on Page ids taken from debug_token's granular scopes
PAGE_ID_RE = re.compile(r"^[0-9]{1,64}$")


# -- Page comments: Meta facts, checked against Meta's developer docs on 2026-11-18 ------------
# * `feed` webhook field: item `comment`, verb add / edited / remove (the documented verb enum also has
#   hide / unhide), value keys comment_id, post_id, parent_id, from{id,name}, message, created_time,
#   post{...}. "Webhooks are not sent for Ad Posts, but are sent for Comments on Ad Posts." The page
#   names pages_manage_metadata for receiving these webhooks.
#   https://developers.facebook.com/docs/graph-api/webhooks/reference/page/
# * Public reply: POST /{comment-id}/comments with `message`; answer {"id"}; Page token of a person
#   who can MODERATE, or pages_manage_engagement.
#   https://developers.facebook.com/docs/graph-api/reference/object/comments/
# * Hide: POST /{comment-id} with is_hidden (boolean, "only applicable to Page comments"); answer
#   {"success": bool}.  https://developers.facebook.com/docs/graph-api/reference/comment/
# * Private reply: POST /{page-id}/messages with recipient {comment_id}; pages_messaging; "must be sent
#   within 7 days from when the post or comment was created"; "only one message can be sent to the
#   person who commented".  https://developers.facebook.com/docs/messenger-platform/discovery/private-replies
# * Permissions: pages_read_user_content (read comments and posts by users on a Page) and
#   pages_manage_engagement (create, edit, delete comments on a Page), each App Review.
#   https://developers.facebook.com/docs/permissions/
# NOT confirmed in the docs (kept as the safest reading, listed in the task report): the exact
# messaging_type for a private reply (RESPONSE is used), the permission the hide call needs
# (pages_manage_engagement is assumed), and the error codes for a second private reply.
COMMENT_PERMISSIONS = ("pages_read_user_content", "pages_manage_engagement")
COMMENT_SCOPES = SCOPES + "," + ",".join(COMMENT_PERMISSIONS)
FEED_FIELD = "feed"
MAX_COMMENT_TEXT = 2000
COMMENT_ID_RE = re.compile(r"^[0-9_]{1,64}$")


def app_secret():
    return settings.META_MESSENGER_APP_SECRET


def configured():
    return bool(settings.META_MESSENGER_APP_ID and settings.META_MESSENGER_APP_SECRET)


def authorization_url(redirect_uri, state, comments=False):
    params = {
        "client_id": settings.META_MESSENGER_APP_ID, "redirect_uri": redirect_uri, "state": state,
        "response_type": "code",
    }
    # Comments use the second configuration; with none set they ask for the scopes, never the
    # Messenger-only configuration (it lacks the two extra permissions).
    config_id = settings.META_COMMENTS_CONFIG_ID if comments else settings.META_MESSENGER_CONFIG_ID
    if config_id:  # Facebook Login for Business: config_id replaces scope
        params["config_id"] = config_id
    else:
        params["scope"] = COMMENT_SCOPES if comments else SCOPES
    return f"{DIALOG_URL}?{urllib.parse.urlencode(params)}"


def _proof(token):
    return hmac.new(app_secret().encode(), token.encode(), hashlib.sha256).hexdigest()


def _transport(method, path, *, params=None, form=None, token=None, timeout=TIMEOUT):
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
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https host
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


_TOKEN_RUN = re.compile(r"[A-Za-z0-9_\-|.%]{20,}")
_PLAIN_NAME = re.compile(r"[a-z_]+")  # a permission-style name (pages_read_engagement) is not a token
_ID_SEGMENT = re.compile(r"^\d+$")


def _path_template(path):
    """The path only (never params or query) with all-digit segments as `{id}`."""
    clean = str(path).split("?", 1)[0].split("#", 1)[0]
    return "/".join("{id}" if _ID_SEGMENT.match(part) else part for part in clean.split("/"))[:120]


def _log_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else ""


def _log_text(value, limit):
    if not isinstance(value, str):
        return ""
    masked = _TOKEN_RUN.sub(lambda m: m.group(0) if _PLAIN_NAME.fullmatch(m.group(0)) else "***", value)
    return " ".join(masked.split())[:limit]


def _log_error_fields(body):
    error = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
    return (
        _log_int(error.get("code")), _log_int(error.get("error_subcode")),
        _log_text(error.get("type"), 40), _log_text(error.get("fbtrace_id"), 40),
        _log_text(error.get("message"), 200),
    )


def call(method, path, *, params=None, form=None, token=None, timeout=None, private=None):
    """Result(code, body): `ok` with the parsed body, or a fixed outcome code. Never raises.
    `timeout` (seconds) replaces the default for this call. `private` is a fixed label for a path
    that holds a customer id: it is logged instead of the path, and a refusal logs codes only
    (Graph's message and trace id can quote the id)."""
    extra = {"timeout": timeout} if timeout is not None else {}
    shown = private or _path_template(path)
    try:
        status, body = _transport(method, path, params=params, form=form, token=token, **extra)
    except (OSError, ValueError) as error:
        logger.warning("Messenger call failed (%s) %s %s", type(error).__name__, _log_text(method, 10), shown)
        return Result(outcomes.PROVIDER_DOWN)
    if status >= 400 or "error" in body:
        code = map_error(status, body)
        if private:
            error_code, subcode, *_ = _log_error_fields(body)
            logger.warning(
                "Messenger call refused (%s) %s %s status=%s code=%s subcode=%s",
                code, _log_text(method, 10), shown, _log_int(status), error_code, subcode,
            )
            return Result(code)
        error_code, subcode, error_type, fbtrace, message = _log_error_fields(body)
        logger.warning(
            "Messenger call refused (%s) %s %s status=%s code=%s subcode=%s type=%s fbtrace=%s msg=%s",
            code, _log_text(method, 10), _path_template(path), _log_int(status), error_code, subcode,
            error_type, fbtrace, message,
        )
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


def subscribe_page(page_id, page_token, comments=False):
    """Subscribe the app to the Page. `comments` adds the `feed` field: only for a site whose comments
    switch is on and whose token has the comments permissions (the caller decides)."""
    result = call("POST", f"/{page_id}/subscribed_apps", form={
        "subscribed_fields": SUBSCRIBED_FIELDS + ("," + FEED_FIELD if comments else ""), "appsecret_proof": _proof(page_token),
    }, token=page_token)
    if result.ok and result.value.get("success") is not True:
        return Result(outcomes.BAD_RESPONSE)
    return result


def unsubscribe_page(page_id, page_token):
    """Best effort: removes this app's subscription, with the Page's own token (the caller decrypts it)."""
    return call("DELETE", f"/{page_id}/subscribed_apps", params={"appsecret_proof": _proof(page_token)}, token=page_token)


# -- the customer's profile ------------------------------------------------------------------

PROFILE_TIMEOUT = 3  # seconds: a slow Graph answer is given up, never waited for


def profile(psid, page_token):
    """Result(ok, {"first_name", "last_name", "profile_pic"}) for a customer of the Page, read with the
    Page's own token. The customer id is in the path, so the call logs a fixed label and codes only."""
    return call(
        "GET", f"/{urllib.parse.quote(str(psid), safe='')}",
        params={"fields": "first_name,last_name,profile_pic", "appsecret_proof": _proof(page_token)},
        token=page_token, timeout=PROFILE_TIMEOUT, private="/{psid}",
    )


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


# -- comments ---------------------------------------------------------------------------------

def _comment_path(comment_id, tail=""):
    return f"/{urllib.parse.quote(str(comment_id), safe='')}{tail}"


def reply_comment(page_token, comment_id, text):
    """Public reply under a comment, posted as the Page. Result(ok, Meta's id of the new comment).
    The comment id is in the path, so the call logs a fixed label and codes only."""
    if not COMMENT_ID_RE.match(str(comment_id)):
        return Result(outcomes.NOT_FOUND)
    result = call("POST", _comment_path(comment_id, "/comments"), form={
        "message": text[:MAX_COMMENT_TEXT], "appsecret_proof": _proof(page_token),
    }, token=page_token, private="/{comment}/comments")
    if not result.ok:
        return result
    new_id = result.value.get("id")
    return Result(outcomes.OK, str(new_id)[:128]) if new_id else Result(outcomes.BAD_RESPONSE)


def commenter_picture(author_id, page_token):
    """Result(ok, {"data": {"url", "is_silhouette"}}): the picture of a commenter, read with the Page's
    own token (the picture edge of the commenter's page-scoped id; redirect=false answers JSON). The id
    is in the path, so the call logs a fixed label and codes only."""
    if not PAGE_ID_RE.match(str(author_id)):
        return Result(outcomes.NOT_FOUND)
    return call(
        "GET", f"/{urllib.parse.quote(str(author_id), safe='')}/picture",
        params={"redirect": "false", "type": "normal", "appsecret_proof": _proof(page_token)},
        token=page_token, timeout=PROFILE_TIMEOUT, private="/{commenter}/picture",
    )


def post_text(post_id, page_token):
    """Result(ok, {"message", "permalink_url"}): the text and link of a Page post (fields allowed by
    pages_read_engagement). The post id is in the path, so the call logs a fixed label and codes only."""
    if not COMMENT_ID_RE.match(str(post_id)):
        return Result(outcomes.NOT_FOUND)
    return call(
        "GET", _comment_path(post_id), params={"fields": "message,permalink_url", "appsecret_proof": _proof(page_token)},
        token=page_token, timeout=PROFILE_TIMEOUT, private="/{post}",
    )


def hide_comment(page_token, comment_id):
    """Hide a comment on Facebook (it is not deleted). Result(ok)."""
    if not COMMENT_ID_RE.match(str(comment_id)):
        return Result(outcomes.NOT_FOUND)
    result = call("POST", _comment_path(comment_id), form={
        "is_hidden": "true", "appsecret_proof": _proof(page_token),
    }, token=page_token, private="/{comment}")
    if result.ok and result.value.get("success") is not True:
        return Result(outcomes.BAD_RESPONSE)
    return result


def private_reply(page_id, page_token, comment_id, text):
    """Private reply to a commenter: Send API with recipient {comment_id}. Result(ok, Meta's message id)."""
    if not COMMENT_ID_RE.match(str(comment_id)):
        return Result(outcomes.NOT_FOUND)
    result = call("POST", f"/{page_id}/messages", form={
        "recipient": json.dumps({"comment_id": str(comment_id)}), "messaging_type": "RESPONSE",
        "message": json.dumps({"text": text[:MAX_TEXT]}), "appsecret_proof": _proof(page_token),
    }, token=page_token, private="/{page}/messages(comment)")
    if not result.ok:
        return result
    mid = result.value.get("message_id")
    return Result(outcomes.OK, str(mid)[:128]) if mid else Result(outcomes.BAD_RESPONSE)
