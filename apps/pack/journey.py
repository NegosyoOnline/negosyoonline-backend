"""The Subscriber's buyer journey read (ClickUp z8uq9meyqk): counts of what happened on one Santé
site in a period, from rows that already exist. Nothing is stored and nothing personal is read out.
Every query is filtered by the site. Meta figures are event counts, not people."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from apps.members.models import MemberSession
from apps.meta_ads.models import MetaDelivery, MetaSettings
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission, SanteRegistration

PERIODS = {"7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}
DEFAULT_PERIOD = "30d"
RECENT_MAX = 20
FUNNEL_ORDER = ["PageView", "ViewContent", "InitiateCheckout", "AddPaymentInfo", "Purchase"]
FAILED_OUTCOMES = (MetaDelivery.FAILED, MetaDelivery.EXPIRED)


def since_for(period, now):
    span = PERIODS[period]
    return None if span is None else now - span


def _in_period(qs, field, since):
    return qs if since is None else qs.filter(**{f"{field}__gte": since})


def meta_block(site, since):
    row = MetaSettings.objects.filter(site=site).first()
    connected = bool(settings.META_EVENTS_ENABLED and row and row.pixel_id and row.token_encrypted)
    qs = _in_period(MetaDelivery.objects.filter(site=site), "created_at", since)
    counts = {}
    for item in qs.values("event_name", "outcome").annotate(n=Count("pk")):
        entry = counts.setdefault(item["event_name"], {"sent": 0, "failed": 0})
        if item["outcome"] == MetaDelivery.SENT:
            entry["sent"] += item["n"]
        elif item["outcome"] in FAILED_OUTCOMES:
            entry["failed"] += item["n"]
    names = [n for n in FUNNEL_ORDER if n in counts] + sorted(n for n in counts if n not in FUNNEL_ORDER)
    return {"connected": connected, "events": [{"name": n, **counts[n]} for n in names]}


def stages(site, since):
    orders = PackOrder.objects.filter(site=site)
    registrations = _in_period(SanteRegistration.objects.filter(site=site), "created_at", since)
    breakdown = [
        {"status": r["status"], "count": r["n"]}
        for r in registrations.values("status").annotate(n=Count("pk")).order_by("status")
    ]
    return [
        {"key": "checkout_started",
         "count": _in_period(CheckoutToken.objects.filter(site=site), "created_at", since).count()},
        {"key": "order_placed", "count": _in_period(orders, "created_at", since).count()},
        {"key": "proof_submitted",
         "count": _in_period(PackProofSubmission.objects.filter(site=site), "submitted_at", since)
         .values("order").distinct().count()},
        {"key": "payment_verified", "count": _in_period(orders.filter(verified_at__isnull=False), "verified_at", since).count()},
        {"key": "onboarding", "count": registrations.count(), "breakdown": breakdown},
        {"key": "dashboard_access",
         "count": _in_period(MemberSession.objects.filter(site=site), "created_at", since)
         .values("member").distinct().count()},
    ]


def recent_orders(site, since):
    orders = list(
        _in_period(PackOrder.objects.filter(site=site), "created_at", since).order_by("-created_at", "-pk")[:RECENT_MAX]
    )
    statuses = dict(
        SanteRegistration.objects.filter(site=site, pack_order__in=orders).values_list("pack_order_id", "status")
    )
    with_access = set(
        MemberSession.objects.filter(site=site, member_id__in=[o.member_id for o in orders if o.member_id])
        .values_list("member_id", flat=True)
    )
    return [
        {
            "order_id": o.public_id, "order_number": o.order_number, "placed_at": o.created_at.isoformat(),
            "payment_status": o.payment_status, "registration_status": statuses.get(o.pk),
            "dashboard_access": o.member_id is not None and o.member_id in with_access,
        }
        for o in orders
    ]


def journey(site, period):
    now = timezone.now()
    since = since_for(period, now)
    return {
        "period": period, "since": since.isoformat() if since else None, "generated_at": now.isoformat(),
        "meta": meta_block(site, since), "stages": stages(site, since),
        "attribution": {"per_person": "not_recorded"}, "recent": recent_orders(site, since),
    }
