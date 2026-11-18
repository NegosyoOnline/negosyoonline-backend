"""Facebook Page comments in the Inbox (task z8uq9mg9pe): the feed webhook, storage and the owner's
manual actions (public reply, private reply, hide). Every query carries the site (or the connection
that belongs to one site). No token, Meta body or URL is logged or returned; a failure is a fixed
outcome code (apps/inquiries/outcomes.py). Every Graph call is in messenger.py, where the Meta
sources for the facts used here are recorded.

Comment text and author names are personal and untrusted: stored as plain text, kept
COMMENT_RETENTION (apps/inquiries/retention.py), erased at once by a `remove` event.
"""
import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.inquiries import comment_automation, crypto, limiter, messenger, outcomes
from apps.inquiries.models import (
    InquiryComment, InquiryCommentAction, InquiryCommentSettings, InquiryConnection, InquiryPost,
)

logger = logging.getLogger(__name__)

PRIVATE_WINDOW = timedelta(days=7)  # Meta: a private reply within 7 days of the comment
MAX_TEXT = 2000
MAX_PUBLIC, MAX_PRIVATE = 500, 1000
LIST_LIMIT = 30
SEARCH_MIN, SEARCH_MAX = 2, 80
REPLIES_LIMIT, ACTIONS_LIMIT = 50, 20
ANONYMOUS = "Facebook user"
ID_RE = re.compile(r"^[0-9_]{1,64}$")
PERMALINK_RE = re.compile(r"^https://www\.facebook\.com/[A-Za-z0-9_.\-/]{1,200}$")
POST_ID_RE = re.compile(r"^[0-9]{1,30}_[0-9]{1,30}$")
REF_RE = re.compile(r"^cm_[0-9a-f]{24}$")
CURSOR_RE = re.compile(r"^[0-9]{1,20}_[0-9]{1,18}$")
EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

STORED, UPDATED, REMOVED, HIDDEN, DUPLICATE, DROPPED = "stored", "updated", "removed", "hidden", "duplicate", "dropped"
SUMMARY_KEYS = (STORED, UPDATED, REMOVED, HIDDEN, DUPLICATE, DROPPED)
ADD_VERBS, EDIT_VERBS, REMOVE_VERBS, HIDE_VERBS = ("add",), ("edited", "edit"), ("remove", "delete"), ("hide", "unhide")


class Refusal(Exception):
    """A precondition failed before any call to Meta. `code` is a fixed word the route turns into a 409."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# -- readiness ----------------------------------------------------------------------------------

def settings_of(site):
    return InquiryCommentSettings.objects.filter(site=site).first()


def readiness(site):
    """(connection, reason): reason is "" when comments work for this site's Page now."""
    connection = InquiryConnection.objects.filter(site=site, provider=messenger.PROVIDER).first()
    if connection is None or connection.status in (InquiryConnection.UNSET, InquiryConnection.DISABLED) \
            or not connection.external_page_id or not messenger.configured():
        return connection, "not_connected"
    if connection.status == InquiryConnection.INVALID or not connection.token_encrypted:
        return connection, "reconnect"
    row = settings_of(site)
    if row is None or not row.feed_subscribed or row.page_id != connection.external_page_id:
        return connection, "permission_missing"
    return connection, ""


def status_out(site):
    connection, reason = readiness(site)
    shown = connection is not None and connection.status == InquiryConnection.ACTIVE
    return {
        "enabled": True, "connected": bool(connection and connection.external_page_id
                                           and connection.status in (InquiryConnection.ACTIVE, InquiryConnection.INVALID)),
        "page_name": (connection.label or None) if shown else None, "ready": reason == "", "reason": reason,
    }


def mark_subscribed(site, connection, subscribed):
    """Remember whether the Page's `feed` field was subscribed with the comments permissions."""
    row, _ = InquiryCommentSettings.objects.get_or_create(site=site)
    row.feed_subscribed = bool(subscribed)
    row.page_id = connection.external_page_id if subscribed else ""
    row.subscribed_at = timezone.now() if subscribed else None
    row.save()


# -- the feed webhook ---------------------------------------------------------------------------

def _when(value, now):
    """Meta's created_time: unix seconds (as the webhook sends it) or an ISO text. Never in the future."""
    at = None
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            at = datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
        elif isinstance(value, str):
            at = datetime.fromisoformat(value.replace("Z", "+00:00"))
            at = at if at.tzinfo else at.replace(tzinfo=dt_timezone.utc)
    except (ValueError, OverflowError, OSError):
        at = None
    return min(at, now) if at else now


def _text(value, limit):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _post(connection, post_id, info):
    """The stored post of a comment: id, a verified permalink pattern and a short caption only."""
    info = info if isinstance(info, dict) else {}
    link = info.get("permalink_url")
    if not (isinstance(link, str) and PERMALINK_RE.match(link) and "?" not in link):
        link = f"https://www.facebook.com/{post_id}" if POST_ID_RE.match(post_id) else ""
    caption = _text(info.get("message"), 140)
    # Ads run as unpublished Page posts (not confirmed in Meta's docs: is_published=false is the only sign used).
    is_ad = info.get("is_published") is False
    post = InquiryPost.objects.filter(site=connection.site, external_id=post_id).first()
    if post is None:
        try:
            with transaction.atomic():
                return InquiryPost.objects.create(
                    site=connection.site, connection=connection, external_id=post_id, is_ad=is_ad,
                    permalink=link, caption=caption,
                )
        except IntegrityError:
            post = InquiryPost.objects.get(site=connection.site, external_id=post_id)
    changes = {}
    if caption and caption != post.caption:
        changes["caption"] = caption
    if is_ad and not post.is_ad:
        changes["is_ad"] = True
    if changes:
        InquiryPost.objects.filter(pk=post.pk).update(**changes)
    return post


def cancel_queued(comment):
    """A removed comment cancels the replies still waiting for it."""
    return InquiryCommentAction.objects.filter(
        comment=comment, state=InquiryCommentAction.QUEUED,
    ).update(state=InquiryCommentAction.CANCELLED)


def _remove(comment):
    InquiryComment.objects.filter(pk=comment.pk).update(
        removed=True, text="", author_name="", author_psid="", unread=False,
    )
    InquiryCommentAction.objects.filter(comment=comment).update(text="")  # the owner's private message text
    cancel_queued(comment)


def _store(connection, page_id, value, now):
    """Apply one `feed` comment change. Returns a result word (STORED, UPDATED, REMOVED, HIDDEN, DUPLICATE, DROPPED)."""
    verb = value.get("verb")
    comment_id = str(value.get("comment_id") or "")
    post_id = str(value.get("post_id") or "")
    if not (ID_RE.match(comment_id) and ID_RE.match(post_id) and isinstance(verb, str)):
        return DROPPED
    site = connection.site
    existing = InquiryComment.objects.filter(site=site, external_id=comment_id).first()
    if verb in REMOVE_VERBS:
        if existing is None:  # a tombstone, so a late retry of the same comment is not stored
            post = _post(connection, post_id, value.get("post"))
            InquiryComment.objects.create(
                site=site, connection=connection, post=post, external_id=comment_id, removed=True, unread=False,
                parent_external_id=post_id, created_at=_when(value.get("created_time"), now),
            )
            return REMOVED
        if not existing.removed:
            _remove(existing)
        return REMOVED
    if verb in HIDE_VERBS:
        if existing is None or existing.removed:
            return DROPPED
        InquiryComment.objects.filter(pk=existing.pk).update(is_hidden=(verb == "hide"))
        return HIDDEN
    if verb not in ADD_VERBS + EDIT_VERBS:
        return DROPPED
    sender = value.get("from") if isinstance(value.get("from"), dict) else {}
    author_id = str(sender.get("id") or "")[:64]
    author_id = author_id if re.match(r"^[0-9]{1,64}$", author_id) else ""
    name = _text(sender.get("name"), 120)
    text = _text(value.get("message"), MAX_TEXT) if isinstance(value.get("message"), str) else ""
    if existing is not None:
        if existing.removed or verb in ADD_VERBS:
            return DUPLICATE  # Meta retries: the comment id is the idempotency key; edits never revive a removed one
        InquiryComment.objects.filter(pk=existing.pk).update(text=text, **({"author_name": name} if name else {}))
        return UPDATED
    parent = str(value.get("parent_id") or "")
    parent = parent if ID_RE.match(parent) else post_id
    is_page = bool(author_id) and author_id == page_id
    at = _when(value.get("created_time"), now)
    try:
        with transaction.atomic():
            post = _post(connection, post_id, value.get("post"))
            created = InquiryComment.objects.create(
                site=site, connection=connection, post=post, external_id=comment_id, parent_external_id=parent,
                author_psid="" if is_page else author_id, author_name=name, is_page=is_page, text=text,
                unread=not is_page, created_at=at, received_at=now,
            )
            if is_page and parent != post_id:  # the Page answered under a comment (here or on Facebook itself)
                InquiryComment.objects.filter(
                    site=site, external_id=parent, public_reply_at__isnull=True,
                ).update(public_reply_at=at)
    except IntegrityError:  # the same comment arrived twice at once
        return DUPLICATE
    if not is_page:
        comment_automation.schedule(created.pk)  # a Celery task evaluates the auto-reply rules (C2)
    return STORED


def receive(connection, page_id, changes, now):
    """Store the `feed` comment changes of one signed webhook entry (the connection is the one site the
    Page routes to). Other fields and items are ignored. One warning line carries fixed counts only
    (never an id, a name or text); a failure logs its class name."""
    counts = dict.fromkeys(SUMMARY_KEYS, 0)
    seen = False
    for change in changes:
        value = change.get("value") if change.get("field") == messenger.FEED_FIELD else None
        if not isinstance(value, dict) or value.get("item") != "comment":
            continue
        seen = True
        try:
            counts[_store(connection, page_id, value, now)] += 1
        except Exception as error:  # noqa: BLE001 - one bad change must not lose the others
            logger.error("Comment change not stored (%s)", type(error).__name__)
            counts[DROPPED] += 1
    if seen:
        logger.warning("Comments webhook summary %s", ",".join(f"{k}:{v}" for k, v in counts.items()))
    return counts


# -- the owner's list and detail --------------------------------------------------------------

def valid_query(text):
    text = (text or "").strip()
    return text if SEARCH_MIN <= len(text) <= SEARCH_MAX else None


def cursor_of(comment):
    return f"{(comment.created_at - EPOCH) // timedelta(microseconds=1)}_{comment.pk}"


def _after(rows, cursor):
    stamp, _, pk = cursor.partition("_")
    at = EPOCH + timedelta(microseconds=int(stamp))
    return rows.filter(Q(created_at__lt=at) | Q(created_at=at, pk__lt=int(pk)))


def visible(site):
    """The comments the owner sees: this site's, from other people, not deleted."""
    return InquiryComment.objects.filter(site=site, removed=False, is_page=False)


def unread_count(site):
    return visible(site).filter(unread=True).count()


def _reasons(comment, base, now):
    """(public, private, hide) reasons: "" means available."""
    if comment.removed:
        base = "removed"
    elif comment.is_page:
        base = "own_comment"
    private = base
    if not private:
        if comment.private_reply_at is not None:
            private = "already_sent"
        elif now > comment.created_at + PRIVATE_WINDOW:
            private = "window_closed"
    hide = base or ("already_hidden" if comment.is_hidden else "")
    return base, private, hide


def item_out(comment, base_reason, now=None):
    now = now or timezone.now()
    public, private, hide = _reasons(comment, base_reason, now)
    post = comment.post
    own = comment.is_page
    return {
        "ref": comment.ref,
        "author_name": comment.author_name or ANONYMOUS,
        "text": comment.text,
        "created_at": comment.created_at.isoformat(),
        "unread": comment.unread,
        "answered": comment.public_reply_at is not None or comment.private_reply_at is not None,
        "hidden": comment.is_hidden,
        "is_reply": comment.parent_external_id not in ("", post.external_id),
        "post": {"id": post.external_id, "is_ad": post.is_ad, "permalink": post.permalink or None, "caption": post.caption},
        "public_reply": {
            "available": not public, "reason": public,
            "replied_at": comment.public_reply_at.isoformat() if comment.public_reply_at else None,
        },
        "private_reply": {
            "available": not private, "reason": private,
            "replied_at": comment.private_reply_at.isoformat() if comment.private_reply_at else None,
            "expires_at": None if own else (comment.created_at + PRIVATE_WINDOW).isoformat(),
        },
        "hide": {"available": not hide, "reason": hide},
        "auto_reply": comment_automation.auto_reply_of(comment),
    }


def list_items(site, *, scope="all", answered="all", unread=None, query=None, cursor=None, limit=None):
    """(items, next_cursor): the site's comments, newest first. Filters are already validated."""
    limit = limit or LIST_LIMIT
    rows = visible(site).select_related("post").order_by("-created_at", "-pk")
    if scope == "ad":
        rows = rows.filter(post__is_ad=True)
    elif scope == "post":
        rows = rows.filter(post__is_ad=False)
    done = Q(public_reply_at__isnull=False) | Q(private_reply_at__isnull=False)
    if answered == "answered":
        rows = rows.filter(done)
    elif answered == "unanswered":
        rows = rows.exclude(done)
    if unread is not None:
        rows = rows.filter(unread=unread)
    if query:
        rows = rows.filter(Q(author_name__icontains=query) | Q(text__icontains=query) | Q(post__caption__icontains=query))
    if cursor:
        rows = _after(rows, cursor)
    rows = list(rows[: limit + 1])
    more, rows = len(rows) > limit, rows[:limit]
    _, reason = readiness(site)
    now = timezone.now()
    comment_automation.prefetch(rows)
    return [item_out(c, reason, now) for c in rows], (cursor_of(rows[-1]) if more else None)


def find(site, ref, *, removed=False):
    """This site's comment for `ref`, or None. The site is part of the lookup."""
    if not REF_RE.match(ref or ""):
        return None
    rows = InquiryComment.objects.filter(site=site, ref=ref).select_related("post")
    return rows.first() if removed else rows.filter(removed=False).first()


def detail(site, ref):
    comment = find(site, ref)
    if comment is None:
        return None
    _, reason = readiness(site)
    out = item_out(comment, reason)
    children = InquiryComment.objects.filter(
        site=site, post=comment.post, parent_external_id=comment.external_id, removed=False,
    ).order_by("created_at", "pk")[:REPLIES_LIMIT]
    items = [
        (c.created_at, {"ref": c.ref, "kind": "comment", "author_name": c.author_name or ANONYMOUS,
                        "is_page": c.is_page, "text": c.text, "created_at": c.created_at.isoformat()})
        for c in children
    ]
    sent = InquiryCommentAction.objects.filter(
        site=site, comment=comment, kind=InquiryCommentAction.PRIVATE, state=InquiryCommentAction.SENT,
    ).exclude(text="")
    label = ""
    connection = InquiryConnection.objects.filter(site=site, provider=messenger.PROVIDER).first()
    if connection is not None:
        label = connection.label or ""
    items += [
        (a.created_at, {"ref": "", "kind": "private", "author_name": label, "is_page": True, "text": a.text,
                        "created_at": a.created_at.isoformat()})
        for a in sent
    ]
    items.sort(key=lambda pair: pair[0])
    out["replies"] = [item for _, item in items[:REPLIES_LIMIT]]
    out["actions"] = [action_out(a) for a in InquiryCommentAction.objects.filter(site=site, comment=comment)[:ACTIONS_LIMIT]]
    return out


def action_out(action):
    return {"kind": action.kind, "source": action.source, "state": action.state,
            "error_code": action.error_code, "created_at": action.created_at.isoformat(),
            "skip_reason": comment_automation.skip_reason_of(action)}


def mark_read(site, ref):
    comment = find(site, ref)
    if comment is None:
        return None
    if comment.unread:
        InquiryComment.objects.filter(pk=comment.pk).update(unread=False)
        comment.unread = False
    _, reason = readiness(site)
    return item_out(comment, reason)


# -- the owner's actions ------------------------------------------------------------------------

def _earlier(action, comment, kind):
    if action.comment_id != comment.pk or action.kind != kind:
        raise Refusal("conflict")
    return _result(action, comment)


def _result(action, comment):
    comment = InquiryComment.objects.select_related("post").get(pk=comment.pk)
    _, reason = readiness(comment.site)
    return {
        "state": action.state, "error_code": action.error_code, "created_at": action.created_at.isoformat(),
        "comment": item_out(comment, reason),
    }


def _claim(site, account, comment, kind, key, length):
    """Create the audit row before the call, so a repeat never sends twice. Returns (row, None), or
    (None, existing row) when the key was used meanwhile. A second private reply is refused."""
    try:
        with transaction.atomic():
            return InquiryCommentAction.objects.create(
                site=site, comment=comment, kind=kind, source=InquiryCommentAction.MANUAL, author=account,
                state=InquiryCommentAction.SENDING, idempotency_key=key, body_length=length,
            ), None
    except IntegrityError:
        existing = InquiryCommentAction.objects.filter(site=site, idempotency_key=key).first()
        if existing is not None:
            return None, existing
        raise Refusal("already_sent") from None  # the one-private-reply constraint


def _store_page_reply(connection, comment, meta_id, text, now):
    """The Page's own public reply, stored at once so the thread shows it after a reload. The webhook
    echo of the same id then counts as a duplicate (or, if it came first, this insert is skipped)."""
    try:
        with transaction.atomic():
            InquiryComment.objects.create(
                site=comment.site, connection=connection, post=comment.post, external_id=meta_id,
                parent_external_id=comment.external_id, is_page=True, author_psid="",
                author_name=(connection.label or "")[:120], text=text[:MAX_TEXT], unread=False,
                created_at=now, received_at=now,
            )
    except IntegrityError:
        pass


def _perform(site, account, ref, kind, key, text):
    comment = find(site, ref, removed=True)
    if comment is None:
        return None
    if key:
        existing = InquiryCommentAction.objects.filter(site=site, idempotency_key=key).first()
        if existing is not None:
            return _earlier(existing, comment, kind)
    connection, reason = readiness(site)
    public, private, hide = _reasons(comment, reason, timezone.now())
    refused = {InquiryCommentAction.PUBLIC: public, InquiryCommentAction.PRIVATE: private, InquiryCommentAction.HIDE: hide}[kind]
    if refused == "removed":
        raise Refusal("comment_removed")
    if refused:
        raise Refusal(refused)
    if not limiter.acquire(connection.pk):
        row, earlier = _claim(site, account, comment, kind, key, len(text))
        if row is None:
            return _earlier(earlier, comment, kind)
        row.state, row.error_code = InquiryCommentAction.BLOCKED_RATE, outcomes.RATE_LIMITED
        row.save(update_fields=["state", "error_code"])
        return _result(row, comment)
    row, earlier = _claim(site, account, comment, kind, key, len(text))
    if row is None:
        return _earlier(earlier, comment, kind)
    try:
        token = crypto.decrypt(connection.token_encrypted)
    except crypto.TokenKeyError:
        row.state, row.error_code = InquiryCommentAction.FAILED, outcomes.NOT_CONFIGURED
        row.save(update_fields=["state", "error_code"])
        return _result(row, comment)
    if kind == InquiryCommentAction.PUBLIC:
        result = messenger.reply_comment(token, comment.external_id, text)
    elif kind == InquiryCommentAction.PRIVATE:
        result = messenger.private_reply(connection.external_page_id, token, comment.external_id, text)
    else:
        result = messenger.hide_comment(token, comment.external_id)
    now = timezone.now()
    if result.ok:
        row.state, row.error_code = InquiryCommentAction.SENT, ""
        row.meta_id = result.value if isinstance(result.value, str) else ""
        changes = {InquiryCommentAction.PUBLIC: {"public_reply_at": now, "unread": False},
                   InquiryCommentAction.PRIVATE: {"private_reply_at": now, "unread": False},
                   InquiryCommentAction.HIDE: {"is_hidden": True}}[kind]
        InquiryComment.objects.filter(pk=comment.pk).update(**changes)
        if kind == InquiryCommentAction.PRIVATE:
            row.text = text[:MAX_PRIVATE]
        elif kind == InquiryCommentAction.PUBLIC and ID_RE.match(row.meta_id):
            _store_page_reply(connection, comment, row.meta_id, text, now)
    else:
        row.state = InquiryCommentAction.BLOCKED_RATE if result.code == outcomes.RATE_LIMITED else InquiryCommentAction.FAILED
        row.error_code = result.code
        comment_automation.note_manual(site, result.code)  # a Meta rate-limit/spam error pauses auto-replies (C2)
        if result.code in outcomes.CREDENTIAL_FAILURES:
            InquiryConnection.objects.filter(pk=connection.pk).update(
                status=InquiryConnection.INVALID, last_test_at=now, last_test_outcome=result.code,
            )
    row.save(update_fields=["state", "error_code", "meta_id", "text"])
    return _result(row, comment)


def public_reply(site, account, ref, text, key):
    """Reply under the comment as the Page. Returns the result dict, None for an unknown ref; raises Refusal."""
    return _perform(site, account, ref, InquiryCommentAction.PUBLIC, key, text)


def private_reply(site, account, ref, text, key):
    return _perform(site, account, ref, InquiryCommentAction.PRIVATE, key, text)


def hide(site, account, ref):
    return _perform(site, account, ref, InquiryCommentAction.HIDE, "", "")
