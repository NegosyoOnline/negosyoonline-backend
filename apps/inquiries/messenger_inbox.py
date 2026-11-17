"""The Messenger inbox's own logic: connections, incoming events, the reply, and Meta's two
callbacks. Every query carries the site (or the connection that belongs to one site), and no
token, Meta body or URL is logged or returned.

Customer messages are stored in InquiryMessage like the other stored channels (kept
MESSAGE_RETENTION, apps/inquiries/retention.py); an attachment is recorded as its type only."""
import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.inquiries import crypto, limiter, live, messenger, outcomes, profile
from apps.inquiries.models import InquiryConnection, InquiryConversation, InquiryMessage, InquiryReply

logger = logging.getLogger(__name__)

PROVIDER = InquiryConversation.MESSENGER
WINDOW = timedelta(hours=24)  # Meta's standard messaging window (D2: no message tags)
PICK_SECONDS = 600
MAX_BODY = 4000
TYPE_RE = re.compile(r"^[a-z_]{1,20}$")


# -- the connection ---------------------------------------------------------------------------

def connection_of(site):
    return InquiryConnection.objects.filter(site=site, provider=PROVIDER).first()


def pick_key(site, account):
    return f"inq:fm:pick:{site.pk}:{account.pk}"


def connection_out(site, account):
    row = connection_of(site)
    status = row.status if row and row.status in (InquiryConnection.ACTIVE, InquiryConnection.INVALID) else "unset"
    shown = status != "unset"
    return {
        "status": status,
        "page_id": row.external_page_id if shown else None,
        "page_name": (row.label or None) if shown else None,
        "connected_at": row.updated_at.isoformat() if shown else None,
        "pick_pending": cache.get(pick_key(site, account)) is not None,
    }


def unsubscribe_row(row):
    """Best effort: take the app off the row's Page with the row's decrypted token. Call it with the row
    instance as read before the stored row is cleared or overwritten (the instance keeps the old values). No token, no key or no Meta app settings: nothing is sent."""
    if not (row.external_page_id and row.token_encrypted and row.status == InquiryConnection.ACTIVE) \
            or not messenger.configured():
        return
    try:
        token = crypto.decrypt(row.token_encrypted)
    except crypto.TokenKeyError:
        return
    messenger.unsubscribe_page(row.external_page_id, token)


def disconnect(site):
    """Unsubscribe the Page (best effort) and delete the token. The conversations and their
    messages stay (they keep their own retention); the Page is free for another site."""
    row = connection_of(site)
    if row is None:
        return
    unsubscribe_row(row)
    clear_connection(row, InquiryConnection.UNSET)


def clear_connection(row, status):
    row.token_encrypted, row.status = "", status
    row.external_page_id = row.label = row.meta_user_id = ""
    row.save(update_fields=["token_encrypted", "status", "external_page_id", "label", "meta_user_id", "updated_at"])


# -- incoming events --------------------------------------------------------------------------

def window_of(conversation, now=None):
    """{"open", "closes_at"}: Meta lets a Page reply 24 hours after the customer's last message."""
    now = now or timezone.now()
    last = conversation.last_inbound_at
    if last is None:
        return {"open": False, "closes_at": None}
    closes = last + WINDOW
    return {"open": closes > now, "closes_at": closes.isoformat()}


def _when(value, now):
    try:
        at = datetime.fromtimestamp(int(value) / 1000, tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return now
    return min(at, now)


def _attachment_types(message):
    types = []
    for item in message.get("attachments") or []:
        kind = item.get("type") if isinstance(item, dict) else None
        types.append(kind if isinstance(kind, str) and TYPE_RE.match(kind) else "file")
    return ",".join(types)[:60]


def _conversation(connection, psid, now, page_id=""):
    found = InquiryConversation.objects.filter(connection=connection, psid=psid).first()
    if found:
        return found
    try:
        with transaction.atomic():
            return InquiryConversation.objects.create(
                site=connection.site, connection=connection, provider=PROVIDER, psid=psid, page_id=page_id,
                external_id=_new_ref(), stores_history=True, status=InquiryConversation.OPEN,
                last_message_at=now,
            )
    except IntegrityError:  # two events for a new customer at once: the first one wins
        return InquiryConversation.objects.get(connection=connection, psid=psid)


def _new_ref():
    from apps.inquiries import stored
    return stored.new_ref(PROVIDER)


STORED, NOT_ADDRESSED, EMPTY, DUPLICATE = "stored", "not_addressed", "empty", "duplicate"
ENTRY_KEYS = ("messaging", "standby", "changes", "other")
EVENT_KINDS = ("message", "echo", "delivery", "read", "postback", "pass_thread_control", "take_thread_control", "other")
DROPS = ("no_connection", NOT_ADDRESSED, EMPTY, DUPLICATE)


def _kind(event):
    """The fixed kind code of one webhook event (never any of its content)."""
    message = event.get("message")
    if isinstance(message, dict):
        return "echo" if message.get("is_echo") else "message"
    for kind in ("delivery", "read", "postback", "pass_thread_control", "take_thread_control"):
        if kind in event:
            return kind
    return "other"


def _store_event(connection, page_id, event, now):
    """Store one messaging event. Returns STORED, a drop reason (NOT_ADDRESSED, EMPTY, DUPLICATE), or
    None for an event that carries no message (postbacks, deliveries and reads)."""
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    mid = str(message.get("mid") or "")[:128]
    sender = str((event.get("sender") or {}).get("id") or "")[:64]
    recipient = str((event.get("recipient") or {}).get("id") or "")[:64]
    echo = bool(message.get("is_echo"))
    # An echo is the Page's own message: the customer is the recipient. Anything else must be
    # addressed to this Page.
    psid, valid = (recipient, sender == page_id) if echo else (sender, recipient == page_id)
    text = str(message.get("text") or "")[:MAX_BODY]
    attachments = _attachment_types(message)
    if not (mid and psid and valid):
        return NOT_ADDRESSED
    if not (text or attachments):
        return EMPTY
    if InquiryMessage.objects.filter(site=connection.site, external_id=mid).exists():
        return DUPLICATE  # Meta retries: the message id is the idempotency key
    at = _when(event.get("timestamp"), now)
    try:
        with transaction.atomic():
            conversation = _conversation(connection, psid, now, page_id)
            InquiryMessage.objects.create(
                site=connection.site, conversation=conversation, external_id=mid, body=text,
                attachment_types=attachments, created_at=at,
                direction=InquiryMessage.OUT if echo else InquiryMessage.IN,
                sender_kind=InquiryMessage.OWNER if echo else InquiryMessage.CUSTOMER,
                delivery_state="sent" if echo else "",
            )
            changes = {"last_message_at": max(conversation.last_message_at or at, at)}
            if not echo:
                changes.update(
                    last_inbound_at=max(conversation.last_inbound_at or at, at), unread=True,
                    status=InquiryConversation.OPEN,
                )
            InquiryConversation.objects.filter(pk=conversation.pk).update(**changes)
            profile.schedule(conversation.pk)  # the customer's name and photo, after commit (U1)
            live.publish(connection.site, conversation.external_id, live.KIND_MESSAGE, changes["last_message_at"])
    except IntegrityError:  # the same mid arrived twice at once
        return DUPLICATE
    return STORED


# -- delivery and read receipts (U2) ----------------------------------------------------------

# An outgoing Messenger message only moves forward: sent -> delivered -> seen. `failed` and the rest never change.
STATE_BELOW = {"delivered": ("sent",), "seen": ("sent", "delivered")}
MAX_MIDS = 100


def _watermark(value, now):
    """The receipt's watermark as a time, or None when it is missing or not a number. Never in the future."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return min(datetime.fromtimestamp(int(value) / 1000, tz=dt_timezone.utc), now)
    except (ValueError, OverflowError, OSError):
        return None


def _publish_status(site, ref):
    """Tell the owner's open Inbox that a message's state changed (`message_status`, after commit,
    ref only). live.publish swallows a Redis failure; the guard keeps a webhook safe regardless."""
    try:
        live.publish(site, ref, live.KIND_STATUS, event=live.MESSAGE_STATUS)
    except Exception as error:  # noqa: BLE001 - a live-push failure never fails a webhook
        logger.warning("Messenger status not published (%s)", type(error).__name__)


def _status_event(connection, page_id, event, now):
    """Apply one `delivery` or `read` event to the Page's own outgoing messages in that customer's
    conversation. Returns how many messages moved forward; other events and unknown customers: 0."""
    sender = str((event.get("sender") or {}).get("id") or "")[:64]
    recipient = str((event.get("recipient") or {}).get("id") or "")[:64]
    delivery, read = event.get("delivery"), event.get("read")
    if not sender or recipient != page_id or not (isinstance(delivery, dict) or isinstance(read, dict)):
        return 0
    conversation = InquiryConversation.objects.filter(connection=connection, psid=sender).first()
    if conversation is None:
        return 0
    target = "seen" if isinstance(read, dict) else "delivered"
    receipt = read if isinstance(read, dict) else delivery
    mids = receipt.get("mids") if target == "delivered" else None
    mids = [str(m)[:128] for m in mids[:MAX_MIDS] if isinstance(m, str) and m] if isinstance(mids, list) else []
    watermark = _watermark(receipt.get("watermark"), now)
    match = Q(pk__in=[])
    if mids:
        match |= Q(external_id__in=mids)
    if watermark is not None:
        match |= Q(created_at__lte=watermark)
    moved = InquiryMessage.objects.filter(
        match, conversation=conversation, site=connection.site, direction=InquiryMessage.OUT,
        delivery_state__in=STATE_BELOW[target],
    ).update(delivery_state=target)
    if moved:
        _publish_status(connection.site, conversation.external_id)
    return moved


def _events_of(entry, key):
    events = entry.get(key)
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []


def receive(payload):
    """Store the messaging events of one signed webhook POST. `payload` is the parsed JSON. Unknown
    Pages and malformed events are dropped. Standby events (another app holds the thread) are counted
    and never stored; nothing is sent to Meta. One warning summary line per POST carries fixed codes
    and counts only (no id, page id, customer id, message id or text); a failure logs its class name."""
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return
    now = timezone.now()
    entries = dict.fromkeys(ENTRY_KEYS, 0)
    kinds = dict.fromkeys(EVENT_KINDS, 0)
    dropped = dict.fromkeys(DROPS, 0)
    stored = 0
    raw_entries = payload.get("entry")
    for entry in raw_entries if isinstance(raw_entries, list) else []:
        if not isinstance(entry, dict):
            continue
        for key in entry:
            if key in ("id", "time"):
                continue
            entries[key if key in ("messaging", "standby", "changes") else "other"] += 1
        messaging, standby = _events_of(entry, "messaging"), _events_of(entry, "standby")
        for event in messaging + standby:
            kinds[_kind(event)] += 1
        page_id = str(entry.get("id") or "")[:64]
        connections = list(InquiryConnection.objects.filter(
            provider=PROVIDER, external_page_id=page_id, status=InquiryConnection.ACTIVE,
        ).select_related("site")[:2]) if page_id else []
        if len(connections) != 1:
            dropped["no_connection"] += len(messaging) + len(standby) or 1
            continue
        for event in messaging:
            try:
                if "delivery" in event or "read" in event:
                    _status_event(connections[0], page_id, event, now)
                    continue
                result = _store_event(connections[0], page_id, event, now)
            except Exception as error:  # noqa: BLE001 - one bad event must not lose the others
                logger.error("Messenger event not stored (%s)", type(error).__name__)
                continue
            if result == STORED:
                stored += 1
            elif result in dropped:
                dropped[result] += 1
    logger.warning(
        "Messenger webhook summary entries=%s events=%s stored=%d dropped=%s",
        ",".join(f"{k}:{v}" for k, v in entries.items()), ",".join(f"{k}:{v}" for k, v in kinds.items()),
        stored, ",".join(f"{k}:{v}" for k, v in dropped.items()),
    )


# -- the reply --------------------------------------------------------------------------------

def reply(site, account, conversation, text, key):
    """Reply on a Messenger conversation. Returns (OK, reply dict). The window is checked from the
    customer's last message before any call to Meta. The audit row holds the outcome code and Meta's
    message id only (the text lives in InquiryMessage)."""
    from apps.inquiries import stored

    now = timezone.now()
    connection = conversation.connection
    blocked = ""
    if not window_of(conversation, now)["open"]:
        blocked = outcomes.WINDOW_CLOSED
    elif connection is None or connection.site_id != site.pk or connection.status != InquiryConnection.ACTIVE \
            or not connection.token_encrypted or not messenger.configured() \
            or conversation.page_id != connection.external_page_id:  # the connection was switched to another Page
        blocked = outcomes.TOKEN_INVALID if connection and connection.status == InquiryConnection.INVALID else outcomes.NOT_CONFIGURED
    if blocked:
        row = _audit(site, account, conversation, key, text, state=(
            InquiryReply.BLOCKED_WINDOW if blocked == outcomes.WINDOW_CLOSED else InquiryReply.FAILED), code=blocked)
        return _done(row)
    if not limiter.acquire(connection.pk):
        row = _audit(site, account, conversation, key, text, state=InquiryReply.BLOCKED_RATE, code=outcomes.RATE_LIMITED)
        return _done(row)
    try:
        token = crypto.decrypt(connection.token_encrypted)
    except crypto.TokenKeyError:
        row = _audit(site, account, conversation, key, text, state=InquiryReply.FAILED, code=outcomes.NOT_CONFIGURED)
        return _done(row)
    # Claim the idempotency key before the call, so a repeat never sends twice.
    row = _audit(site, account, conversation, key, text, state=InquiryReply.SENDING, code="")
    if isinstance(row, tuple):
        return _done(row)
    result = messenger.send_text(connection.external_page_id, token, conversation.psid, text)
    if result.ok:
        row.state, row.provider_message_ref = InquiryReply.SENT, result.value
        row.error_code = ""
    else:
        row.state = InquiryReply.BLOCKED_WINDOW if result.code == outcomes.WINDOW_CLOSED else (
            InquiryReply.BLOCKED_RATE if result.code == outcomes.RATE_LIMITED else InquiryReply.FAILED)
        row.error_code = result.code
        if result.code in outcomes.CREDENTIAL_FAILURES:
            InquiryConnection.objects.filter(pk=connection.pk).update(
                status=InquiryConnection.INVALID, last_test_at=now, last_test_outcome=result.code,
            )
    message = _store_reply(site, conversation, text, row, result, now)
    row.message = message
    row.save(update_fields=["state", "error_code", "provider_message_ref", "message"])
    if result.ok:
        InquiryConversation.objects.filter(pk=conversation.pk).update(last_message_at=now, unread=False)
    live.publish(site, conversation.external_id, live.KIND_MESSAGE, now if result.ok else conversation.last_message_at)
    return outcomes.OK, stored._reply_out(row)


def _done(row):
    from apps.inquiries import stored
    if isinstance(row, tuple):
        return stored._earlier(*row)
    return outcomes.OK, stored._reply_out(row)


def _audit(site, account, conversation, key, text, *, state, code):
    """Create the audit row, or return (existing row, ref) when the key was used meanwhile."""
    try:
        with transaction.atomic():
            return InquiryReply.objects.create(
                site=site, conversation=conversation, author=account, idempotency_key=key,
                body_length=len(text), state=state, error_code=code,
            )
    except IntegrityError:
        existing = InquiryReply.objects.select_related("conversation", "message").get(site=site, idempotency_key=key)
        return existing, conversation.external_id


def _store_reply(site, conversation, text, row, result, now):
    """The owner's message as stored for the thread. When Meta's echo of it already arrived, that
    row is the message (the message id is unique)."""
    mid = result.value if result.ok else ""
    if mid:
        earlier = InquiryMessage.objects.filter(site=site, external_id=mid).first()
        if earlier:
            return earlier
    return InquiryMessage.objects.create(
        site=site, conversation=conversation, direction=InquiryMessage.OUT, sender_kind=InquiryMessage.OWNER,
        body=text, external_id=mid, created_at=now,
        delivery_state="sent" if result.ok else "failed", error_code="" if result.ok else result.code,
    )


# -- Meta's callbacks -------------------------------------------------------------------------

def deauthorize(meta_user_id):
    """The person removed the app: every Page they connected is invalid and its token is gone."""
    rows = list(InquiryConnection.objects.filter(provider=PROVIDER, meta_user_id=meta_user_id))
    for row in rows:
        # No unsubscribe here: the person already removed the app, so Meta has dropped the subscription.
        clear_connection(row, InquiryConnection.INVALID)
    profile.forget(InquiryConversation.objects.filter(
        provider=PROVIDER, connection__in=[r.pk for r in rows]).values_list("pk", flat=True))
    return len(rows)


def erase_user(meta_user_id):
    """Meta's data deletion request for one Facebook user: their connections lose the token and the
    Page, and the Messenger messages and customer fields of those connections are deleted. Reply
    audit rows hold no personal data and stay for their own retention. Only conversations of those
    connections are erased: a customer id is page-scoped and never equals a Meta user id."""
    from apps.inquiries.retention import PERSONAL

    connections = list(InquiryConnection.objects.filter(provider=PROVIDER, meta_user_id=meta_user_id))
    for row in connections:
        unsubscribe_row(row)
    conversations = InquiryConversation.objects.filter(
        provider=PROVIDER, connection__in=[c.pk for c in connections],
    )
    ids = list(conversations.values_list("pk", flat=True))
    with transaction.atomic():
        InquiryMessage.objects.filter(conversation_id__in=ids).delete()
        InquiryConversation.objects.filter(pk__in=ids, replies__isnull=True).delete()
        InquiryConversation.objects.filter(pk__in=ids).update(
            status=InquiryConversation.CLOSED, unread=False, **{f: "" for f in PERSONAL},
        )
        for row in connections:
            clear_connection(row, InquiryConnection.UNSET)
    profile.forget(ids)  # the cached photos go with the rest
    return len(ids)

