"""Member security holds. The end is stored once at save; a hold is active while now < ends_at,
so no background job is needed. A 'payout_fallback' hold blocks only new withdrawal requests; a
hold never touches existing requests or reservations."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.common.errors import ApiError

from .models import MemberSecurityHold

MANILA = ZoneInfo("Asia/Manila")
HOLD_DURATION = timedelta(hours=24)
ACTIONS = ("payout_destination_change", "withdrawal_request", "record_payout")
# A recovery hold blocks all three; payout_fallback blocks new withdrawal requests only: destination
# changes and paying existing requests stay allowed.
BLOCKS = {"recovery": frozenset(ACTIONS), "payout_fallback": frozenset({"withdrawal_request"})}
FALLBACK_BANNER = "New withdrawal requests are paused until {end} because your payout account was changed."


def display(dt):
    local = dt.astimezone(MANILA)
    hour = local.hour % 12 or 12
    return f"{local.day} {local:%b %Y}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'} (Philippine time)"


def banner(hold):
    if hold.kind == "payout_fallback":
        return FALLBACK_BANNER.format(end=display(hold.ends_at))
    return f"For your security, payout changes and withdrawals are paused until {display(hold.ends_at)}."


def _active(member, now):
    return MemberSecurityHold.objects.filter(member=member, site_id=member.site_id, ends_at__gt=now)


def current_hold(member, *, now=None):
    return _active(member, now or timezone.now()).order_by("-ends_at", "-pk").first()


def active_hold(member, action, *, now=None):
    if action not in ACTIONS:
        raise ValueError(f"unknown hold action {action!r}")
    kinds = [kind for kind, blocked in BLOCKS.items() if action in blocked]
    return _active(member, now or timezone.now()).filter(kind__in=kinds).order_by("-ends_at", "-pk").first()


def assert_no_hold(member, action, *, now=None):
    hold = active_hold(member, action, now=now)
    if hold is not None:
        raise ApiError("security_hold", banner(hold), status=409)


def start_recovery_hold(member, audit, *, now):
    return MemberSecurityHold.objects.create(site_id=member.site_id, member=member, kind="recovery", starts_at=now,
                                             ends_at=now + HOLD_DURATION, audit=audit)


def start_payout_fallback_hold(member, destination, *, now):
    """A destination confirmed through the non-default contact holds new withdrawal requests for 24 hours.
    Call inside the caller's transaction, after the member row lock."""
    return MemberSecurityHold.objects.create(site_id=member.site_id, member=member, kind="payout_fallback",
                                             starts_at=now, ends_at=now + HOLD_DURATION,
                                             payout_destination_ref=destination.pk)


def hold_out(hold):
    if hold is None:
        return None
    return {"ends_at": hold.ends_at.isoformat(), "ends_display": display(hold.ends_at),
            "blocks": sorted(BLOCKS[hold.kind])}
