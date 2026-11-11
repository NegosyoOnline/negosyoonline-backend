"""The storage usage ledger (Billing Build B4). Nothing outside billing computes an allowance
or an overage: media upload calls `record_storage_snapshot` and nothing else.

NegosyoOnline does not meter or charge orders (Coach Ed, 2026-11-11): a Subscriber's sales are
their own business. Storage is platform usage. A snapshot is taken once, at the exact end of a
monthly allowance period, is immutable, and is never recomputed.

Every decision is written under the subscription's row lock, the lock the lifecycle writes
take, so a snapshot and a payment outcome are serialised.
"""
import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.billing import clock, plan
from apps.billing.models import BillingCycle, ChargeLine, Subscription, UsageEntry, UsageEvent
from apps.sites.models import Site

logger = logging.getLogger(__name__)

Status = UsageEntry.Status
Settlement = BillingCycle.Settlement
REFERENCE_MAX_LENGTH = 64


@dataclass(frozen=True)
class UsageResult:
    """What the intake decided. `status` is a ledger status for a written row, or one of
    `disabled`, `not_metered`, `no_subscription`, `within_allowance`, `not_counted`."""

    status: str
    reason: str = ""
    charge_centavos: int = 0
    replayed: bool = False


def _result(entry, replayed=False):
    charge = getattr(entry, "charge", None) if entry.status == Status.COUNTED else None
    return UsageResult(
        entry.status, entry.reason, charge.amount_centavos if charge else 0, replayed
    )


def started_gb(measured_bytes):
    """The started GB over the allowance: any fraction rounds up to the next whole GB."""
    over = measured_bytes - plan.STORAGE_INCLUDED_GB * plan.GB_BYTES
    return -(-over // plan.GB_BYTES) if over > 0 else 0


def _period_of(subscription, period_end_at):
    """(cycle, period index) whose monthly allowance period ends at `period_end_at`.

    (cycle, None) means the instant is inside a cycle but is not the end of one of its
    periods; (None, None) means no cycle holds it.
    """
    cycle = (
        subscription.cycles.filter(start_at__lt=period_end_at, end_at__gte=period_end_at)
        .order_by("-sequence")
        .first()
    )
    if cycle is None:
        return None, None
    if cycle.kind == BillingCycle.Kind.TRIAL:
        return cycle, (1 if cycle.end_at == period_end_at else None)
    bought = plan.version_of(cycle.plan_code, cycle.plan_version)
    periods = clock.allowance_periods(
        cycle.start_at, subscription.anchor_day, cycle.term_months, bought.allowance_periods
    )
    for index, (_, end) in enumerate(periods, start=1):
        if end == period_end_at:
            return cycle, index
    return cycle, None


def _event(entry, code, key, actor=UsageEvent.Actor.INTAKE, detail=None):
    return UsageEvent.objects.create(
        entry=entry, code=code, actor=actor, idempotency_key=key, detail=detail or {}
    )


def make_charge(entry, labels=()):
    """The overage line of a counted snapshot: one per entry, so a replay cannot charge twice."""
    gigabytes = started_gb(entry.quantity)
    return ChargeLine.objects.create(
        subscription_id=entry.subscription_id,
        cycle=entry.cycle,
        period_index=entry.period_index,
        entry=entry,
        quantity=gigabytes,
        amount_centavos=gigabytes * plan.STORAGE_OVERAGE_CENTAVOS_PER_GB,
        currency=plan.CURRENCY,
        labels=list(labels),
    )


def record_storage_snapshot(
    site, *, source_reference, period_end_at, measured_bytes, measured_at, now=None
):
    """Decide one storage snapshot of `site`, taken at the end of a monthly allowance period.

    Returns a `UsageResult`; it raises only for a malformed call, never for a business
    outcome, so a producer has nothing to retry. The producer passes merchant-uploaded
    business media only: billing cannot tell a receipt from a banner.
    """
    if not plan.usage_enabled():
        return UsageResult("disabled")
    if not plan.STORAGE_METERED:
        return UsageResult("not_metered")  # L13: not metered until the media task decides
    if (
        not isinstance(source_reference, str)
        or not 0 < len(source_reference) <= REFERENCE_MAX_LENGTH
    ):
        raise ValueError("A snapshot needs the producer's own reference, at most 64 characters.")
    if isinstance(measured_bytes, bool) or not isinstance(measured_bytes, int) or measured_bytes < 0:
        raise ValueError("A snapshot needs the measured size in bytes.")
    for value in (period_end_at, measured_at):
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("A snapshot needs times with a time zone.")
    now = now or timezone.now()
    from apps.billing import services  # the lifecycle imports this module for its hooks

    with transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update().filter(site=site).first()
            if isinstance(site, Site) else None
        )
        if subscription is None:
            return UsageResult("no_subscription")
        previous = UsageEntry.objects.filter(source_reference=source_reference).first()
        if previous is not None:
            if previous.subscription_id != subscription.pk:
                return UsageResult("not_counted", "reference_reused")
            return _result(previous, replayed=True)
        services.catch_up(subscription, now)
        if measured_at != period_end_at:
            return UsageResult("not_counted", "not_at_period_end")
        if measured_at > now:
            return UsageResult("not_counted", "in_the_future")
        cycle, index = _period_of(subscription, period_end_at)
        if cycle is not None and index is None:
            return UsageResult("not_counted", "not_at_period_end")
        if cycle is not None:
            earlier = UsageEntry.objects.filter(cycle=cycle, period_index=index).first()
            if earlier is not None:  # one snapshot per period, ever: nothing is recomputed
                return _result(earlier, replayed=True)
        if started_gb(measured_bytes) == 0:
            return UsageResult("within_allowance")  # no source record at or below the allowance
        status, reason = _route(subscription, cycle, period_end_at, now)
        entry = UsageEntry.objects.create(
            subscription=subscription, cycle=cycle, period_index=index,
            source_reference=source_reference, quantity=measured_bytes,
            occurred_at=measured_at, received_at=now, status=status, reason=reason,
            detail={"period_end_at": period_end_at.isoformat()},
        )
        if status == Status.COUNTED:
            make_charge(entry)
        _event(entry, f"recorded_{status}", f"intake:{source_reference}", detail={"reason": reason})
        return _result(entry)


def _route(subscription, cycle, period_end_at, now):
    """The status and reason a snapshot gets: the order of the rules is the order of the plan."""
    if subscription.billing_exempt:
        return Status.NOT_BILLABLE, "test_site"  # Q1: skipped and auditable
    if cycle is None:
        return Status.NEEDS_REVIEW, "no_cycle"  # L6: never charged, never discarded
    if cycle.kind == BillingCycle.Kind.TRIAL:
        return Status.NOT_BILLABLE, "trial_cycle"
    ended = subscription.service_ended_at
    if cycle.settlement == Settlement.PAID and ended is not None and ended < period_end_at:
        return Status.NEEDS_REVIEW, "after_service_end"  # L6, and L20 for a suspended site
    if now > period_end_at + plan.LATE_ADMISSION:
        return Status.NEEDS_REVIEW, "late_over_72h"  # L2
    if cycle.settlement == Settlement.PENDING:
        return Status.HELD, "cycle_pending"  # L36
    if cycle.settlement == Settlement.LAPSED:
        return Status.WRITTEN_OFF, "cycle_lapsed"  # L5: usage in an unpaid cycle is never billed
    return Status.COUNTED, ""


def release_held(cycle, paid, *, now=None):
    """The cycle that held snapshots has resolved. Called by the lifecycle inside the
    transaction that holds the subscription's lock.

    Paid: each held snapshot becomes counted and gets its line. Lapsed: each is written off
    with its cause (L36, L41). Safe to call twice: only held rows move.
    """
    for entry in UsageEntry.objects.select_for_update().filter(cycle=cycle, status=Status.HELD):
        entry.status = Status.COUNTED if paid else Status.WRITTEN_OFF
        entry.reason = "" if paid else "cycle_lapsed"
        entry.save()
        if paid:
            make_charge(entry)
        _event(
            entry, "released_paid" if paid else "written_off_lapsed", f"release:{entry.pk}",
            UsageEvent.Actor.SWEEP,
        )
