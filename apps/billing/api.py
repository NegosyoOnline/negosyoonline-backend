"""Billing endpoints. Shapes here are the frontend's contract.

Every endpoint is signed-in only and reaches its site through sites.services.site_for, so a
missing site, a removed site and another account's site answer the same 404 before any
billing row is read. A site with no subscription, and every site while BILLING_ENABLED is
off, answers that 404 too.
"""
import json
from datetime import datetime

from ninja import Router, Schema

from apps.accounts.api import signed_in
from apps.accounts.services import record_event
from apps.billing import services
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.sites.services import site_for

router = Router(tags=["billing"], auth=signed_in)

WRITE_LIMIT, WRITE_WINDOW = 20, 3600  # cancels and reactivations per account per hour


class PlanOut(Schema):
    name: str
    price_centavos: int
    currency: str
    interval: str
    trial_days: int


class CycleOut(Schema):
    sequence: int
    start_at: datetime
    end_at: datetime
    settlement: str


class BillingOut(Schema):
    state: str
    storefront_served: bool
    billing_only: bool
    plan: PlanOut
    trial_start_at: datetime
    trial_end_at: datetime
    current_cycle: CycleOut
    next_due_at: datetime | None
    attention_ends_at: datetime | None
    cancel_requested_at: datetime | None
    service_end_at: datetime | None
    download_until: datetime | None
    deletion_due_at: datetime | None
    pay_by: datetime | None
    clocks_started: bool
    billing_exempt: bool
    outstanding_centavos: int
    can_cancel: bool
    can_reactivate: bool


def _nothing_sent(request):
    """A cancel and a reactivation carry nothing: no body, or `{}`. Anything else is refused."""
    raw = request.body.strip()
    if not raw:
        return
    try:
        sent = json.loads(raw)
    except ValueError:
        sent = None
    if sent != {}:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"request": ["This request takes no values."]},
        )


def _subscription_for(request, site_id):
    site = site_for(request.account, site_id)
    subscription = services.subscription_of(site)
    if subscription is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site, subscription


@router.get("/sites/{site_id}/billing/", response=BillingOut)
def billing_detail(request, site_id: str):
    site, _ = _subscription_for(request, site_id)
    return services.overview(site)


@router.post("/sites/{site_id}/billing/cancel/", response=BillingOut)
def billing_cancel(request, site_id: str):
    site, subscription = _subscription_for(request, site_id)
    _nothing_sent(request)
    hit("billing-write", request.account.pk, limit=WRITE_LIMIT, window=WRITE_WINDOW)
    services.cancel(subscription)
    record_event(request.account, "subscription_cancelled", site=site.public_id)
    return services.overview(site)


@router.post("/sites/{site_id}/billing/reactivate/", response=BillingOut)
def billing_reactivate(request, site_id: str):
    site, subscription = _subscription_for(request, site_id)
    _nothing_sent(request)
    hit("billing-write", request.account.pk, limit=WRITE_LIMIT, window=WRITE_WINDOW)
    services.withdraw_cancellation(subscription)
    record_event(request.account, "subscription_cancellation_withdrawn", site=site.public_id)
    return services.overview(site)
