"""Website chat: what the two storefront chat routes do (apps/inquiries/api_storefront.py).

The browser never calls Django. The storefronts server posts here with the server credential, the
visitor's host and address, and the visitor's secret (which it keeps in a cookie). The site is
found from the host alone. A visitor's identity is a 256-bit random secret made on the first send;
only its SHA-256 is stored, so a stolen database row cannot continue a conversation, and the hash is
looked up together with the site, so a secret from one store continues nothing on another.

Abuse limits (plan section 4): a honeypot, two seconds from the widget opening to the first send,
a text of 1..1000 characters with no control characters and at most two links, the same text
twice in five minutes dropped, and counters per visitor address, per secret and per site. There
is no CAPTCHA. The text, the secret and the visitor's address are never logged."""
import hashlib
import ipaddress
import re
import secrets
import unicodedata
from datetime import timedelta, timezone as dt_timezone

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Case, When
from django.utils import timezone

from apps.common import throttle
from apps.common.errors import ApiError
from apps.inquiries import live, stored
from apps.inquiries.models import InquiryChannelSettings, InquiryConversation, InquiryMessage

PAUSED_NOTICE = "Chat is not available right now. Please use the contact form instead."
MAX_TEXT, MAX_NAME, MAX_EMAIL, MAX_LINKS = 1000, 80, 254, 2
MIN_OPEN_SECONDS = 2
DUPLICATE_WINDOW = 5 * 60
POLL_LIMIT = 50
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # token_urlsafe(32)
LINK_RE = re.compile(r"(?i)(?:https?://|www\.)\S+")

HOUR, DAY = 60 * 60, 24 * 60 * 60
# (scope, limit, window seconds). The address counters are shared by every store, like the contact form's.
IP_NEW = ("chat-ip-new", 5, HOUR)
IP_MESSAGES = ("chat-ip-messages", 30, HOUR)
SECRET_MESSAGES = ("chat-secret-messages", 10, 10 * 60)
SITE_NEW = ("chat-site-new", 60, DAY)
SITE_MESSAGES = ("chat-site-messages", 600, DAY)
IP_POLLS = ("chat-ip-polls", 400, HOUR)  # a widget polls every 20 s while open


class SiteCapped(Exception):
    """A per-site daily ceiling was reached: the visitor is answered as if chat were paused."""


def _invalid(field, message):
    return ApiError("invalid_request", message, status=422, fields={field: [message]})


def _ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return "unknown"  # every visitor with no usable address shares one count


def hash_of(secret):
    """The SHA-256 of a well-formed secret, or "" for anything else."""
    if not secret or not SECRET_RE.match(secret):
        return ""
    return hashlib.sha256(secret.encode()).hexdigest()


def _one_line(value, limit):
    plain = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c for c in value or "")
    return " ".join(plain.split())[:limit]


def available(site):
    """True while the owner has chat switched on and not paused."""
    row = InquiryChannelSettings.objects.filter(site=site).first()
    if row is None or not row.webchat_enabled:
        return False
    return not (row.paused_until and row.paused_until > timezone.now())


def _checked_text(raw):
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise _invalid("text", "Please write a message.")
    if len(text) > MAX_TEXT:
        raise _invalid("text", f"A message is at most {MAX_TEXT} characters.")
    if any(unicodedata.category(c) == "Cc" and c != "\n" for c in text):
        raise _invalid("text", "That message has characters we cannot accept.")
    if len(LINK_RE.findall(text)) > MAX_LINKS:
        raise _invalid("text", f"Please keep a message to {MAX_LINKS} links or fewer.")
    return text


def _checked_email(raw):
    email = (raw or "").strip().lower()[:MAX_EMAIL]
    if not email:
        return ""
    try:
        validate_email(email)
    except ValidationError:
        raise _invalid("email", "Please enter a valid email address.") from None
    return email


def _paused(state="paused"):
    return {"state": state, "visitor_secret": "", "messages": [], "notice": PAUSED_NOTICE}


def _owner_messages(conversation, after_id):
    if conversation is None:
        return []
    rows = InquiryMessage.objects.filter(
        conversation=conversation, direction=InquiryMessage.OUT, pk__gt=max(after_id, 0),
    ).order_by("pk")[:POLL_LIMIT]
    return [{"id": m.pk, "text": m.body, "at": m.created_at.isoformat()} for m in rows]


def _latest(site, secret_hash):
    """The visitor's open conversation on this site if they have one (the database allows one),
    otherwise their newest, or None. The site is part of the lookup. Open first, because the
    owner can reopen an older conversation after a newer one was made and closed."""
    if not secret_hash:
        return None
    return InquiryConversation.objects.filter(
        site=site, provider=InquiryConversation.WEBCHAT, visitor_hash=secret_hash,
    ).order_by(Case(When(status=InquiryConversation.OPEN, then=0), default=1), "-pk").first()


def _site_hit(site, spec):
    scope, limit, window = spec
    try:
        throttle.hit(scope, site.pk, limit=limit, window=window)
    except ApiError:
        InquiryChannelSettings.objects.filter(site=site).update(chat_limit_at=timezone.now())
        raise SiteCapped from None


def _opened_in_time(opened_at, now):
    if opened_at is None:
        return False
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=dt_timezone.utc)
    return now - opened_at >= timedelta(seconds=MIN_OPEN_SECONDS)


def _dup_key(site, who, text):
    digest = hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()[:32]
    return f"inq-chat-dup:{site.pk}:{who}:{digest}"


def _is_duplicate(site, who, text):
    return not cache.add(_dup_key(site, who, text), 1, DUPLICATE_WINDOW)


def _fill_contact(conversation, name, email):
    changed = []
    if name and not conversation.contact_name:
        conversation.contact_name = conversation.display_name = name
        changed += ["contact_name", "display_name"]
    if email and not conversation.contact_email:
        conversation.contact_email = email
        changed.append("contact_email")
    if changed:
        conversation.save(update_fields=changed)


def _store(site, conversation, secret_hash, name, email, text, now):
    """Add the visitor's message, starting a conversation when there is none open. Returns the
    conversation. Two first messages at once end up in one conversation (the database allows
    one open conversation per visitor)."""
    try:
        with transaction.atomic():
            if conversation is None or conversation.status != InquiryConversation.OPEN:
                conversation = InquiryConversation.objects.create(
                    site=site, provider=InquiryConversation.WEBCHAT, external_id=stored.new_ref(InquiryConversation.WEBCHAT),
                    stores_history=True, display_name=name, contact_name=name, contact_email=email,
                    visitor_hash=secret_hash, status=InquiryConversation.OPEN,
                )
            InquiryMessage.objects.create(
                site=site, conversation=conversation, direction=InquiryMessage.IN,
                sender_kind=InquiryMessage.CUSTOMER, body=text, created_at=now,
            )
    except IntegrityError:
        conversation = _latest(site, secret_hash)
        if conversation is None or conversation.status != InquiryConversation.OPEN:
            raise
        InquiryMessage.objects.create(
            site=site, conversation=conversation, direction=InquiryMessage.IN,
            sender_kind=InquiryMessage.CUSTOMER, body=text, created_at=now,
        )
    _fill_contact(conversation, name, email)
    InquiryConversation.objects.filter(pk=conversation.pk).update(
        last_message_at=now, last_inbound_at=now, unread=True,
    )
    live.publish(site, conversation.external_id, live.KIND_MESSAGE, now)
    return conversation


def send(site, *, visitor_ip, visitor_secret, name, email, text, website, opened_at, after_id):
    """One visitor message. Returns the response dict: {state, visitor_secret, messages, notice}.
    `visitor_secret` is filled only on the call that made it."""
    if website:  # the hidden field people never see: answered as sent, nothing stored or counted
        return {"state": "open", "visitor_secret": "", "messages": [], "notice": ""}
    if not available(site):
        return _paused()
    text = _checked_text(text)
    name = _one_line(name, MAX_NAME)
    email = _checked_email(email)
    ip = _ip(visitor_ip)
    secret_hash = hash_of(visitor_secret)
    conversation = _latest(site, secret_hash)
    known = conversation is not None  # a secret that matches nothing here is treated as no secret
    now = timezone.now()
    if not known and not _opened_in_time(opened_at, now):
        raise ApiError("too_fast", "Please wait a moment and try again.", status=422)
    issued = ""
    if not known:
        issued = secrets.token_urlsafe(32)
        secret_hash = hash_of(issued)
    starts_new = conversation is None or conversation.status != InquiryConversation.OPEN
    throttle.hit(IP_MESSAGES[0], ip, limit=IP_MESSAGES[1], window=IP_MESSAGES[2])
    throttle.hit(SECRET_MESSAGES[0], f"{site.pk}:{secret_hash}", limit=SECRET_MESSAGES[1], window=SECRET_MESSAGES[2])
    try:
        _site_hit(site, SITE_MESSAGES)
        if starts_new:
            throttle.hit(IP_NEW[0], ip, limit=IP_NEW[1], window=IP_NEW[2])
            _site_hit(site, SITE_NEW)
    except SiteCapped:
        return _paused()
    if _is_duplicate(site, secret_hash if known else ip, text):
        return {
            "state": conversation.status if known else "open", "visitor_secret": "",
            "messages": _owner_messages(conversation, after_id), "notice": "",
        }
    saved = _store(site, conversation, secret_hash, name, email, text, now)
    if issued:  # the same text again, now with the new secret, is a duplicate of this one
        cache.add(_dup_key(site, secret_hash, text), 1, DUPLICATE_WINDOW)
    return {
        "state": saved.status, "visitor_secret": issued, "messages": _owner_messages(saved, after_id), "notice": "",
    }


def poll(site, *, visitor_ip, visitor_secret, after_id):
    """The owner's replies since `after_id` and the conversation's state. A secret that matches
    nothing on this site gets the same empty answer as a visitor who has not written yet."""
    throttle.hit(IP_POLLS[0], _ip(visitor_ip), limit=IP_POLLS[1], window=IP_POLLS[2])
    conversation = _latest(site, hash_of(visitor_secret))
    state = conversation.status if conversation else "none"
    notice = ""
    if not available(site):
        state, notice = "paused", PAUSED_NOTICE
    return {
        "state": state, "visitor_secret": "", "messages": _owner_messages(conversation, after_id), "notice": notice,
    }
