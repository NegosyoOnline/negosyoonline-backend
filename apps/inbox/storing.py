"""The one place an incoming text is stored. Called only by the gateway's `received` call.

No log line, cache entry or audit entry made here ever holds a number or a text."""
import logging
import unicodedata
from datetime import datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.common.phones import normalise_ph_mobile
from apps.inbox.models import Conversation, ConversationMessage, DoNotText
from apps.inbox.switch import SMS, dnt_tables_exist, inbox_on
from apps.notifications.models import SmsDevice

logger = logging.getLogger(__name__)

MAX_TEXTS = 20
MAX_BODY = 2000
MAX_CONTACT = 32
MAX_REF = 100


def owner_of(device):
    """Who owns a conversation on this phone's line. Always NegosyoOnline in this build: no
    phone is assigned to a Site. The Dedicated SMS Line add-on changes this one function."""
    return {"owner_kind": Conversation.Owner.HQ, "site": None, "account": None}


def wanted(device):
    """True when a text from this phone is to be stored. Never for a Site's phone."""
    return device.site_id is None and inbox_on() and device.forwards_incoming


def _text(value, longest=None):
    """A string without its NUL characters, which PostgreSQL refuses; None when it is not a
    string. With `longest` it is also trimmed, and None when it is then empty or too long."""
    if not isinstance(value, str):
        return None
    value = value.replace("\x00", "")
    if longest is None:
        return value
    value = value.strip()
    return value if 0 < len(value) <= longest else None


def _clean(body):
    """The good texts in the body as plain values, and how many items were skipped as bad.
    None, and how many items the list held, when the body is not a list of at most 20 items.

    One bad item never refuses the others: the phone sends a refused list again unchanged,
    so a single bad text would hold back every text behind it."""
    items = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return None, 0
    if len(items) > MAX_TEXTS:
        return None, len(items)
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ref, sender, text = _text(item.get("id"), MAX_REF), _text(item.get("from"), 10_000), _text(item.get("text"))
        if ref is None or sender is None or text is None:
            continue
        cleaned.append((ref, sender, text, item.get("received_at"), item.get("sim_slot")))
    return cleaned, len(items) - len(cleaned)


def _contact(sender):
    """The sender as stored, and whether it can be replied to."""
    try:
        return normalise_ph_mobile(sender), True
    except ValueError:
        return sender[:MAX_CONTACT], False


def _is_stop(text):
    """True when the whole text is the word STOP, in any case. Whitespace and punctuation
    (Unicode category P) at either end are dropped; nothing inside the text is."""
    word = text.strip()
    start, end = 0, len(word)
    while start < end and unicodedata.category(word[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(word[end - 1]).startswith("P"):
        end -= 1
    return word[start:end].strip().casefold() == "stop"


def _when(raw, now):
    try:
        value = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return now
    return value if value.tzinfo else now


def _slot(raw):
    whole = isinstance(raw, int) and not isinstance(raw, bool)
    return raw if whole and 0 <= raw <= 9 else None


def store(device, body):
    """Store the texts of one `received` call. True when the call is done with: its good
    texts are stored or were already, and its bad ones are skipped for good. False when the
    body is not a list of texts and nothing was stored."""
    texts, count = _clean(body)
    if texts is None:
        logger.error("sms_incoming_rejected count=%s", count)
        return False
    if count:
        logger.error("sms_incoming_skipped count=%s", count)
    if not texts:
        return True
    now = timezone.now()
    listing = dnt_tables_exist()
    from apps.inquiries import sms_inbox  # a store's own text conversations (SMS-OWNER)
    with transaction.atomic():
        # One call per phone at a time, so a repeat that overlaps the first cannot store twice.
        SmsDevice.objects.select_for_update().get(pk=device.pk)
        seen = set(
            ConversationMessage.objects.filter(
                device=device, direction=ConversationMessage.Direction.IN,
                gateway_ref__in=[ref for ref, *_ in texts],
            ).values_list("gateway_ref", flat=True)
        )
        seen |= sms_inbox.seen_refs(device, [ref for ref, *_ in texts])  # already in a store's conversation
        for ref, sender, text, received_at, sim_slot in texts:
            if ref in seen:
                continue
            seen.add(ref)
            contact, can_reply = _contact(sender)
            occurred_at = _when(received_at, now)
            if can_reply:
                # The store that texted this number last (7 days) gets the answer, STOP included:
                # a STOP to a store is that store's opt-out, not HQ's. None: it goes to HQ below.
                site = sms_inbox.route_incoming(contact, now)
                if site is not None:
                    sms_inbox.store_incoming(site, device, contact, ref, text, occurred_at, _is_stop(text))
                    continue
            conversation, _ = Conversation.objects.get_or_create(
                platform=SMS, line=device, contact=contact, **owner_of(device),
                defaults={"can_reply": can_reply, "last_message_at": occurred_at},
            )
            message = ConversationMessage.objects.create(
                conversation=conversation, platform=SMS, direction=ConversationMessage.Direction.IN,
                body=text[:MAX_BODY], clipped=len(text) > MAX_BODY, gateway_ref=ref,
                device=device, sim_slot=_slot(sim_slot), occurred_at=occurred_at,
            )
            if listing and can_reply and _is_stop(text):
                DoNotText.objects.get_or_create(platform=SMS, mobile=contact, defaults={"message": message})
            Conversation.objects.filter(pk=conversation.pk).update(
                unread_count=F("unread_count") + 1,
                last_message_at=max(conversation.last_message_at, occurred_at),
            )
    return True
