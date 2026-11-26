"""The one place an incoming text is stored. Called only by the gateway's `received` call.

The platform phone's texts go to HQ's inbox (or to the store that texted the number last). A
Site's phone forwards only when the server says so (`wanted`), and only the replies to that
Site's own texts of the last 7 days are kept, in the Site's own Inbox; the rest are dropped
unread (`_store_site`).

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
    """True when a text from this phone is to be stored. The platform phone: the inbox switch and
    the phone's own `forwards_incoming`. A Site's phone: the server decides (SITE-PHONE-REPLIES),
    see `_site_wanted`; `forwards_incoming` is not read for it."""
    if device.site_id is not None:
        return _site_wanted(device)
    return inbox_on() and device.forwards_incoming


def _site_wanted(device):
    """True when a Site's phone is to forward what it receives: the inbox and store-SMS switches
    are on, Site phones are on, the Site has a line and its text conversations on, and this phone
    is that Site's active line. Anything else and the phone is told not to forward."""
    from apps.inquiries import sms_inbox  # not at import: inquiries sits above the inbox
    from apps.notifications import sms

    site = device.site
    return bool(
        inbox_on() and sms_inbox.sms_ready() and sms.site_lines_on()
        and sms.site_has_line(site) and sms_inbox.enabled(site)
        and (lined := sms._lined(site)) is not None and lined.pk == device.pk
    )


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


def _store_site(device, texts, now, sms_inbox):
    """The texts of one `received` call from a Site's phone (SITE-PHONE-REPLIES, option A). A text
    is kept only when its sender can be replied to and this Site texted that number from this
    phone in the last 7 days (a store_reply, store_compose or store_bulk text handed to this
    phone, or sent by it; a queued text has no phone and no reply). It goes to the Site's own conversation, a STOP onto the Site's
    opt-out list. Every other text is dropped: not stored, not logged, only counted. Nothing here
    ever touches HQ's conversation tables. Always True: a dropped text is done with for good."""
    from apps.notifications import sms
    from apps.notifications.models import SmsMessage

    since = now - sms_inbox.ROUTE_WITHIN
    dropped = 0
    with transaction.atomic():
        SmsDevice.objects.select_for_update().get(pk=device.pk)  # one call per phone at a time
        seen = sms_inbox.seen_refs(device, [ref for ref, *_ in texts])
        for ref, sender, text, received_at, _slot_unused in texts:
            if ref in seen:
                continue
            seen.add(ref)
            contact, can_reply = _contact(sender)
            texted = can_reply and SmsMessage.objects.filter(
                site=device.site, recipient=contact, purpose__in=sms.STORE_PURPOSES,
                device=device, status__in=(SmsMessage.Status.DISPATCHED, SmsMessage.Status.SENT),
                created_at__gte=since,
            ).exists()
            if not texted:
                dropped += 1
                continue
            sms_inbox.store_incoming(device.site, device, contact, ref, text, _when(received_at, now), _is_stop(text))
    if dropped:
        logger.info("sms_site_incoming_dropped count=%s", dropped)  # a count only, never who or what
    return True


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
    from apps.inquiries import sms_inbox  # a store's own text conversations (SMS-OWNER)
    if device.site_id is not None:
        return _store_site(device, texts, now, sms_inbox)
    listing = dnt_tables_exist()
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
