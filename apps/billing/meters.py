"""Plan usage meters: what the site has used of its plan, for display only.

Reads and writes nothing else: no table, no cache, no snapshot, no charge, no block. The storage
figure is the media library's stored files; the orders figure is the site's shop and Membership
Pack orders placed in the current period. Neither feeds the usage ledger in `usage.py`.
"""
from datetime import datetime

from django.db.models import Sum

from apps.billing import clock, plan, services
from apps.billing.models import Subscription
from apps.media_library.models import MediaAsset
from apps.pack.models import PackOrder
from apps.shop import constants as shop_constants
from apps.shop.models import ShopOrder

BILLING_MONTH, TRIAL, CALENDAR_MONTH = "billing_month", "trial", "calendar_month"
# Not in plan.py: a guard there forbids any plan name with "ORDER" (no order is ever charged).
ORDERS_INCLUDED_PER_MONTH = 500  # shown on the meter; display only


def storage_used_bytes(site):
    """Every media-library row of the site, images and videos, active or not and published or
    not: a hidden or unpublished row still holds its stored file until it is deleted (deleting
    removes the file). Thumbnails, posters and other pictures are not counted."""
    return MediaAsset.objects.filter(site=site).aggregate(total=Sum("file_size"))["total"] or 0


def period_at(subscription, now):
    """(start, end, kind): the paid cycle's allowance month when one covers `now`; else the trial
    window while the site is in its trial; else the current Manila calendar month."""
    allowance = services.allowance_period_at(subscription, now)
    if allowance is not None:
        return allowance["start_at"], allowance["end_at"], BILLING_MONTH
    if (
        subscription.state == Subscription.State.TRIAL_ACTIVE
        and subscription.trial_start_at <= now < subscription.trial_end_at
    ):
        return subscription.trial_start_at, subscription.trial_end_at, TRIAL
    local = now.astimezone(clock.MANILA)
    start = datetime(local.year, local.month, 1, tzinfo=clock.MANILA)
    end = datetime(local.year + (local.month == 12), local.month % 12 + 1, 1, tzinfo=clock.MANILA)
    return start, end, CALENDAR_MONTH


def orders_in(site, start, end):
    """Shop orders (cancelled ones left out) plus Membership Pack orders placed in [start, end).
    A pack order has no cancelled status (a failed payment is its own status and stays counted),
    so none is left out. MemberOrderLog is not counted."""
    shop = (
        ShopOrder.objects.filter(site=site, created_at__gte=start, created_at__lt=end)
        .exclude(payment_status=shop_constants.CANCELLED)
        .count()
    )
    pack = PackOrder.objects.filter(site=site, created_at__gte=start, created_at__lt=end).count()
    return shop + pack


def read(subscription, now):
    site = subscription.site
    start, end, kind = period_at(subscription, now)
    return {
        "storage": {"used_bytes": storage_used_bytes(site), "limit_bytes": plan.STORAGE_LIMIT_BYTES},
        "orders": {
            "count": orders_in(site, start, end),
            "limit": ORDERS_INCLUDED_PER_MONTH,
            "period_start": start,
            "period_end": end,
            "period_kind": kind,
        },
    }
