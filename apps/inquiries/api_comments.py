"""The Facebook comments routes (task z8uq9mg9pe), under /v1/sites/{site_id}/inquiries/comments/.
The contract is docs/contracts/comments-api-c1.md; this docstring only lists the routes:

    GET    status/                  {enabled, connected, page_name, ready, reason}
    GET    ?scope=&answered=&unread=&q=&cursor=   -> {items, next_cursor, unread_total}
    GET    {ref}/                   the comment, its replies and its recent actions
    POST   {ref}/read/              the comment, read
    POST   {ref}/reply/             {text, idempotency_key}  public reply as the Page
    POST   {ref}/private-reply/     {text, idempotency_key}  one per comment, within 7 days
    POST   {ref}/hide/              hide on Facebook

While INQUIRY_COMMENTS_ENABLED (with the Messenger switch) is off, or the Meta app values or the
comment tables are missing, every route answers 404, signed in or not. A Meta refusal is a 200 with
state `failed` and a fixed error_code; a failed precondition is a 409 with a fixed code."""
import re

from ninja import Router, Schema
from pydantic import ConfigDict

from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.inquiries import api_owner, comments, messenger
from apps.inquiries.switch import comment_tables_exist, comments_on

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MESSAGES = {
    "comment_removed": "That comment was deleted on Facebook.",
    "not_connected": "Connect your Facebook Page first.",
    "reconnect": "Reconnect your Facebook Page to continue.",
    "permission_missing": "Turn on comments for your Facebook Page first.",
    "own_comment": "That is your Page's own comment.",
    "window_closed": "A private reply can only be sent within 7 days of the comment.",
    "already_sent": "A private reply was already sent for this comment.",
    "already_hidden": "That comment is already hidden.",
    "conflict": "That key was already used for another action.",
}


def comments_site(request):
    """The router's sign-in step: closed unless comments, Messenger and the Meta app values are on."""
    if not comments_on() or not messenger.configured() or not comment_tables_exist():
        raise api_owner._not_found()
    return api_owner.inquiries_site(request)


router = Router(tags=["inquiries"], auth=comments_site)


class ReplyIn(Schema):
    model_config = ConfigDict(extra="forbid")
    text: str
    idempotency_key: str


def _text(payload, limit):
    text = payload.text.strip()
    if not text:
        raise api_owner._invalid("text", "Write a reply first.")
    if len(text) > limit:
        raise api_owner._invalid("text", f"A reply is at most {limit} characters.")
    if not KEY_RE.match(payload.idempotency_key):
        raise api_owner._invalid("idempotency_key", "That key does not look right.")
    return text


def _done(call):
    try:
        data = call()
    except comments.Refusal as refusal:
        raise ApiError(refusal.code, MESSAGES.get(refusal.code, "Not possible now."), status=409) from None
    if data is None:
        raise api_owner._not_found()
    return data


def _choice(value, allowed, field):
    if value not in allowed:
        message = f"{field.capitalize()} must be one of: {', '.join(a for a in allowed if a)}."
        raise ApiError(f"invalid_{field}", message, status=422, fields={field: [message]})
    return value


@router.get("/sites/{site_id}/inquiries/comments/status/")
def status(request, site_id: str):
    hit("inq-read", request.account.pk, limit=api_owner.READ_LIMIT, window=api_owner.WINDOW)
    return comments.status_out(request.inquiry_site)


@router.get("/sites/{site_id}/inquiries/comments/")
def comment_list(request, site_id: str, scope: str | None = None, answered: str | None = None,
                 unread: str | None = None, q: str | None = None, cursor: str | None = None):
    hit("inq-read", request.account.pk, limit=api_owner.READ_LIMIT, window=api_owner.WINDOW)
    scope = _choice(scope or "all", ("all", "post", "ad"), "scope")
    answered = _choice(answered or "all", ("all", "answered", "unanswered"), "answered")
    unread = _choice(unread or "", ("", "1", "0"), "unread")
    query = None
    if q is not None:
        query = comments.valid_query(q)
        if query is None:
            message = f"Search needs {comments.SEARCH_MIN} to {comments.SEARCH_MAX} characters."
            raise ApiError("invalid_query", message, status=422, fields={"q": [message]})
    if cursor and not comments.CURSOR_RE.match(cursor):
        message = "That cursor does not look right."
        raise ApiError("invalid_cursor", message, status=422, fields={"cursor": [message]})
    site = request.inquiry_site
    items, next_cursor = comments.list_items(
        site, scope=scope, answered=answered, unread=None if not unread else unread == "1",
        query=query, cursor=cursor or None,
    )
    return {"items": items, "next_cursor": next_cursor, "unread_total": comments.unread_count(site)}


@router.get("/sites/{site_id}/inquiries/comments/{ref}/")
def comment_detail(request, site_id: str, ref: str):
    hit("inq-read", request.account.pk, limit=api_owner.READ_LIMIT, window=api_owner.WINDOW)
    return _done(lambda: comments.detail(request.inquiry_site, ref))


@router.post("/sites/{site_id}/inquiries/comments/{ref}/read/")
def comment_read(request, site_id: str, ref: str):
    hit("inq-mark", request.account.pk, limit=api_owner.READ_LIMIT, window=api_owner.WINDOW)
    return _done(lambda: comments.mark_read(request.inquiry_site, ref))


@router.post("/sites/{site_id}/inquiries/comments/{ref}/reply/")
def comment_reply(request, site_id: str, ref: str, payload: ReplyIn):
    hit("inq-reply", request.account.pk, limit=api_owner.REPLY_LIMIT, window=api_owner.WINDOW)
    text = _text(payload, comments.MAX_PUBLIC)
    return _done(lambda: comments.public_reply(request.inquiry_site, request.account, ref, text, payload.idempotency_key))


@router.post("/sites/{site_id}/inquiries/comments/{ref}/private-reply/")
def comment_private_reply(request, site_id: str, ref: str, payload: ReplyIn):
    hit("inq-reply", request.account.pk, limit=api_owner.REPLY_LIMIT, window=api_owner.WINDOW)
    text = _text(payload, comments.MAX_PRIVATE)
    return _done(lambda: comments.private_reply(request.inquiry_site, request.account, ref, text, payload.idempotency_key))


@router.post("/sites/{site_id}/inquiries/comments/{ref}/hide/")
def comment_hide(request, site_id: str, ref: str):
    hit("inq-reply", request.account.pk, limit=api_owner.REPLY_LIMIT, window=api_owner.WINDOW)
    return _done(lambda: comments.hide(request.inquiry_site, request.account, ref))
