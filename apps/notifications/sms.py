"""The parts `send_sms` and the gateway are built from: what may be sent, the limits, the kill
switch, where a text waits, and which phone sends it.

Nothing here is called by another app. Every app sends through
`apps.notifications.services.send_sms`.
"""
import base64
import hashlib
import json
import logging
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.notifications.models import (
    SmsDevice, SmsLineDisable, SmsMessage, SmsPolicy, site_column_exists,
)

logger = logging.getLogger(__name__)

Priority = SmsMessage.Priority
CODE, STATUS = SmsMessage.Category.CODE.value, SmsMessage.Category.STATUS.value
INBOX = SmsMessage.Category.INBOX.value
STORE = SmsMessage.Category.STORE.value
STORE_BULK = SmsMessage.Category.STORE_BULK.value
QUEUED, EMAILED, REFUSED = "queued", "emailed", "refused"

DEFAULT_LIFETIME = 600  # seconds a text is worth sending: a code's 10 minutes
ONLINE_WITHIN = timedelta(seconds=60)
NO_REPORT_AFTER = 600  # seconds the gateway waits for the phone's report on a text it took
_COUNT_KEPT = 2 * 24 * 3600
STAFF_TEXT_LIFETIME = 3600  # seconds a staff text waits for the phone
# Of the day's texts, this many are for codes only (Coach Ed, "Hold back 300"). A staff text
# is refused once the day's count reaches the limit less this. Changing it is his ruling.
RESERVED_FOR_CODES = 300
# Texts one store may send in a clock hour (Manila time). The same pattern as the 20 emails an
# hour a store may send from its Inbox (apps/inquiries/stored.py, EMAIL_HOUR).
STORE_HOURLY_LIMIT = 20


@dataclass(frozen=True)
class Purpose:
    category: str
    priority: int
    paced: bool = False  # sent no sooner than the gap after the last paced text


# The closed list of what may be sent. A text of any other kind cannot be sent without a
# reviewed change to this list: codes, order-status texts, and a text an HQ staff member
# writes to one number from the inbox. Marketing has no purpose here and cannot be sent.
PURPOSES = {
    "mobile_verify": Purpose(category=CODE, priority=Priority.CODE),
    # A Santé member's one-time code, texted to the member's own phone: signed in by mobile, and
    # the mobile confirmed after the first order. Codes only: platform limit, never a site cap.
    "member_signin_code": Purpose(category=CODE, priority=Priority.CODE),
    "member_mobile_verify": Purpose(category=CODE, priority=Priority.CODE),
    "inbox_reply": Purpose(category=INBOX, priority=Priority.REPLY, paced=True),
    "inbox_compose": Purpose(category=INBOX, priority=Priority.REPLY, paced=True),
    # One announcement text of a Bulk SMS batch. MARKETING is the sending class (lowest, paced),
    # not a permission for other marketing: this closed list is what forbids that.
    "inbox_bulk": Purpose(category=INBOX, priority=Priority.MARKETING, paced=True),
    # A Santé store's text to one of its own members, written in the store's dash Inbox (SMS-OWNER,
    # Coach Ed 2026-11-21). One member at a time: there is no list send. It names its site and has
    # no email fallback. The same sending class as a staff text, in the same paced lane.
    "store_reply": Purpose(category=STORE, priority=Priority.REPLY, paced=True),
    "store_compose": Purpose(category=STORE, priority=Priority.REPLY, paced=True),
    # One text of a store's bulk send to its own members (STORE-BULK, Coach Ed 2026-11-23). Counted
    # apart from the one-to-one store texts: its own daily cap (SmsPolicy.store_bulk_daily_limit),
    # never NegosyoOnline's daily limit or the room kept for codes, and only ever from the store's
    # own paired phone. The lowest sending class, so a one-to-one text is handed out first.
    "store_bulk": Purpose(category=STORE_BULK, priority=Priority.MARKETING, paced=True),
}

STORE_PURPOSES = ("store_reply", "store_compose", "store_bulk")


def paced_purposes():
    return [name for name, declared in PURPOSES.items() if declared.paced]


@dataclass(frozen=True)
class EmailFallback:
    """The email that goes out when the text cannot."""

    to: str
    subject: str
    body: str


@dataclass(frozen=True)
class SmsResult:
    outcome: str  # QUEUED, EMAILED or REFUSED
    reason: str = ""
    message: SmsMessage | None = None  # None while the gateway is off: no record is written


# --- The master switch ---------------------------------------------------------------------


def redis_missing():
    """A deployment without Redis keeps the cache per process, so a text queued by one process
    could not be found by the one the phone reaches."""
    return not settings.DEBUG and not settings.REDIS_URL


def gateway_on():
    return bool(settings.SMS_GATEWAY_ENABLED) and not redis_missing()


# --- The text ------------------------------------------------------------------------------

# The GSM 7-bit basic set: the characters of which 160 fit one SMS.
_PLAIN = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
# A web link in any form a phone could make tappable. The edges are spelled out as plain letters
# and digits rather than "\b": "\b" counts "_" and the accented and Greek SMS letters as part of
# a word, so "see_example.com" and "ñexample.com" used to pass.
_LINK = re.compile(
    r"https?://"
    r"|(?<![a-z0-9])www\."
    r"|[a-z0-9-]+\.[a-z]{2,}(?![a-z0-9])"  # a bare domain: example.com
    r"|(?<![0-9])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![0-9])",  # a bare IP address: 10.0.0.1
    re.IGNORECASE | re.ASCII,
)
MAX_LENGTH = 160


def text_problem(text):
    """Why a text cannot be sent, or "" when it can."""
    if not isinstance(text, str) or not text.strip():
        return "The text is empty."
    if len(text) > MAX_LENGTH or not _PLAIN.issuperset(text):
        return "The text must fit one SMS: at most 160 plain characters."
    if _LINK.search(text):
        return "The text must not contain a web link."
    return ""


# --- Where a text waits --------------------------------------------------------------------
#
# Holding and taking are keyed by the send record. The database never holds a text; the cache
# holds ciphertext only, for as long as the text is worth sending.


def _fernet():
    digest = hashlib.sha256(b"notifications.sms.text:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def text_key(message):
    return f"sms:text:{message.public_id}"


def fallback_key(message):
    return f"sms:fallback:{message.public_id}"


def _hold(key, plain, lifetime):
    cache.set(key, _fernet().encrypt(plain.encode()).decode(), lifetime)


def _take(key):
    held = cache.get(key)
    if held is None:
        return None
    cache.delete(key)
    try:
        return _fernet().decrypt(held.encode()).decode()
    except (InvalidToken, AttributeError):
        return None


def hold_text(message, text, lifetime):
    _hold(text_key(message), text, lifetime)


def take_text(message):
    """The text, once. None when it expired or was lost."""
    return _take(text_key(message))


def hold_fallback(message, fallback, lifetime):
    # It can still be owed after the text itself expired: when the phone took the text and
    # never reported, which the gateway learns NO_REPORT_AFTER seconds later.
    _hold(fallback_key(message), json.dumps(asdict(fallback)), lifetime + NO_REPORT_AFTER + 60)


def take_fallback(message):
    held = _take(fallback_key(message))
    return EmailFallback(**json.loads(held)) if held else None


def forget(message):
    cache.delete_many([text_key(message), fallback_key(message)])


# --- The kill switch and the limits --------------------------------------------------------


def policy():
    return SmsPolicy.objects.get_or_create(pk=1)[0]


def _today():
    return timezone.localdate()  # TIME_ZONE is Asia/Manila


def _day_start():
    return timezone.make_aware(datetime.combine(_today(), time.min))


def _counted(message=None):
    """Today's texts that used a slot. The records are the source of truth for the counts."""
    rows = SmsMessage.objects.filter(created_at__gte=_day_start()).exclude(
        status=SmsMessage.Status.REFUSED
    )
    return rows.exclude(pk=message.pk) if message else rows


def _platform_key():
    return f"sms:count:platform:{_today():%Y%m%d}"


def _site_key(site):
    return f"sms:count:site:{site.pk}:{_today():%Y%m%d}"


def _store_key(site):
    return f"sms:count:store:{site.pk}:{_today():%Y%m%d}"


def _store_hour_key(site, hour_start):
    return f"sms:count:storehour:{site.pk}:{hour_start:%Y%m%d%H}"


def _store_bulk_key(site):
    return f"sms:count:storebulk:{site.pk}:{_today():%Y%m%d}"


def _all_stores_key():
    return f"sms:count:stores:{_today():%Y%m%d}"


def hour_start():
    """The start of the current clock hour (Manila time), which the hourly store cap counts from."""
    return timezone.localtime().replace(minute=0, second=0, microsecond=0)


def _claim(key, limit, recount):
    """Take one of the day's slots. The add is atomic, so two requests cannot both take the
    last one. An empty cache is started from the records."""
    if cache.get(key) is None:
        cache.add(key, recount(), _COUNT_KEPT)
    try:
        used = cache.incr(key)
    except ValueError:  # the entry went between the add and the incr
        used = recount() + 1
        cache.set(key, used, _COUNT_KEPT)
    if used > limit:
        _release(key)
        return False
    return True


def _release(key):
    try:
        cache.decr(key)
    except ValueError:
        pass


def platform_count():
    """Today's count for the platform, as the limit sees it."""
    held = cache.get(_platform_key())
    return _counted().count() if held is None else held


def store_used_today(site):
    """Today's store texts of this Site that used a slot, from the records."""
    return _counted().filter(site=site, category=STORE).count()


def store_bulk_used_today(site):
    """Today's bulk texts of this Site that used a slot (a test send counts as one), from the records."""
    return _counted().filter(site=site, category=STORE_BULK).count()


def store_used_this_hour(site):
    return _counted().filter(site=site, category=STORE, created_at__gte=hour_start()).count()


def refusal(message):
    """Why this text is not sent now, or "" when it may be queued. Takes its daily slots.

    A store text (4.2 of the SMS-OWNER plan) is refused in this order: sending stopped; the
    store's day (`site_limit`); the store's hour (`site_hour_limit`); all stores' day
    (`stores_limit`); the platform day less the texts kept for codes (`staff_limit`); the phone
    offline (`gateway_offline`, or `site_phone_offline` when the store's own paired phone is the
    one that is offline)."""
    rules = policy()
    if rules.sending_stopped:
        return "paused"
    taken = []  # the slots claimed so far, given back when a later rule refuses

    def refuse(reason):
        for key in taken:
            _release(key)
        return reason

    def claim(key, limit, recount):
        if not _claim(key, limit, recount):
            return False
        taken.append(key)
        return True

    if message.category == STATUS and message.site_id:
        of_site = _counted(message).filter(site=message.site, category=STATUS)
        if not claim(_site_key(message.site), rules.site_daily_limit, of_site.count):
            return refuse("site_limit")
    if message.category == STORE and message.site_id:
        mine = _counted(message).filter(site=message.site, category=STORE)
        if not claim(_store_key(message.site), rules.store_daily_limit, mine.count):
            return refuse("site_limit")
        start = hour_start()
        this_hour = mine.filter(created_at__gte=start)
        if not claim(_store_hour_key(message.site, start), STORE_HOURLY_LIMIT, this_hour.count):
            return refuse("site_hour_limit")
        stores = _counted(message).filter(category=STORE)
        if not claim(_all_stores_key(), rules.all_stores_daily_limit, stores.count):
            return refuse("stores_limit")
    if message.category == STORE_BULK and message.site_id:
        # A store's bulk text uses its own daily cap only: not the store's 50 a day or 20 an hour, not
        # all stores' day, and not NegosyoOnline's daily limit or the room kept for codes. It goes
        # from the store's own paired phone; a store entitled to a line with no phone of its own
        # goes from the platform phone (ONE-PHONE, Coach Ed 2026-11-26).
        mine = _counted(message).filter(site=message.site, category=STORE_BULK)
        if not claim(_store_bulk_key(message.site), rules.store_bulk_daily_limit, mine.count):
            return refuse("bulk_limit")
        if bulk_phone(message.site) is None:
            return refuse("no_line")
        if not gateway_online(message.site, message.category):
            own = _lined(message.site) is not None
            return refuse("site_phone_offline" if own else "gateway_offline")
        return ""
    limit, reason = rules.platform_daily_limit, "platform_limit"
    if message.category in (INBOX, STORE):
        limit, reason = max(limit - RESERVED_FOR_CODES, 0), "staff_limit"
    if not claim(_platform_key(), limit, _counted(message).count):
        return refuse(reason)
    if not gateway_online(message.site, message.category):
        # A store whose own paired phone is offline is told so; its text is not handed to the
        # shared phone (D2). A store with no phone of its own meets the shared phone's offline.
        own_phone = message.category == STORE and _lined(message.site) is not None
        return refuse("site_phone_offline" if own_phone else "gateway_offline")
    return ""


# --- Texts whose time passed ---------------------------------------------------------------

SWEEP = 200  # texts ended in one pass; the rest wait for the next one


def sweep(now, unsent):
    """End the texts whose time passed while waiting, and the texts the phone took and never
    reported. `unsent(message, reason)` is called for each: it emails the fallback. Runs when
    the phone polls and when a text is sent, so it does not depend on the phone being there."""
    Status = SmsMessage.Status
    expired = SmsMessage.objects.filter(status=Status.QUEUED, expires_at__lte=now)
    for message in expired.order_by("pk")[:SWEEP]:
        if SmsMessage.objects.filter(pk=message.pk, status=Status.QUEUED).update(
            status=Status.EXPIRED
        ):
            take_text(message)
            unsent(message, "expired")
    silent_since = now - timedelta(seconds=NO_REPORT_AFTER)
    unreported = SmsMessage.objects.filter(
        status=Status.DISPATCHED, dispatched_at__lte=silent_since
    )
    for message in unreported.order_by("pk")[:SWEEP]:
        if SmsMessage.objects.filter(pk=message.pk, status=Status.DISPATCHED).update(
            status=Status.FAILED, reason="no_report"
        ):
            unsent(message, "no_report")


# --- The phone -----------------------------------------------------------------------------


# A phone's token is typed in by hand, so it is short and easy to read: capitals and digits
# without the look-alikes 0, O, 1, I and L. 31 symbols over 8 places is about 40 bits.
TOKEN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TOKEN_LENGTH = 8


def new_token():
    """A fresh phone token. Both `register` and `rotate` (so the HQ screens and the command)
    come here."""
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def normalise_token(token):
    """The token as it is hashed. Surrounding spaces are always dropped. A token of the new
    length is typed by hand, so it is also matched without regard to case; a longer legacy
    token stays exactly as it was."""
    token = token.strip()
    return token.upper() if len(token) == TOKEN_LENGTH else token


TOKEN_SALT = "apps.notifications.sms.token"


def token_hash(token, secret=None):
    """What is stored of a phone's token: a keyed hash (Django's salted_hmac over SECRET_KEY, or
    over `secret`) of the token as normalised. The token itself is shown once and never kept."""
    return salted_hmac(
        TOKEN_SALT, normalise_token(token), secret=secret, algorithm="sha256"
    ).hexdigest()


def _legacy_token_hash(token):
    """The plain SHA-256 that phones registered before the keyed hash were stored with."""
    return hashlib.sha256(normalise_token(token).encode()).hexdigest()


def token_hash_candidates(token):
    """Every hash a phone holding `token` may be stored under, the current one first: the keyed
    hash, the keyed hash under each of SECRET_KEY_FALLBACKS, and the legacy plain hash."""
    keyed = [token_hash(token)] + [
        token_hash(token, secret=old) for old in settings.SECRET_KEY_FALLBACKS
    ]
    return keyed + [_legacy_token_hash(token)]


def pick_device():
    """The platform phone, the system phone: the first active phone that belongs to no Site. A
    phone with a Site can never become it, so a Site's phone is never handed other texts."""
    phones = SmsDevice.objects.filter(is_active=True)
    if site_column_exists():  # before notifications.0005 no phone has a Site
        phones = phones.filter(site__isnull=True)
    return phones.order_by("pk").first()


def site_lines_on():
    """SMS_SITE_LINES_ENABLED: Site phones carry their Site's texts. Off by default."""
    return bool(getattr(settings, "SMS_SITE_LINES_ENABLED", False))


def site_has_line(site):
    """Whether this Site is entitled to its own phone (SMS-INCLUDED): its billing position is
    serving (`billing.position(site).storefront_served`: trial, active, payment needing
    attention, billing-exempt) and HQ has not disabled its line. Nothing else decides
    entitlement. It fails closed: with billing off, no subscription, or a position that cannot
    be read, there is no line. A Site that goes to suspended or cancelled loses it at once;
    reactivated, it has it again."""
    from apps.billing import services as billing  # not at import: billing is a later layer

    if not billing.position(site).storefront_served:
        return False
    return not SmsLineDisable.objects.filter(site=site, reenabled_at__isnull=True).exists()


def _lined(site):
    """The Site's own active phone when its texts go through it, else None."""
    if site is None or not site_lines_on() or not site_column_exists() or not site_has_line(site):
        return None
    return SmsDevice.objects.filter(is_active=True, site=site).first()


def bulk_phone(site):
    """The phone that carries a store's bulk text (ONE-PHONE, Coach Ed 2026-11-26): the store's
    own active phone when it has one with a line, else the platform phone when the store is
    entitled to a line (`site_has_line`) and SMS_SITE_LINES_ENABLED is on, else None (no line:
    not entitled, or the switch is off)."""
    own = _lined(site)
    if own is not None:
        return own
    if site is not None and site_lines_on() and site_has_line(site):
        return pick_device()
    return None


def line_for(site, category=None):
    """The phone that carries a text of this Site (None: a platform text): the Site's own active
    phone when the switch is on and the Site is entitled, otherwise the platform phone. A code
    (`category` CODE) is never a Site's to send: it always goes from the platform phone (D1). A
    store's bulk text (`bulk_phone`) goes from the store's own phone, or from the platform phone
    when the store is entitled to a line but has no phone of its own (ONE-PHONE, 2026-11-26)."""
    if category == CODE:
        return pick_device()
    if category == STORE_BULK:
        return bulk_phone(site)
    return _lined(site) or pick_device()


def claimable(device):
    """The condition on SmsMessage rows that this phone may take. Used by the waiting query and
    again in the claim UPDATE. It is the mirror of `line_for`.

    Platform phone: texts of no Site, texts of Sites that have no line, every code (D1: a
    sign-in code names its Site but always goes from this phone), and the bulk texts of a Site
    with no phone of its own (ONE-PHONE, 2026-11-26; a Site with its own phone keeps its bulk on
    that phone only). A Site phone: only its own
    Site's texts other than codes, and nothing while its Site has no line (switch off, billing
    ended, or disabled by HQ)."""
    if device.site_id is not None:
        if site_lines_on() and site_has_line(device.site):
            return Q(site_id=device.site_id) & ~Q(category=CODE)
        return Q(pk__in=[])
    if not site_column_exists():
        return Q()
    if not site_lines_on():
        return Q()
    sites = SmsDevice.objects.filter(is_active=True, site__isnull=False).select_related("site")
    lined = [phone.site_id for phone in sites if site_has_line(phone.site)]
    if not lined:
        return Q()
    return Q(site__isnull=True) | ~Q(site_id__in=lined) | Q(category=CODE)


def may_be_handed_texts(device):
    """True for the platform phone, and for a Site phone whose Site has a line now."""
    if device.site_id is None:
        return device == pick_device()
    return _lined(device.site) == device


def gateway_online(site=None, category=None):
    """The phone that would carry a text of this Site asked for texts in the last minute."""
    device = line_for(site, category)
    if device is None or device.last_contact_at is None:
        return False
    return device.last_contact_at >= timezone.now() - ONLINE_WITHIN


def log_not_sent(reason, purpose, *, email_failed=False):
    """One ERROR line for a text that neither went out nor was emailed. Never a number or a
    text. While the phone is away the line is written at most once an hour, except for a
    fallback email that failed, which is always written."""
    if email_failed:
        logger.error("sms_not_sent reason=%s purpose=%s fallback_email=failed", reason, purpose)
        return
    if reason == "gateway_offline":
        if cache.add("sms:offline-logged", 1, 3600):
            logger.error("sms_gateway_offline: no phone has asked for texts in the last minute.")
        return
    logger.error("sms_not_sent reason=%s purpose=%s", reason, purpose)
