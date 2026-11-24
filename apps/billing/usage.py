"""The usage ledger (Billing Build B4) for storage and for orders over the plan's 500. Nothing
outside billing computes an allowance or an overage: media upload calls `record_storage_snapshot`
and the billing sweep calls `produce_order_entries`, and nothing else.

OV-9 (Coach Ed, 2026-11-23): a SaaS plan with 500 orders included; a flat fee per extra order;
never a share of sales. A Subscriber's buyers still pay the Subscriber, and NegosyoOnline never
takes a percentage of sales. A storage snapshot is taken once, at the exact end of a monthly
allowance period, and an order count is taken once, 72 hours after it. Each is immutable and is
never recomputed. Orders are never blocked: nothing in checkout reads this ledger (OV-5).

Every decision is written under the subscription's row lock, the lock the lifecycle writes
take, so a snapshot and a payment outcome are serialised.
"""
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, Max, Sum
from django.utils import timezone

from apps.billing import clock, plan
from apps.common.errors import ApiError
from apps.operators import audit
from apps.billing.models import (
    BankTransferPayment, BillingCycle, ChargeLine, Subscription, UsageEntry, UsageEvent,
)
from apps.sites.models import Site

logger = logging.getLogger(__name__)

Status = UsageEntry.Status
Settlement = BillingCycle.Settlement
REFERENCE_MAX_LENGTH = 64
Kind = UsageEntry.Kind
LineKind = ChargeLine.Kind
ORDER_COUNT_LATE = timedelta(days=7)  # a count made later than this after a period needs review
ORDER_SCAN_LOOKBACK = timedelta(days=60)  # how far back the sweep looks for an uncounted period
ORDER_BILL_DUE = timedelta(days=7)  # the usage-only bill of a multi-month term is due in 7 days


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


def over_orders(count):
    """The orders over the 500 included."""
    return max(count - plan.ORDERS_INCLUDED_PER_MONTH, 0)


def make_charge(entry, labels=()):
    """The overage line of a counted entry: one per entry, so a replay cannot charge twice."""
    if entry.kind == Kind.ORDERS:
        kind, quantity = LineKind.OVERAGE_ORDERS, over_orders(entry.quantity)
        amount = quantity * plan.ORDER_OVERAGE_CENTAVOS_PER_ORDER
    else:
        kind, quantity = LineKind.OVERAGE_STORAGE, started_gb(entry.quantity)
        amount = quantity * plan.STORAGE_OVERAGE_CENTAVOS_PER_GB
    return ChargeLine.objects.create(
        subscription_id=entry.subscription_id,
        cycle=entry.cycle,
        period_index=entry.period_index,
        entry=entry,
        kind=kind,
        quantity=quantity,
        amount_centavos=amount,
        currency=plan.CURRENCY,
        labels=list(labels),
    )


def line_label(kind, quantity, amount_centavos):
    """The words of one overage line, on the owner's pages, statements and invoice lines."""
    if kind == LineKind.OVERAGE_ORDERS:
        unit = plan.pesos(plan.ORDER_OVERAGE_CENTAVOS_PER_ORDER)
        return f"Orders over {plan.ORDERS_INCLUDED_PER_MONTH}: {quantity} \u00d7 PHP {unit}"
    return f"Storage over {plan.STORAGE_INCLUDED_GB} GB: {quantity} GB"


def record_storage_snapshot(
    site, *, source_reference, period_end_at, measured_bytes, measured_at, now=None
):
    """Decide one storage snapshot of `site`, taken at the end of a monthly allowance period.

    Returns a `UsageResult`; it raises only for a malformed call, never for a business
    outcome, so a producer has nothing to retry. The producer passes merchant-uploaded
    business media only: billing cannot tell a receipt from a banner.
    """
    if not plan.usage_switch_on():
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


def period_bounds(cycle, index, anchor_day):
    """(start, end) of the 1-based monthly allowance period `index` of a paid cycle."""
    bought = plan.version_of(cycle.plan_code, cycle.plan_version)
    periods = clock.allowance_periods(
        cycle.start_at, anchor_day, cycle.term_months, bought.allowance_periods
    )
    return periods[index - 1]


def record_order_count(cycle, index, *, now=None):
    """Decide the order count of one monthly allowance period (OV-2). Writes one entry, and one
    charge line when it is counted, only if the orders placed in the period (the meter's rule,
    cancelled ones left out) are over the 500 included; a count at or below writes nothing.

    One entry per period, ever, with `source_reference` `orders:<cycle id>:<period index>` and
    `occurred_at` at the period end: a replay returns the first decision and nothing is
    recomputed. Written under the subscription's lock, like a storage snapshot.
    """
    if not plan.orders_enabled():
        return UsageResult("disabled")
    now = now or timezone.now()
    from apps.billing import meters, services  # the lifecycle and the meter import this module

    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().filter(pk=cycle.subscription_id).first()
        if subscription is None:
            return UsageResult("no_subscription")
        services.catch_up(subscription, now)
        cycle = BillingCycle.objects.get(pk=cycle.pk)
        earlier = UsageEntry.objects.filter(cycle=cycle, period_index=index, kind=Kind.ORDERS).first()
        if earlier is not None:
            return _result(earlier, replayed=True)
        start, end = period_bounds(cycle, index, subscription.anchor_day)
        if end > now - plan.LATE_ADMISSION:
            return UsageResult("not_counted", "in_the_grace")  # a cancel inside 72 hours counts
        count = meters.orders_in(subscription.site, start, end)
        if over_orders(count) == 0:
            return UsageResult("within_allowance")
        status, reason = _route(
            subscription, cycle, end, now, kind=Kind.ORDERS, period_start_at=start
        )
        entry = UsageEntry.objects.create(
            subscription=subscription, cycle=cycle, period_index=index, kind=Kind.ORDERS,
            source_reference=f"orders:{cycle.pk}:{index}", quantity=count, occurred_at=end,
            received_at=now, status=status, reason=reason,
            detail={"period_start_at": start.isoformat(), "period_end_at": end.isoformat()},
        )
        if status == Status.COUNTED:
            make_charge(entry)
        _event(
            entry, f"recorded_{status}", f"intake:{entry.source_reference}",
            UsageEvent.Actor.SWEEP, detail={"reason": reason},
        )
        return _result(entry)


def produce_order_entries(now=None):
    """The producer, run by the billing sweep: every paid monthly allowance period that ended 72
    hours or more ago and has no orders entry gets its count decided. Looks back
    ORDER_SCAN_LOOKBACK only. Returns a count per outcome. Safe to run twice; one failing
    period never stops the rest. Nothing is read while the order switch is off."""
    counts = Counter()
    if not plan.orders_enabled():
        return counts
    from apps.billing import meters  # the meter imports this module through services

    now = now or timezone.now()
    cutoff = now - plan.LATE_ADMISSION
    cycles = list(
        BillingCycle.objects.filter(
            settlement=Settlement.PAID, start_at__lt=cutoff,
            end_at__gt=cutoff - ORDER_SCAN_LOOKBACK,
        ).select_related("subscription").order_by("pk")
    )
    done = set(
        UsageEntry.objects.filter(cycle__in=cycles, kind=Kind.ORDERS)
        .values_list("cycle_id", "period_index")
    )
    for cycle in cycles:
        bought = plan.version_of(cycle.plan_code, cycle.plan_version)
        periods = clock.allowance_periods(
            cycle.start_at, cycle.subscription.anchor_day, cycle.term_months,
            bought.allowance_periods,
        )
        for index, (start, end) in enumerate(periods, start=1):
            if end > cutoff or end <= cutoff - ORDER_SCAN_LOOKBACK or (cycle.pk, index) in done:
                continue
            if over_orders(meters.orders_in(cycle.subscription.site, start, end)) == 0:
                counts["within_allowance"] += 1  # nothing is written; cheap, and no lock taken
                continue
            try:
                result = record_order_count(cycle, index, now=now)
            except Exception:
                logger.exception("Order count failed for cycle %s period %s", cycle.pk, index)
                counts["failed"] += 1
                continue
            counts[result.status] += 1
    return counts


def _route(subscription, cycle, period_end_at, now, *, kind=Kind.STORAGE, period_start_at=None):
    """The status and reason an entry gets: the order of the rules is the order of the plan.

    Orders differ from storage in two rules (OV-8): a month that starts before
    BILLING_ORDER_OVERAGE_FROM (or with it empty) is not billable (`before_notice`), and the
    producer is ours, so a count made more than 7 days after the period end needs review
    (`late_producer`) where a late storage snapshot is `late_over_72h`.
    """
    if subscription.billing_exempt:
        return Status.NOT_BILLABLE, "test_site"  # Q1: skipped and auditable
    if cycle is None:
        return Status.NEEDS_REVIEW, "no_cycle"  # L6: never charged, never discarded
    if cycle.kind == BillingCycle.Kind.TRIAL:
        return Status.NOT_BILLABLE, "trial_cycle"
    if kind == Kind.ORDERS and not plan.orders_chargeable(period_start_at or cycle.start_at):
        return Status.NOT_BILLABLE, "before_notice"
    ended = subscription.service_ended_at
    if cycle.settlement == Settlement.PAID and ended is not None and ended < period_end_at:
        return Status.NEEDS_REVIEW, "after_service_end"  # L6, and L20 for a suspended site
    if kind == Kind.ORDERS:
        if now > period_end_at + ORDER_COUNT_LATE:
            return Status.NEEDS_REVIEW, "late_producer"
    elif now > period_end_at + plan.LATE_ADMISSION:
        return Status.NEEDS_REVIEW, "late_over_72h"  # L2
    if closed_and_settled(subscription, now):
        return Status.WRITTEN_OFF, "after_final_settlement"  # L16, L21: nothing amended
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
            make_charge(entry, labels=["late_admitted"] if entry.detail.get("admitted") else ())
        _event(
            entry, "released_paid" if paid else "written_off_lapsed", f"release:{entry.pk}",
            UsageEvent.Actor.SWEEP,
        )


# Collections (Billing Build B4, PR 2). A collection takes the open lines of periods that have
# ended when its proof is sent; a line made later has a higher id and waits for the next one.

@dataclass(frozen=True)
class Due:
    centavos: int = 0
    lines: int = 0
    max_id: int = 0


def _open_lines(subscription_id):
    return ChargeLine.objects.filter(subscription_id=subscription_id, status=ChargeLine.Status.OPEN)


def enabled_kinds():
    """The line kinds whose switch is on: storage (its switch and `STORAGE_METERED`), orders
    (its own switch, independent of the storage one)."""
    kinds = []
    if plan.usage_enabled():
        kinds.append(LineKind.OVERAGE_STORAGE)
    if plan.orders_enabled():
        kinds.append(LineKind.OVERAGE_ORDERS)
    return kinds


def _frozen_rows(payment):
    """The open lines `payment` froze. Of the kinds whose switch is on; with every switch off, all
    of them, as before (a proof already sent can still be confirmed)."""
    rows = _open_lines(payment.subscription_id).filter(
        id__lte=payment.usage_line_max_id, entry__occurred_at__lte=payment.submitted_at
    )
    kinds = enabled_kinds()
    return rows.filter(kind__in=kinds) if kinds else rows


def overage_name(payment):
    """What a usage-only payment pays for, in two words: "storage", "orders" or "usage". Read
    from the lines it settled, else from the open lines it froze."""
    lines = ChargeLine.objects.filter(settled_payment=payment)
    if not lines.exists() and payment.usage_line_max_id:
        lines = _open_lines(payment.subscription_id).filter(
            id__lte=payment.usage_line_max_id, entry__occurred_at__lte=payment.submitted_at
        )
    kinds = set(lines.values_list("kind", flat=True))
    if kinds == {LineKind.OVERAGE_STORAGE}:
        return "storage"
    if kinds == {LineKind.OVERAGE_ORDERS}:
        return "orders"
    return "usage"


def due(subscription, now):
    """The overage a collection sent at `now` would include. Zero while every switch is off."""
    kinds = enabled_kinds()
    if not kinds:
        return Due()
    rows = _open_lines(subscription.pk).filter(kind__in=kinds, entry__occurred_at__lte=now)
    found = rows.aggregate(total=Sum("amount_centavos"), top=Max("id"), count=Count("id"))
    if found["total"] is None:
        return Due()
    return Due(found["total"], found["count"], found["top"])


def frozen_total(payment):
    """The open overage the proof `payment` froze: its lines that are still open."""
    if not payment.usage_line_max_id:
        return 0
    return _frozen_rows(payment).aggregate(total=Sum("amount_centavos"))["total"] or 0


def settle(payment, now):
    """Close the lines `payment` froze as settled by it. The caller holds the subscription's lock
    and has checked `frozen_total`."""
    if not payment.usage_line_max_id:
        return 0
    count = 0
    for line in _frozen_rows(payment).select_for_update():
        line.status, line.settled_payment, line.closed_at = ChargeLine.Status.SETTLED, payment, now
        line.save()
        count += 1
    return count


def write_off_line(line, reason, now, *, actor=UsageEvent.Actor.SWEEP):
    """Close an open line as written off, with its cause (L41). A closed line is left alone."""
    if line.status != ChargeLine.Status.OPEN:
        return False
    line.status, line.closed_reason, line.closed_at = ChargeLine.Status.WRITTEN_OFF, reason, now
    line.save()
    _event(line.entry, f"line_written_off_{reason}", f"writeoff:{line.pk}", actor)
    return True


def closed_and_settled(subscription, now):
    """L16, L21: the account is closed and its final usage collection was confirmed, so a later
    eligible charge is a reasoned write-off and never a debit or an amendment."""
    ended = subscription.service_ended_at
    if subscription.state != Subscription.State.CANCELLED or ended is None or ended > now:
        return False
    return (
        not _open_lines(subscription.pk).exists()
        and subscription.transfers.filter(
            plan_code=plan.USAGE_ONLY, status=BankTransferPayment.Status.CONFIRMED,
            decided_at__gte=ended,
        ).exists()
    )


def outstanding(subscription, now):
    """The total of the open lines of ended periods: `outstanding_centavos` of GET billing/."""
    return due(subscription, now).centavos


def write_off_unreturned(now):
    """R-4c: a suspended account that never came back owes nothing. At its deletion date the
    open lines are written off. Returns the number written off. Safe to run twice."""
    count = 0
    stale = Subscription.objects.filter(
        state=Subscription.State.SUSPENDED, deletion_due_at__lte=now,
        charges__status=ChargeLine.Status.OPEN,
    ).distinct()
    for subscription_id in stale.values_list("pk", flat=True):
        with transaction.atomic():
            Subscription.objects.select_for_update().get(pk=subscription_id)
            for line in _open_lines(subscription_id).select_for_update():
                count += write_off_line(line, "suspension_unreturned", now)
    return count


# Operator review (Billing Build B4, PR 3). An operator names a row by its producer's
# `source_reference`; nothing here is a personal value. Every action takes the subscription's
# lock, is idempotent, and writes a UsageEvent and an OperatorAuditEntry.
REASON_MAX_LENGTH = 300


def _refuse(code, message, status=409):
    return ApiError(code, message, status=status)


def _reason(reason):
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason or len(reason) > REASON_MAX_LENGTH:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"reason": ["Give the reason, at most 300 characters."]},
        )
    return reason


def review_rows():
    """The rows waiting for an operator, oldest first."""
    return (
        UsageEntry.objects.filter(status=Status.NEEDS_REVIEW)
        .select_related("subscription__site", "cycle").order_by("pk")
    )


def entry_for_review(reference):
    if not isinstance(reference, str) or not reference:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"entry": ["Name the usage record."]},
        )
    entry = (
        UsageEntry.objects.select_related("subscription__site", "cycle")
        .filter(source_reference=reference).first()
    )
    if entry is None:
        raise ApiError("not_found", "Not found.", status=404)
    return entry


def _decide(reference, operator, reason, key, change, write_audit):
    """Run `change(entry, subscription)` under the lock and `write_audit(entry, subscription, reason, outcome)`
    for the audit entry; a decision already made is returned."""
    reason = _reason(reason)
    entry = entry_for_review(reference)
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=entry.subscription_id)
        entry = UsageEntry.objects.select_for_update().get(pk=entry.pk)
        if UsageEvent.objects.filter(idempotency_key=f"{key}:{entry.pk}").exists():
            return entry, False
        if entry.status != Status.NEEDS_REVIEW:
            raise _refuse("not_under_review", "This record is no longer waiting for review.")
        outcome = change(entry, subscription)
        entry.save()
        written = write_audit(entry, subscription, reason, outcome)
        _event(
            entry, f"operator_{outcome}", f"{key}:{entry.pk}", UsageEvent.Actor.OPERATOR,
            {"reason": reason, "audit": written.pk},
        )
    return entry, True


def admit(reference, operator, reason, *, now=None):
    """A valid snapshot that arrived after 72 hours, or an order count made more than 7 days late,
    is let into its period (L7, L12). Only `late_over_72h` and `late_producer`: the rows after
    service end or in no cycle are never charged (L6). Returns (entry, changed)."""
    now = now or timezone.now()

    def change(entry, subscription):
        if entry.reason not in ("late_over_72h", "late_producer"):
            raise _refuse("not_admissible", "This record cannot be charged.")
        entry.detail = {**entry.detail, "admitted": True, "was": entry.reason}
        cycle = entry.cycle
        if closed_and_settled(subscription, now):
            entry.status, entry.reason = Status.WRITTEN_OFF, "after_final_settlement"
        elif cycle.settlement == Settlement.PENDING:
            entry.status, entry.reason = Status.HELD, "cycle_pending"
        elif cycle.settlement == Settlement.LAPSED:
            entry.status, entry.reason = Status.WRITTEN_OFF, "cycle_lapsed"
        else:
            entry.status, entry.reason = Status.COUNTED, ""
            entry.save()
            make_charge(entry, labels=["late_admitted"])  # a later id: the next collection takes it
        return entry.status

    def write_audit(entry, subscription, reason, outcome):
        return audit.record(
            operator, "billing_usage_admitted", target_type="site",
            target_id=subscription.site.public_id, reason=reason,
            detail={"entry": entry.source_reference, "outcome": outcome},
        )

    return _decide(reference, operator, reason, "operator_admit", change, write_audit)


def dismiss(reference, operator, reason, *, now=None):
    """Nothing is owed for this record: kept, never charged, never reactivates service."""
    now = now or timezone.now()

    def change(entry, subscription):
        entry.detail = {**entry.detail, "was": entry.reason}
        entry.status, entry.reason = Status.WRITTEN_OFF, "reviewed_not_billable"
        return "dismissed"

    def write_audit(entry, subscription, reason, outcome):
        return audit.record(
            operator, "billing_usage_dismissed", target_type="site",
            target_id=subscription.site.public_id, reason=reason,
            detail={"entry": entry.source_reference, "was": entry.detail.get("was")},
        )

    return _decide(reference, operator, reason, "operator_dismiss", change, write_audit)


def open_items(subscription, now):
    """The open overage lines of ended periods, one item each, with the words the owner is shown.
    Only the kinds whose switch is on. `due_at` is set for an order line of a multi-month term,
    which is billed alone and due 7 days after its count was made (`received_at`); a monthly plan's lines ride on
    its next renewal, so they have none."""
    kinds = enabled_kinds()
    if not kinds:
        return []
    rows = (
        _open_lines(subscription.pk).filter(kind__in=kinds, entry__occurred_at__lte=now)
        .select_related("cycle", "entry").order_by("pk")
    )
    items = []
    for line in rows:
        start, end = period_bounds(line.cycle, line.period_index, subscription.anchor_day)
        orders = line.kind == LineKind.OVERAGE_ORDERS
        unit = (
            plan.ORDER_OVERAGE_CENTAVOS_PER_ORDER if orders else plan.STORAGE_OVERAGE_CENTAVOS_PER_GB
        )
        items.append({
            "kind": line.kind,
            "label": line_label(line.kind, line.quantity, line.amount_centavos),
            "quantity": line.quantity,
            "unit_centavos": unit,
            "amount_centavos": line.amount_centavos,
            "period_start_at": start,
            "period_end_at": end,
            "due_at": (
                line.entry.received_at + ORDER_BILL_DUE
                if orders and line.cycle.term_months > 1 else None
            ),
        })
    return items


def owner_read(subscription, now):
    """What `GET billing/usage/` answers, from stored rows. Held and under-review rows are
    counts, never charges."""
    from apps.billing import services  # the lifecycle imports this module

    latest = subscription.cycles.filter(start_at__lte=now).order_by("-sequence").first()
    found = services.allowance_period_at(subscription, now)
    last = (
        UsageEntry.objects.filter(subscription=subscription, status=Status.COUNTED)
        .select_related("charge").order_by("-occurred_at").first()
    )
    snapshot = last and {
        "period_end_at": last.occurred_at, "measured_bytes": last.quantity,
        "billed_gb": last.charge.quantity, "amount_centavos": last.charge.amount_centavos,
    }
    counts = dict(
        UsageEntry.objects.filter(
            subscription=subscription, status__in=(Status.HELD, Status.NEEDS_REVIEW)
        ).values_list("status").annotate(n=Count("id"))
    )
    return {
        "billable": found is not None and not subscription.billing_exempt,
        "cycle": latest and {
            "sequence": latest.sequence, "start_at": latest.start_at, "end_at": latest.end_at,
        },
        "period": found and {
            "index": found["index"], "start_at": found["start_at"], "end_at": found["end_at"],
        },
        "storage": {
            "metered": bool(plan.STORAGE_METERED), "included_gb": plan.STORAGE_INCLUDED_GB,
            "last_snapshot": snapshot, "held": counts.get(Status.HELD, 0),
            "pending_review": counts.get(Status.NEEDS_REVIEW, 0),
        },
        "due_centavos": due(subscription, now).centavos,
        "items": open_items(subscription, now),
    }
