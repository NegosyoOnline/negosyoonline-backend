"""Plan usage meters: what the site has used of its plan, for display only.

Reads and writes nothing else: no table, no cache, no snapshot, no charge, no block. The storage
figure is the media library's stored files; the orders figure is the site's shop and Membership
Pack orders placed in the current period. The meter itself feeds nothing: the billing sweep
counts a finished month with `orders_in` (the same rule) when it decides the order overage.
"""
from datetime import datetime

from django.conf import settings
from django.db.models import Sum

from apps.billing import clock, plan, services
from apps.media_library.models import MediaAsset
from apps.pack import constants as pack_constants
from apps.pack.models import PackOrder
from apps.shop import constants as shop_constants
from apps.shop.models import ShopOrder

BILLING_MONTH, TRIAL, CALENDAR_MONTH = "billing_month", "trial", "calendar_month"
PACK_NOT_PAID = (pack_constants.REJECTED, pack_constants.XENDIT_EXPIRED)  # the pack's cancelled
ORDERS_INCLUDED_PER_MONTH = plan.ORDERS_INCLUDED_PER_MONTH  # the one figure lives in plan.py
# Video delivered per allowance period (V-1a): 50 decimal GB. A display line only, nothing is charged,
# so it stays out of plan.py (the allowance and price live there and in usage.py).
VIDEO_LIMIT_BYTES = 50_000_000_000


def storage_used_bytes(site):
    """Every media-library row of the site, images and videos, active or not and published or
    not: a hidden or unpublished row still holds its stored file until it is deleted (deleting
    removes the file). Thumbnails, posters and other pictures are not counted."""
    return MediaAsset.objects.filter(site=site).aggregate(total=Sum("file_size"))["total"] or 0


def period_at(subscription, now):
    """(start, end, kind): the paid cycle's allowance month when one covers `now`; else the trial
    window while `now` is inside it, whatever the state (TRIAL_ACTIVE, or ACTIVE after an early
    payment, whose first paid cycle starts at trial_end_at); else the current Manila calendar month."""
    allowance = services.allowance_period_at(subscription, now)
    if allowance is not None:
        return allowance["start_at"], allowance["end_at"], BILLING_MONTH
    if (
        subscription.trial_start_at is not None
        and subscription.trial_end_at is not None
        and subscription.trial_start_at <= now < subscription.trial_end_at
    ):
        return subscription.trial_start_at, subscription.trial_end_at, TRIAL
    local = now.astimezone(clock.MANILA)
    start = datetime(local.year, local.month, 1, tzinfo=clock.MANILA)
    end = datetime(local.year + (local.month == 12), local.month % 12 + 1, 1, tzinfo=clock.MANILA)
    return start, end, CALENDAR_MONTH


def orders_in(site, start, end):
    """Shop orders (cancelled ones left out) plus Membership Pack orders placed in [start, end).
    A pack order has no cancelled status; one whose payment never went through (rejected, or its
    Xendit invoice expired) is the pack's cancelled and is left out. Every other pack status stays
    counted, xendit_mismatch included. MemberOrderLog is not counted."""
    shop = (
        ShopOrder.objects.filter(site=site, created_at__gte=start, created_at__lt=end)
        .exclude(payment_status=shop_constants.CANCELLED)
        .count()
    )
    pack = (
        PackOrder.objects.filter(site=site, created_at__gte=start, created_at__lt=end)
        .exclude(payment_status__in=PACK_NOT_PAID)
        .count()
    )
    return shop + pack


def video_block(site, start, end):
    """The video delivery meter (V-1, display only): the bytes Bunny delivered from the store's own
    Stream library in the period, against the 50 GB allowance, as the daily task last read them
    (apps/training/video_meter.py). `read_at` is empty when no reading of this period exists yet
    (no library, no pull zone, the task has not run): the used bytes are then 0."""
    from apps.training import video_meter

    used, read_at = video_meter.reading_for(site, start)
    return {
        "used_bytes": used, "limit_bytes": VIDEO_LIMIT_BYTES, "read_at": read_at,
        "period_start": start, "period_end": end,
    }


def read(subscription, now):
    site = subscription.site
    start, end, kind = period_at(subscription, now)
    # OV-4 and OV-8: charged only in a paid billing month that starts on or after the notice date,
    # never for a trial or an exempt site, and only while the order switch is on.
    applies = bool(
        plan.orders_enabled() and kind == BILLING_MONTH and not subscription.billing_exempt
        and plan.orders_chargeable(start)
    )
    out = {
        "storage": {"used_bytes": storage_used_bytes(site), "limit_bytes": plan.STORAGE_LIMIT_BYTES},
        "orders": {
            "count": orders_in(site, start, end),
            "limit": ORDERS_INCLUDED_PER_MONTH,
            "period_start": start,
            "period_end": end,
            "period_kind": kind,
            "overage_applies": applies,
            "overage_centavos_per_order": plan.ORDER_OVERAGE_CENTAVOS_PER_ORDER,
        },
    }
    if settings.VIDEO_METER_ENABLED:
        out["video"] = video_block(site, start, end)
    return out
