"""Member one-time codes. A six-digit code, stored only as a keyed hash (HMAC-SHA256), 10 minutes, 5 wrong guesses burn it,
single use, bound to one browser (a binding the storefronts server passes as data) or to the member
session. Sends per typed contact: 3 per 10 minutes and 10 per 24 hours, decoys included, counted
under a per-contact advisory lock (PostgreSQL) and recounted after it is taken; a silent cap on the
delivery target. The per-site volume is a monitor that warns and never refuses; signed-in sends are
also limited per member. The client IP limit is advisory and secondary. Lock order everywhere:
member, challenges, sessions, holds. Ported from the approved TWCinc engine."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.common import throttle
from apps.common.errors import ApiError

from .models import Member, MemberOtpChallenge

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=10)
MAX_GUESSES = 5
RESEND_SECONDS = 60
REUSE_MIN_LEFT = timedelta(minutes=2)  # a resend inside the 10 minutes keeps the code unless under this is left
SEND_WINDOW, SEND_LIMIT = timedelta(minutes=10), 3
DAY_WINDOW, DAY_LIMIT = timedelta(hours=24), 10
SITE_MONITOR = 300  # signed-out code requests per site per hour before one operator alert; never refuses
MEMBER_LIMIT = ("member-otp-member", 10, 3600)  # signed-in sends per member per hour, refusing
IP_SEND_LIMIT = ("member-otp-ip", 10, 3600)
IP_VERIFY_LIMIT = ("member-otp-verify-ip", 30, 600)
DECOY_PREFIX = "decoy:"  # 6 + 58 hex = 64 chars; never equals a sha256 hexdigest
LOCK_TIMEOUT = "3s"
RATE_LIMITED = "Too many attempts. Please wait and try again."
ERRORS = {"expired": ("This code has expired. Request a new one.", "code_expired"),
          "burned": ("Too many wrong codes. Request a new one.", "code_burned"),
          "wrong": ("That code did not work. Please try again.", "wrong_code")}


def error(name):
    message, code = ERRORS[name]
    return ApiError(code, message, fields={"code": [message]})


def rate_limited():
    return ApiError("rate_limited", RATE_LIMITED, status=429)


def binding_hash(raw):
    return hashlib.sha256((raw or "").encode()).hexdigest()


def _code_hash(site_pk, purpose, bhash, target, code):
    """Keyed with SECRET_KEY: a copy of the database alone must not reveal a six-digit code."""
    msg = f"{site_pk}|{purpose}|{bhash}|{target}|{code}"
    return hmac.new(settings.SECRET_KEY.encode(), b"member-otp:" + msg.encode(), hashlib.sha256).hexdigest()


def _ip_send_limit(ip):
    if ip:
        throttle.hit(IP_SEND_LIMIT[0], ip, limit=IP_SEND_LIMIT[1], window=IP_SEND_LIMIT[2])


def monitor_site_volume(site, *, now=None):
    """Spec r4 3.2 per-site monitor: count signed-out code requests per site per hour (decoys included) and raise
    one operator alert past SITE_MONITOR. Never refuses or delays, and a cache error never breaks a request."""
    hour = (now or timezone.now()).strftime("%Y%m%d%H")
    key = f"members:signin-volume:{site.pk}:{hour}"
    try:
        cache.add(key, 0, 3600)
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, 3600)
            count = 1
    except Exception:  # noqa: BLE001 - a monitor never blocks sign-in
        logger.exception("member sign-in volume counter failed, site %s", site.public_id)
        return
    if count == SITE_MONITOR + 1:  # one warning an hour; this repo has no operator alert table yet
        logger.warning("More than %s member sign-in code requests in one hour on site %s.",
                       SITE_MONITOR, site.public_id)


def throttle_signed_out(site, ip):
    """Signed-out steps (sign-in): the advisory per-IP limit, then the non-refusing per-site monitor."""
    _ip_send_limit(ip)
    monitor_site_volume(site)


def throttle_signed_in(member, ip):
    """Signed-in purposes: the advisory per-IP limit, then 10 sends per member per hour (refusing; only
    that member's own session can spend it)."""
    _ip_send_limit(ip)
    throttle.hit(MEMBER_LIMIT[0], member.pk, limit=MEMBER_LIMIT[1], window=MEMBER_LIMIT[2])


def throttle_verify(ip):
    if ip:
        throttle.hit(IP_VERIFY_LIMIT[0], ip, limit=IP_VERIFY_LIMIT[1], window=IP_VERIFY_LIMIT[2])


def _lock_contact(site, value):
    if connection.vendor != "postgresql":
        return
    key = f"members_otp:{site.pk}:{hashlib.sha256(value.encode()).hexdigest()}"
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('lock_timeout')")
        previous = cursor.fetchone()[0]
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", [LOCK_TIMEOUT])
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [key])
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", [previous])


def _full(rows, now):
    return (rows.filter(created_at__gte=now - SEND_WINDOW).count() >= SEND_LIMIT
            or rows.filter(created_at__gte=now - DAY_WINDOW).count() >= DAY_LIMIT)


def _limits_reached(site, requested, now):
    """The visible bucket: keyed on the typed contact; answers 429 the same for every input."""
    return _full(MemberOtpChallenge.objects.filter(site=site, requested_contact=requested), now)


def _target_full(site, target, now):
    """The silent bucket: real sends to one delivery target. When full, the challenge is written
    as a decoy and nothing is sent, but the answer is the visible bucket's, so no answer reveals membership."""
    return _full(MemberOtpChallenge.objects.filter(site=site, target=target).exclude(code_hash__startswith=DECOY_PREFIX), now)


def _resend_waiting(site, bhash, purpose, requested, now):
    return MemberOtpChallenge.objects.filter(
        site=site, binding_hash=bhash, purpose=purpose, requested_contact=requested,
        created_at__gt=now - timedelta(seconds=RESEND_SECONDS), created_at__lte=now).exists()


def _channel_waiting(site, bhash, purpose, requested, channel, now):
    """A 60-second wait per channel: a second send by the same channel inside it is refused (decoys carry the same
    channel, so the answer tells nothing)."""
    return MemberOtpChallenge.objects.filter(
        site=site, binding_hash=bhash, purpose=purpose, requested_contact=requested, channel=channel,
        created_at__gt=now - timedelta(seconds=RESEND_SECONDS), created_at__lte=now).exists()


def _series_code(site, purpose, bhash, requested, series_expiry):
    """The code of one series of challenges (a resend or a channel switch inside the 10 minutes keeps it): a keyed
    hash of the series' shared expiry, so it can be sent again without being stored."""
    msg = f"otp-series:{site.pk}:{purpose}:{bhash}:{requested}:{series_expiry.isoformat()}"
    digest = hmac.new(settings.SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    return "%06d" % (int.from_bytes(digest[:8], "big") % 10 ** 6)


def _reusable_series(site, purpose, bhash, requested, member, now):
    """(expires_at, guesses so far) of this binding's live series, or None: the newest real unused challenge for
    the same member with REUSE_MIN_LEFT left and fewer than MAX_GUESSES wrong guesses across the whole series (carried
    forward on each row, so counted from the newest one)."""
    last = (MemberOtpChallenge.objects.filter(site=site, purpose=purpose, binding_hash=bhash, requested_contact=requested,
                                              member=member, used_at__isnull=True, expires_at__gt=now + REUSE_MIN_LEFT)
            .exclude(code_hash__startswith=DECOY_PREFIX).order_by("-created_at", "-pk").first())
    if last is None:
        return None
    guesses = last.attempts  # every new row is created carrying the series total, so the newest row's count is the series'
    return (last.expires_at, guesses) if guesses < MAX_GUESSES else None


def _open_qs(site, purpose, bhash):
    return MemberOtpChallenge.objects.filter(site=site, purpose=purpose, binding_hash=bhash,
                                             used_at__isnull=True, superseded_at__isnull=True)


def check_send_allowed(site, *, purpose, requested, binding_raw, now=None):
    """Read-only, before any member lookup: the same answer for every well-formed input."""
    now = now or timezone.now()
    if _limits_reached(site, requested, now) or _resend_waiting(site, binding_hash(binding_raw), purpose, requested, now):
        raise rate_limited()


def open_challenge(site, *, purpose, channel, target, binding_raw, requested=None, member=None, decoy=False,
                   replacement_for="", resend_wait=True, channel_wait=False, reuse_code=False, now=None):
    """(challenge, code); code is None for a decoy, including a real request turned into a decoy by the silent
    target bucket. Callers send only when code is not None. Supersedes this binding's open challenges of the purpose.
    channel_wait refuses a send by a channel that sent inside the last 60 seconds. reuse_code keeps the live code
    (and its wrong-guess count) for a resend or channel switch, until REUSE_MIN_LEFT before it expires."""
    now = now or timezone.now()
    requested = requested or target
    bhash = binding_hash(binding_raw)
    try:
        with transaction.atomic():
            for key in sorted({requested, target}):  # second lock on the target, sorted order
                _lock_contact(site, key)
            if _limits_reached(site, requested, now) or (resend_wait and _resend_waiting(site, bhash, purpose, requested, now)):
                raise rate_limited()
            if channel_wait and _channel_waiting(site, bhash, purpose, requested, channel, now):
                raise rate_limited()
            if not decoy and _target_full(site, target, now):
                decoy, member = True, None
            expires_at, attempts, code = now + CODE_TTL, 0, None
            if not decoy:
                live = _reusable_series(site, purpose, bhash, requested, member, now) if reuse_code else None
                if live:
                    expires_at, attempts = live
                code = _series_code(site, purpose, bhash, requested, expires_at) if reuse_code else "%06d" % secrets.randbelow(10 ** 6)
            _open_qs(site, purpose, bhash).update(superseded_at=now)
            ch = MemberOtpChallenge.objects.create(
                site=site, member=None if decoy else member, purpose=purpose, channel=channel, target=target,
                requested_contact=requested, binding_hash=bhash, expires_at=expires_at, created_at=now, attempts=attempts,
                code_hash=(DECOY_PREFIX + secrets.token_hex(29)) if decoy else _code_hash(site.pk, purpose, bhash, target, code),
                delivery="held" if decoy else "sending", replacement_for=replacement_for)
    except DatabaseError:  # lock timeout: answer as a limit, never a 500
        raise rate_limited()
    return ch, code


def latest_open(site, *, purpose, binding_raw, now=None):
    now = now or timezone.now()
    return (_open_qs(site, purpose, binding_hash(binding_raw)).filter(expires_at__gt=now)
            .order_by("-created_at", "-pk").first())


def latest_recent(site, *, purpose, binding_raw, channel=None, now=None):
    """The newest unused challenge of this binding still inside its 10 minutes, superseded or not (of one channel
    when asked)."""
    now = now or timezone.now()
    rows = MemberOtpChallenge.objects.filter(site=site, purpose=purpose, binding_hash=binding_hash(binding_raw),
                                             used_at__isnull=True, expires_at__gt=now)
    if channel:
        rows = rows.filter(channel=channel)
    return rows.order_by("-created_at", "-pk").first()


def verify_challenge(site, *, purpose, binding_raw, code, now=None):
    """The used challenge, or ApiError code_expired / code_burned / wrong_code. Never call it inside an
    atomic block: a wrong guess must stay counted when this raises."""
    now = now or timezone.now()
    bhash, code = binding_hash(binding_raw), (code or "").strip()
    outcome, ch = "expired", None
    with transaction.atomic():
        cand = _open_qs(site, purpose, bhash).order_by("-created_at", "-pk").first()
        if cand is not None:
            if cand.member_id is not None:
                Member.objects.select_for_update().filter(pk=cand.member_id).first()  # lock order: member first
            ch = MemberOtpChallenge.objects.select_for_update().filter(
                pk=cand.pk, used_at__isnull=True, superseded_at__isnull=True).first()
            if ch is None or ch.expires_at <= now:
                outcome = "expired"
            elif ch.attempts >= MAX_GUESSES:
                outcome = "burned"
            else:
                ch.attempts += 1
                good = (not ch.code_hash.startswith(DECOY_PREFIX)
                        and hmac.compare_digest(ch.code_hash, _code_hash(site.pk, purpose, bhash, ch.target, code)))
                if good:
                    ch.used_at = now
                    ch.save(update_fields=["attempts", "used_at"])
                    outcome = "ok"
                else:
                    ch.save(update_fields=["attempts"])
                    outcome = "burned" if ch.attempts >= MAX_GUESSES else "wrong"
    if outcome != "ok":
        raise error(outcome)
    return ch


def mark_held(challenge_pk):
    now = timezone.now()
    MemberOtpChallenge.objects.filter(pk=challenge_pk, superseded_at__isnull=True).update(superseded_at=now)
    MemberOtpChallenge.objects.filter(pk=challenge_pk).update(delivery="held")


def on_sms_delivery(message_id, status):
    """The SMS slice calls this from its delivery listener. A final failure supersedes the challenge."""
    rows = MemberOtpChallenge.objects.filter(sms_message_id=message_id)
    if status == "failed":
        rows.filter(superseded_at__isnull=True).update(superseded_at=timezone.now())
        rows.update(delivery="failed")
    elif status == "sent":
        rows.filter(delivery="sending").update(delivery="sent")


def start_challenge(site, *, purpose, channel, target, binding_raw, member=None, sms_category="otp_no_fallback",
                    reuse_code=False):
    """Entry for the order slice (purposes onboarding and cod_confirmation): (challenge, outcome)."""
    from . import messaging
    ch, code = open_challenge(site, purpose=purpose, channel=channel, target=target, binding_raw=binding_raw,
                              member=member, reuse_code=reuse_code)
    if code is None:  # silent target bucket full: nothing sent
        return ch, "held"
    return ch, messaging.deliver_code(ch, code, sms_category=sms_category)
