"""The stored channels (website chat now, email in a later PR): list, thread, reply, close and
reopen, mark read, and the per-site channel settings.

Every function takes the Site the router already resolved for the signed-in owner and reads only
that site's rows. A ref is the conversation's server-made random `external_id` (`wc_...`); it is
looked up together with the site, so another site's ref is a ref that does not exist. A reply's
text is stored in InquiryMessage (there is no provider to hold it); the InquiryReply audit row
keeps only its length and a code. There is no provider path."""
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.db.models.functions import Concat
from django.db.models import BooleanField, Case, Exists, F, OuterRef, Q, Subquery, Value, When
from django.utils import timezone

from apps.common import throttle
from apps.common.errors import ApiError
from apps.inquiries import live, outcomes, profile
from apps.inquiries.models import (
    InquiryChannelSettings, InquiryConversation, InquiryMessage, InquiryReply,
)

LIST_LIMIT = 30
SEARCH_MIN, SEARCH_MAX = 2, 80  # characters of a search text
THREAD_LIMIT = 200  # the latest messages of a thread
RECENT_REPLIES = 20
SNIPPET = 100
ANONYMOUS = "Website visitor"
MESSENGER_CUSTOMER = "Messenger customer"
SMS_CUSTOMER = "Customer"
MAX_PAUSE = timedelta(days=90)

CLOSED_CODE = "conversation_closed"
CONFLICT_CODE = "conflict"


def providers():
    """The stored providers the owner may see now: Messenger only while its switch is on."""
    from apps.inquiries.switch import messenger_on, sms_ready
    shown = InquiryConversation.STORED_PROVIDERS
    if not messenger_on():
        shown = tuple(p for p in shown if p != InquiryConversation.MESSENGER)
    if not sms_ready():  # texts need their switches and tables (inquiries.0010)
        shown = tuple(p for p in shown if p != InquiryConversation.SMS)
    return shown


def is_stored_ref(ref):
    return ref.startswith(tuple(InquiryConversation.REF_PREFIX.values()))


def new_ref(provider):
    return InquiryConversation.REF_PREFIX[provider] + secrets.token_hex(12)


def find(site, ref):
    """This site's stored conversation for `ref`, or None. The site is part of the lookup."""
    if not is_stored_ref(ref):
        return None
    return InquiryConversation.objects.filter(
        site=site, provider__in=providers(), external_id=ref, stores_history=True,
    ).first()


def _name(conversation, resolved=None):
    """The stored name, else (a text conversation with none) `resolved`, the member's or the
    store's saved contact's name found at read time, else the fixed fallback."""
    fallback = {InquiryConversation.MESSENGER: MESSENGER_CUSTOMER, InquiryConversation.SMS: SMS_CUSTOMER}.get(
        conversation.provider, ANONYMOUS)
    return conversation.contact_name or conversation.display_name or resolved or fallback


def _unnamed_sms_names(site, conversations):
    """{mobile: name} for the unnamed text conversations among `conversations`: one batch lookup."""
    from apps.inquiries import sms_inbox
    return sms_inbox.names_for(site, [
        c.contact_mobile for c in conversations
        if c.provider == InquiryConversation.SMS and not (c.contact_name or c.display_name)
    ])


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


def _attachment_line(attachment_types):
    """The list snippet for an attachment-only message, the words the dash thread uses."""
    first = (attachment_types or "").split(",")[0].strip().lower()
    if not first:
        return ""
    return "[Photo]" if first in ("image", "photo") else "[Attachment]"


def valid_query(text):
    """The search text trimmed, or None when it is not 2 to 80 characters."""
    text = (text or "").strip()
    return text if SEARCH_MIN <= len(text) <= SEARCH_MAX else None


def _matching(rows, text, site=None):
    """The rows whose name, contact name, contact email, contact mobile (not a text conversation's: it is searched by name only) or any message text contains `text`, ignoring
    case. `rows` is already this site's; the message test is tied to the same conversation and site."""
    in_message = InquiryMessage.objects.filter(
        conversation=OuterRef("pk"), site=OuterRef("site"), body__icontains=text,
    )
    named = Q(display_name__icontains=text) | Q(contact_name__icontains=text) | Q(contact_email__icontains=text)
    if site is not None:  # a text conversation with no stored name is named at read time: search that name too
        from apps.inbox.models import Contact
        from apps.members.models import Member
        full = Concat("first_name", Value(" "), "last_name")
        contact_numbers = (
            Contact.objects.filter(owner_kind="site", site=site).annotate(full=full)
            .filter(Q(full__icontains=text)).values("mobile")
        )
        member_numbers = Member.objects.filter(site=site, full_name__icontains=text).values("mobile")
        named |= Q(provider=InquiryConversation.SMS, contact_name="", display_name="") & (
            Q(contact_mobile__in=contact_numbers) | Q(contact_mobile__in=member_numbers))
    return rows.filter(
        named
        | (Q(contact_mobile__icontains=text) & ~Q(provider=InquiryConversation.SMS))
        | Exists(in_message)
    )


def by_channel(rows, channel):
    """`channel` "sms" keeps only text conversations, "nosms" every other channel, None all."""
    if channel == "sms":
        return rows.filter(provider=InquiryConversation.SMS)
    if channel == "nosms":
        return rows.exclude(provider=InquiryConversation.SMS)
    return rows


def unread_count(site, channel=None):
    """How many of the site's visible stored conversations are unread (one count on the site's rows);
    `channel` as in by_channel."""
    return by_channel(InquiryConversation.objects.filter(
        site=site, provider__in=providers(), stores_history=True, unread=True,
    ), channel).count()


FAILED_DELIVERY = ("failed", "bounced")  # a store message in one of these states did not reach the customer


def _with_answered(rows):
    """Annotate `answered`: the conversation's latest message (created_at, then pk) is the store's and
    its delivery did not fail. No messages means unanswered. One subquery, no per-row query."""
    latest = InquiryMessage.objects.filter(conversation=OuterRef("pk")).order_by("-created_at", "-pk")
    return rows.annotate(
        _last_direction=Subquery(latest.values("direction")[:1]),
        _last_delivery=Subquery(latest.values("delivery_state")[:1]),
    ).annotate(answered=Case(
        When(Q(_last_direction=InquiryMessage.OUT) & ~Q(_last_delivery__in=FAILED_DELIVERY), then=Value(True)),
        default=Value(False), output_field=BooleanField(),
    ))


def list_items(site, limit=LIST_LIMIT, after=None, query=None, status=None, unread=None, answered=None,
               channel=None):
    """(items, next_cursor): the site's stored conversations, latest first, in the shape the owner's
    list uses. `after` is a previous call's next_cursor; next_cursor is None on the last page.
    `query` (already validated, see valid_query) keeps only the conversations that match it;
    `status` (open or closed, already validated) keeps only those;
    `unread` (True or False, None for all) keeps only the unread or only the read;
    `answered` (True or False, None for all) keeps only the answered or only the unanswered (see _with_answered)."""
    rows = InquiryConversation.objects.filter(
        site=site, provider__in=providers(), stores_history=True,
    ).order_by(F("last_message_at").desc(nulls_last=True), "-pk")
    rows = by_channel(rows, channel)  # `channel`: "sms", "nosms" or None (see by_channel)
    rows = _with_answered(rows)
    if status:
        rows = rows.filter(status=status)
    if unread is not None:
        rows = rows.filter(unread=unread)
    if answered is not None:
        rows = rows.filter(answered=answered)
    if query:
        rows = _matching(rows, query, site)
    if after:
        rows = _after(rows, after)
    rows = list(rows[: limit + 1])
    more, rows = len(rows) > limit, rows[:limit]
    photos = profile.avatar_urls(c.pk for c in rows if c.provider == InquiryConversation.MESSENGER)
    resolved = _unnamed_sms_names(site, rows)
    items = []
    for conversation in rows:
        last = InquiryMessage.objects.filter(conversation=conversation).order_by("-created_at", "-pk").first()
        items.append({
            "ref": conversation.external_id,
            "display_name": _name(conversation, resolved.get(conversation.contact_mobile)),
            "snippet": (_snippet(last.body) or _attachment_line(last.attachment_types)) if last else "",
            "updated_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
            "channel": conversation.provider,
            "source": "stored",
            "status": conversation.status,
            "unread": conversation.unread,
            "answered": conversation.answered,
            "avatar_url": photos.get(conversation.pk),
        })
    return items, (cursor_of(rows[-1]) if more else None)


def has_any(site):
    return InquiryConversation.objects.filter(
        site=site, provider__in=providers(), stores_history=True,
    ).exists()


def _reply_out(reply):
    out = {"state": reply.state, "error_code": reply.error_code, "created_at": reply.created_at.isoformat()}
    specific = reply.message.error_code if reply.message_id else ""
    if specific:  # an email reply that did not go: the specific code and its fixed text
        out["error_code"] = specific
        out["message"] = outcomes.EMAIL_TEXTS.get(specific, "")
    return out


def thread(site, ref):
    """The thread dict, or None when this site has no such conversation."""
    conversation = find(site, ref)
    if conversation is None:
        return None
    latest = list(
        InquiryMessage.objects.filter(conversation=conversation).order_by("-created_at", "-pk")[:THREAD_LIMIT]
    )
    latest.reverse()
    replies = InquiryReply.objects.filter(conversation=conversation).select_related("message")[:RECENT_REPLIES]
    is_open = conversation.status == InquiryConversation.OPEN
    window = None
    if conversation.provider == InquiryConversation.MESSENGER:
        from apps.inquiries import messenger_inbox
        window = messenger_inbox.window_of(conversation)
    out = {
        "ref": conversation.external_id,
        "display_name": _name(conversation, _unnamed_sms_names(site, [conversation]).get(conversation.contact_mobile)),
        "avatar_url": (
            profile.avatar_urls([conversation.pk])[conversation.pk]
            if conversation.provider == InquiryConversation.MESSENGER else None
        ),
        "channel": conversation.provider,
        "source": "stored",
        "status": conversation.status,
        "unread": conversation.unread,
        "contact_email": conversation.contact_email,
        "contact_mobile": conversation.contact_mobile,
        "messages": [
            {
                "id": str(m.pk), "direction": m.direction, "sender_kind": m.sender_kind, "text": m.body,
                "attachments": [t for t in m.attachment_types.split(",") if t], "at": m.created_at.isoformat(), "delivery_state": m.delivery_state,
            }
            for m in latest
        ],
        "reply": {"open": is_open, "reason": "" if is_open else "closed"},
        "provider_url": None,
        "next_cursor": None,
        "replies": [_reply_out(r) for r in replies],
    }
    if conversation.provider == InquiryConversation.SMS:  # a text conversation: live send states, masked number, room
        from apps.inquiries import sms_inbox
        sms_inbox.refresh(conversation, latest)
        for item, m in zip(out["messages"], latest):
            item["delivery_state"] = m.delivery_state
            if sms_inbox.message_error(m):
                item["error_code"], item["error_message"] = m.error_code, sms_inbox.message_error(m)
        out.update(sms_inbox.thread_extras(site, conversation))
    if window is not None:  # Messenger only: Meta's 24-hour window since the customer's last message
        out["reply_window"] = window
        if is_open and not window["open"]:
            out["reply"] = {"open": False, "reason": outcomes.WINDOW_CLOSED}
    return out


def _earlier(existing, ref):
    if existing.conversation.external_id != ref:
        return "idempotency_conflict", None
    return outcomes.OK, _reply_out(existing)


def reply(site, account, ref, text, idempotency_key):
    """Send one owner reply on a stored conversation. Returns (code, dict): NOT_FOUND for a ref
    that is not this site's; CLOSED_CODE for a closed conversation; otherwise the audit row's state.
    For website chat the visitor's widget is the channel, so `sent` means stored for pick-up."""
    conversation = find(site, ref)
    if conversation is None:
        return outcomes.NOT_FOUND, None
    existing = (
        InquiryReply.objects.filter(site=site, idempotency_key=idempotency_key)
        .select_related("conversation", "message").first()
    )
    if existing:
        return _earlier(existing, ref)
    if conversation.status != InquiryConversation.OPEN:
        return CLOSED_CODE, None
    if conversation.provider == InquiryConversation.MESSENGER:
        from apps.inquiries import messenger_inbox
        return messenger_inbox.reply(site, account, conversation, text, idempotency_key)
    if conversation.provider == InquiryConversation.SMS:
        from apps.inquiries import sms_inbox
        return sms_inbox.reply(site, account, conversation, text, idempotency_key)
    if conversation.provider == InquiryConversation.EMAIL:
        return _reply_email(site, account, conversation, text, idempotency_key)
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
            live.publish(site, ref, live.KIND_MESSAGE, now)
    except IntegrityError:  # the same key arrived twice at once: the first one wins
        existing = InquiryReply.objects.select_related("conversation").get(site=site, idempotency_key=idempotency_key)
        return _earlier(existing, ref)
    return outcomes.OK, _reply_out(row)


# -- email: replies out, and the contact form in -----------------------------------------------

EMAIL_CATEGORY = "inquiry_reply"
EMAIL_HOUR = ("inq-email-hour", 20, 60 * 60)  # inquiry emails per site per hour ...
EMAIL_DAY = ("inq-email-day", 100, 24 * 60 * 60)  # ... and per day
# The footer sentence of an inquiry reply. D6 placeholder wording: awaiting Coach Ed's decision.
FOOTER_REASON = "You are receiving this because you contacted {store}."  # D6 placeholder wording
SUBJECT = "A message from {store}"

logger = logging.getLogger(__name__)


def _one_line(value):
    return " ".join((value or "").split())


def _email_limited(site):
    """True when the site is over its hourly or daily budget of inquiry emails. Keys carry the site."""
    for scope, limit, window in (EMAIL_HOUR, EMAIL_DAY):
        try:
            throttle.hit(scope, site.pk, limit=limit, window=window)
        except ApiError:
            return True
    return False


def _reply_email(site, account, conversation, text, key):
    """Reply by email on an `em_` conversation. The recipient is the conversation's own address and
    nothing in the request; the sender is the store's own (D5). Returns (OK, reply dict): a failure
    is the audit row's state with the specific code and its fixed text, never the mail server's words.
    No exception text, mail-server reply or credential is stored, logged or returned."""
    from apps.members import messaging
    from apps.notifications.store_email import send_store_email, sender_address

    to = conversation.contact_email
    code = ""
    if not to:
        code = outcomes.NO_RECIPIENT
    elif not sender_address(site):
        code = outcomes.SENDER_NOT_READY
    limited = not code and _email_limited(site)
    now = timezone.now()
    failed = code or (outcomes.RATE_LIMITED if limited else "")
    try:
        with transaction.atomic():
            message = InquiryMessage.objects.create(
                site=site, conversation=conversation, direction=InquiryMessage.OUT,
                sender_kind=InquiryMessage.OWNER, body=text, created_at=now,
                delivery_state="failed" if failed else "", error_code=failed,
            )
            row = InquiryReply.objects.create(
                site=site, conversation=conversation, author=account, idempotency_key=key,
                body_length=len(text), message=message,
                state=InquiryReply.BLOCKED_RATE if limited else InquiryReply.FAILED if code else InquiryReply.SENDING,
                error_code=(outcomes.RATE_LIMITED if limited else outcomes.EMAIL_AUDIT_CODE.get(code, "")),
            )
    except IntegrityError:  # the same key arrived twice at once: the first one wins
        existing = (
            InquiryReply.objects.select_related("conversation", "message").get(site=site, idempotency_key=key)
        )
        return _earlier(existing, conversation.external_id)
    live.publish(site, conversation.external_id, live.KIND_MESSAGE, conversation.last_message_at if failed else now)
    if failed:
        return outcomes.OK, _reply_out(row)
    store = _one_line(messaging.store_name(site))
    try:
        send_store_email(
            site, to=to, category=EMAIL_CATEGORY, subject=SUBJECT.format(store=store), body=text,
            footer_reason=FOOTER_REASON.format(store=store), branded=True, log_detail=False,
        )
    except Exception as error:  # noqa: BLE001 - only the class name is kept
        logger.error("Inquiry email not sent (%s).", type(error).__name__)
        state, specific = InquiryReply.FAILED, outcomes.EMAIL_FAILED
        row.state, row.error_code = state, outcomes.EMAIL_AUDIT_CODE[specific]
        row.save(update_fields=["state", "error_code"])
        InquiryMessage.objects.filter(pk=message.pk).update(delivery_state="failed", error_code=specific)
        row.message.delivery_state, row.message.error_code = "failed", specific
        live.publish(site, conversation.external_id, None, event=live.MESSAGE_STATUS)
        return outcomes.OK, _reply_out(row)
    row.state = InquiryReply.SENT
    row.save(update_fields=["state"])
    InquiryMessage.objects.filter(pk=message.pk).update(delivery_state="sent")
    InquiryConversation.objects.filter(pk=conversation.pk).update(last_message_at=now, unread=False)
    live.publish(site, conversation.external_id, None, event=live.MESSAGE_STATUS)
    return outcomes.OK, _reply_out(row)


CONTACT_SITE_DAY = ("inq-contact-site", 200, 24 * 60 * 60)  # stored contact messages per site per day
CONTACT_SENDER_DAY = ("inq-contact-sender", 30, 24 * 60 * 60)  # ... and per typed address per site
MAX_BODY = 4000


def add_contact_message(site, *, name, email, subject, message):
    """Store one storefront contact-form message as a NEW email conversation, after the email to
    the store has gone. Never merged by the typed address (it is a claim, not an identity), so the
    conversation is flagged unverified_sender. Returns the conversation, or None over a cap (the
    email has gone either way; going over a cap is not a failure). The sender counter keys on a
    hash of the lowercased address, never the address."""
    sender = hashlib.sha256(email.lower().encode()).hexdigest()
    try:
        throttle.hit(CONTACT_SITE_DAY[0], site.pk, limit=CONTACT_SITE_DAY[1], window=CONTACT_SITE_DAY[2])
        throttle.hit(
            CONTACT_SENDER_DAY[0], f"{site.pk}:{sender}", limit=CONTACT_SENDER_DAY[1], window=CONTACT_SENDER_DAY[2],
        )
    except ApiError:
        return None
    now = timezone.now()
    with transaction.atomic():
        conversation = InquiryConversation.objects.create(
            site=site, provider=InquiryConversation.EMAIL, external_id=new_ref(InquiryConversation.EMAIL),
            stores_history=True, display_name=name, contact_name=name, contact_email=email,
            auth_flag=InquiryConversation.UNVERIFIED_SENDER, status=InquiryConversation.OPEN, unread=True,
            last_message_at=now, last_inbound_at=now,
        )
        InquiryMessage.objects.create(
            site=site, conversation=conversation, direction=InquiryMessage.IN,
            sender_kind=InquiryMessage.CUSTOMER, body=f"Subject: {subject}\n\n{message}"[:MAX_BODY], created_at=now,
        )
        live.publish(site, conversation.external_id, live.KIND_MESSAGE, now)
    return conversation


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
                live.publish(site, ref, live.KIND_STATUS, conversation.last_message_at)
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
        live.publish(site, ref, live.KIND_STATUS, conversation.last_message_at)
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
        "email_enabled": bool(row and row.email_enabled),
        "paused_until": row.paused_until.isoformat() if paused else None,
        "chat_limit_reached": bool(row and row.chat_limit_at and row.chat_limit_at > now - timedelta(hours=24)),
    }


def update_settings(site, *, webchat_enabled=None, email_enabled=None, paused_until=None, set_pause=False):
    """Save the owner's choices. `set_pause` says paused_until was part of the request, so that
    None can clear it. Returns the row."""
    row, _ = InquiryChannelSettings.objects.get_or_create(site=site)
    if webchat_enabled is not None:
        row.webchat_enabled = webchat_enabled
        if webchat_enabled:
            row.chat_limit_at = None
    if email_enabled is not None:
        row.email_enabled = email_enabled
    if set_pause:
        row.paused_until = paused_until
    row.save()
    return row
