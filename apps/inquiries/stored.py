"""The stored channels (website chat now, email in a later PR): list, thread, reply, close and
reopen, mark read, and the per-site channel settings.

Every function takes the Site the router already resolved for the signed-in owner and reads only
that site's rows. A ref is the conversation's server-made random `external_id` (`wc_...`); it is
looked up together with the site, so another site's ref is a ref that does not exist. A reply's
text is stored in InquiryMessage (there is no provider to hold it); the InquiryReply audit row
keeps only its length and a code. The Pancake path (services.py) is not touched by any of this."""
import re
import secrets
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.inquiries import outcomes
from apps.inquiries.models import (
    InquiryChannelSettings, InquiryConversation, InquiryMessage, InquiryReply,
)

LIST_LIMIT = 30
THREAD_LIMIT = 200  # the latest messages of a thread
RECENT_REPLIES = 20
SNIPPET = 100
ANONYMOUS = "Website visitor"
MAX_PAUSE = timedelta(days=90)

CLOSED_CODE = "conversation_closed"
CONFLICT_CODE = "conflict"


def is_stored_ref(ref):
    return ref.startswith(tuple(InquiryConversation.REF_PREFIX.values()))


def new_ref(provider):
    return InquiryConversation.REF_PREFIX[provider] + secrets.token_hex(12)


def find(site, ref):
    """This site's stored conversation for `ref`, or None. The site is part of the lookup."""
    if not is_stored_ref(ref):
        return None
    return InquiryConversation.objects.filter(
        site=site, provider__in=InquiryConversation.STORED_PROVIDERS, external_id=ref, stores_history=True,
    ).first()


def _name(conversation):
    return conversation.contact_name or conversation.display_name or ANONYMOUS


def _snippet(text):
    return " ".join(text.split())[:SNIPPET]


EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
STORED_CURSOR_RE = re.compile(r"^(?:[0-9]{1,20}|n)_[0-9]{1,18}$")


def cursor_of(conversation):
    """The keyset position of a stored conversation: its last message time (microseconds since the
    epoch, or `n` for none) and its id. Opaque to the dashboard."""
    at = conversation.last_message_at
    stamp = (at - EPOCH) // timedelta(microseconds=1) if at else "n"
    return f"{stamp}_{conversation.pk}"


def _after(rows, cursor):
    """The rows that sort after `cursor` in (last_message_at desc, nulls last; pk desc) order."""
    stamp, _, pk = cursor.partition("_")
    pk = int(pk)
    if stamp == "n":
        return rows.filter(last_message_at__isnull=True, pk__lt=pk)
    at = EPOCH + timedelta(microseconds=int(stamp))
    return rows.filter(
        Q(last_message_at__lt=at) | Q(last_message_at=at, pk__lt=pk) | Q(last_message_at__isnull=True)
    )


def list_items(site, limit=LIST_LIMIT, after=None):
    """(items, next_cursor): the site's stored conversations, latest first, in the shape of the
    Pancake list items. `after` is a previous call's next_cursor; next_cursor is None on the last page."""
    rows = InquiryConversation.objects.filter(
        site=site, provider__in=InquiryConversation.STORED_PROVIDERS, stores_history=True,
    ).order_by(F("last_message_at").desc(nulls_last=True), "-pk")
    if after:
        rows = _after(rows, after)
    rows = list(rows[: limit + 1])
    more, rows = len(rows) > limit, rows[:limit]
    items = []
    for conversation in rows:
        last = InquiryMessage.objects.filter(conversation=conversation).order_by("-created_at", "-pk").first()
        items.append({
            "ref": conversation.external_id,
            "display_name": _name(conversation),
            "snippet": _snippet(last.body) if last else "",
            "updated_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
            "channel": conversation.provider,
            "source": "stored",
            "status": conversation.status,
            "unread": conversation.unread,
        })
    return items, (cursor_of(rows[-1]) if more else None)


def has_any(site):
    return InquiryConversation.objects.filter(
        site=site, provider__in=InquiryConversation.STORED_PROVIDERS, stores_history=True,
    ).exists()


def _reply_out(reply):
    return {"state": reply.state, "error_code": reply.error_code, "created_at": reply.created_at.isoformat()}


def thread(site, ref):
    """The thread dict, or None when this site has no such conversation."""
    conversation = find(site, ref)
    if conversation is None:
        return None
    latest = list(
        InquiryMessage.objects.filter(conversation=conversation).order_by("-created_at", "-pk")[:THREAD_LIMIT]
    )
    latest.reverse()
    replies = InquiryReply.objects.filter(conversation=conversation)[:RECENT_REPLIES]
    is_open = conversation.status == InquiryConversation.OPEN
    return {
        "ref": conversation.external_id,
        "display_name": _name(conversation),
        "channel": conversation.provider,
        "source": "stored",
        "status": conversation.status,
        "unread": conversation.unread,
        "contact_email": conversation.contact_email,
        "messages": [
            {
                "id": str(m.pk), "direction": m.direction, "sender_kind": m.sender_kind, "text": m.body,
                "attachments": [], "at": m.created_at.isoformat(), "delivery_state": m.delivery_state,
            }
            for m in latest
        ],
        "reply": {"open": is_open, "reason": "" if is_open else "closed"},
        "provider_url": None,
        "next_cursor": None,
        "replies": [_reply_out(r) for r in replies],
    }


def _earlier(existing, ref):
    if existing.conversation.external_id != ref:
        return "idempotency_conflict", None
    return outcomes.OK, _reply_out(existing)


def reply(site, account, ref, text, idempotency_key):
    """Send one owner reply on a stored conversation. Returns (code, dict): NOT_FOUND for a ref
    that is not this site's; CLOSED_CODE for a closed conversation; otherwise the audit row's state.
    For website chat the visitor's widget is the channel, so `sent` means stored for pick-up."""
    conversation = find(site, ref)
    if conversation is None or conversation.provider != InquiryConversation.WEBCHAT:
        return outcomes.NOT_FOUND, None  # email replies arrive with the email channel
    existing = InquiryReply.objects.filter(site=site, idempotency_key=idempotency_key).select_related("conversation").first()
    if existing:
        return _earlier(existing, ref)
    if conversation.status != InquiryConversation.OPEN:
        return CLOSED_CODE, None
    now = timezone.now()
    try:
        with transaction.atomic():
            message = InquiryMessage.objects.create(
                site=site, conversation=conversation, direction=InquiryMessage.OUT,
                sender_kind=InquiryMessage.OWNER, body=text, delivery_state="sent", created_at=now,
            )
            row = InquiryReply.objects.create(
                site=site, conversation=conversation, author=account, idempotency_key=idempotency_key,
                body_length=len(text), state=InquiryReply.SENT, message=message,
            )
            InquiryConversation.objects.filter(pk=conversation.pk).update(last_message_at=now, unread=False)
    except IntegrityError:  # the same key arrived twice at once: the first one wins
        existing = InquiryReply.objects.select_related("conversation").get(site=site, idempotency_key=idempotency_key)
        return _earlier(existing, ref)
    return outcomes.OK, _reply_out(row)


def _summary(conversation):
    return {"ref": conversation.external_id, "status": conversation.status, "unread": conversation.unread}


def set_status(site, ref, status):
    """Close or reopen. Returns (code, summary). Reopening is refused with CONFLICT_CODE when the
    same visitor already has another open conversation."""
    conversation = find(site, ref)
    if conversation is None:
        return outcomes.NOT_FOUND, None
    if conversation.status != status:
        conversation.status = status
        try:
            with transaction.atomic():
                conversation.save(update_fields=["status"])
        except IntegrityError:
            return CONFLICT_CODE, None
    return outcomes.OK, _summary(conversation)


def mark_read(site, ref):
    conversation = find(site, ref)
    if conversation is None:
        return outcomes.NOT_FOUND, None
    if conversation.unread:
        conversation.unread = False
        conversation.save(update_fields=["unread"])
    return outcomes.OK, _summary(conversation)


# -- the per-site channel settings -----------------------------------------------------------

def channel_settings(site):
    """The site's settings row, without creating one."""
    return InquiryChannelSettings.objects.filter(site=site).first()


def settings_out(row):
    now = timezone.now()
    paused = bool(row and row.paused_until and row.paused_until > now)
    return {
        "webchat_enabled": bool(row and row.webchat_enabled),
        "paused_until": row.paused_until.isoformat() if paused else None,
        "chat_limit_reached": bool(row and row.chat_limit_at and row.chat_limit_at > now - timedelta(hours=24)),
    }


def update_settings(site, *, webchat_enabled=None, paused_until=None, set_pause=False):
    """Save the owner's choices. `set_pause` says paused_until was part of the request, so that
    None can clear it. Returns the row."""
    row, _ = InquiryChannelSettings.objects.get_or_create(site=site)
    if webchat_enabled is not None:
        row.webchat_enabled = webchat_enabled
        if webchat_enabled:
            row.chat_limit_at = None
    if set_pause:
        row.paused_until = paused_until
    row.save()
    return row
