"""Member sign-in by emailed one-time code.

The codes are `apps.accounts.codes` with the purpose `member:<site pk>`: 600 seconds, 5 attempts,
and the same per-address and per-IP throttles as a Subscriber's. The request answers the same
for a member, an unknown address and a decoy, and takes at least MIN_SECONDS, so neither the
answer nor the time says whether an address is a member of the site.
"""
import logging
import time

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.accounts import codes
from apps.common import throttle
from apps.common.errors import ApiError
from apps.members import contacts, sessions
from apps.members.models import PENDING, VERIFIED, Member
from apps.notifications.services import EmailNotSent, send_email

logger = logging.getLogger(__name__)

MIN_SECONDS = 1.5
SITE_MONITOR = 300  # code requests per site per hour before one warning; never refuses
IP_SEND_LIMIT = (10, 3600)
IP_VERIFY_LIMIT = (30, 600)
CATEGORY = "member_signin_code"
_clock, _sleep = time.monotonic, time.sleep


def purpose(site):
    return f"member:{site.pk}"


def _pad(started):
    remaining = MIN_SECONDS - (_clock() - started)
    if remaining > 0:
        _sleep(remaining)


def _invalid_code():
    return ApiError("invalid_code", "That code is wrong or has expired.")


def _monitor_site_volume(site):
    """Count the site's code requests, decoys included, and log one warning past SITE_MONITOR.
    A cache error never blocks a sign-in. The warning names the site, never an address."""
    hour = timezone.now().strftime("%Y%m%d%H")
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
    if count == SITE_MONITOR + 1:
        logger.warning(
            "More than %s member sign-in code requests in one hour on site %s.",
            SITE_MONITOR, site.public_id,
        )


def _signin_member(site, email):
    """The member who may be sent a code for this address: their email is verified, or pending
    until this first code proves it."""
    return Member.objects.filter(site=site, email=email, email_state__in=(VERIFIED, PENDING)).first()


def request_code(site, raw_email, ip):
    """Email a sign-in code to the member of this site with this address. Nothing in the answer,
    the time or the limits tells a member from anyone else. Raises ApiError: invalid_request for
    a malformed address, rate_limited (429) for too many requests."""
    started = _clock()
    email = contacts.clean_email(raw_email)
    if ip:
        throttle.hit("member-signin-ip", ip, limit=IP_SEND_LIMIT[0], window=IP_SEND_LIMIT[1])
    _monitor_site_volume(site)
    # Issued for every address, so the cooldown and the hourly limit answer alike for all.
    code = codes.issue_code(email, ip or "unknown", purpose(site))
    member = _signin_member(site, email)
    if member is not None:
        try:
            send_email(
                to=member.email,
                category=CATEGORY,
                subject=f"Your {site.display_name} sign-in code",
                body=(
                    f"Your sign-in code is {code}. It expires in 10 minutes.\n\n"
                    "If you did not ask for this code, you can ignore this email."
                ),
                sender_name=site.display_name,
            )
        except (EmailNotSent, ValueError):
            logger.warning("member sign-in code email not sent, site %s", site.public_id)
    _pad(started)


def verify_code(site, raw_email, code, ip):
    """(member, raw session token). The first valid code marks a pending email verified. Wrong,
    expired, used and unknown all answer invalid_code."""
    email = contacts.clean_email(raw_email)
    if ip:
        throttle.hit("member-verify-ip", ip, limit=IP_VERIFY_LIMIT[0], window=IP_VERIFY_LIMIT[1])
    if not codes.check_code(email, (code or "").strip(), purpose(site)):
        raise _invalid_code()
    now = timezone.now()
    with transaction.atomic():
        member = (
            Member.objects.select_for_update()
            .filter(site=site, email=email, email_state__in=(VERIFIED, PENDING))
            .first()
        )
        if member is None:  # no such member, or the contact changed after the code was sent
            raise _invalid_code()
        if not codes.claim_code(email, purpose(site)):  # a simultaneous request used it first
            raise _invalid_code()
        if member.email_state == PENDING:
            member.email_state, member.email_verified_at = VERIFIED, now
            member.save(update_fields=["email_state", "email_verified_at", "updated_at"])
        _, raw = sessions.create_session(site, member, ip=ip, now=now)
    return member, raw
