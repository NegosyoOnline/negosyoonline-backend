"""Lifecycle removal (design 2.4, plan r4.3 item 5).

A claim is removed (not just dark) when its site's service has ended for good: the site is
deleted, its subscription is gone, cancelled past its service end, cancelled with no payment
method, or its stored deletion date has passed. A suspended or cancelled-but-paid site is only
dark (`served()` already refuses it) and keeps its domain. Removal puts the claim in
`removal_pending`, which leaves the served set at once; Vercel and DNS entries are taken down
only by recorded id by the next sweep. The registration is never deleted. Because it is never
removed an operator alert says auto-renew must be reviewed, and a returning Subscriber re-attaches
the same registration (a claim created after one exists starts at `registered`, B2).
"""
from django.conf import settings
from django.utils import timezone

from apps.billing.models import Subscription
from apps.domains import machine
from apps.domains.models import ClaimState, Domain

ACTIVE_CLAIM_STATES = (
    ClaimState.REGISTERED, ClaimState.ZONE, ClaimState.CONNECTING, ClaimState.ACTIVATING,
    ClaimState.LIVE, ClaimState.ATTENTION, ClaimState.FAILED, ClaimState.CONFLICT,
)


def removal_reason(site, now):
    """Why the site's domain must be removed now, or "". Unknown never removes."""
    if site.deleted_at is not None:
        return "site_deleted"
    subscription = Subscription.objects.filter(site=site).first()
    if subscription is None:
        return "no_subscription"
    if not settings.BILLING_ENABLED:
        return ""
    if subscription.state == Subscription.State.CANCELLED_NO_PAYMENT_METHOD:
        return "cancelled_no_payment_method"
    if (
        subscription.state == Subscription.State.CANCELLED
        and subscription.service_ended_at is not None and now >= subscription.service_ended_at
    ):
        return "cancelled_service_ended"
    if (
        subscription.state in (Subscription.State.SUSPENDED, Subscription.State.CANCELLED)
        and subscription.deletion_due_at is not None and now >= subscription.deletion_due_at
    ):
        return "deletion_due"
    return ""


def sweep(now=None):
    """Move every claim whose site's service has ended into `removal_pending`. Returns the count."""
    now = now or timezone.now()
    moved = 0
    for claim in Domain.objects.filter(state__in=ACTIVE_CLAIM_STATES).select_related("site"):
        reason = removal_reason(claim.site, now)
        if reason and machine.request_removal(claim, reason=reason, now=now):
            machine.alert("auto_renew_review", claim, reason=reason)  # the registration is kept
            moved += 1
    return moved
