"""The subscription lifecycle. Other apps call these functions, never the tables.

`position` answers every "what state is this site in" question from the stored rows and the
current time; it writes nothing. Every write goes through `_write` and `_apply`, under the
subscription's row lock, after `catch_up` has written the time-driven transitions that were due.
"""
import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.utils import timezone

from apps.billing import clock, methods, plan, proofs, usage
from apps.billing.models import (
    BankTransferEvent, BankTransferPayment, BillingCycle, RenewalConsent, Subscription,
    SubscriptionTransition,
)
from apps.common.errors import ApiError
from apps.common.private_files import StoreError
from apps.common.ids import is_public_id
from apps.operators import audit
from apps.sites.models import Onboarding, Site

logger = logging.getLogger(__name__)

State = Subscription.State
Method = Subscription.MethodAtTrialEnd
Actor = SubscriptionTransition.Actor
Settlement = BillingCycle.Settlement
Kind = BillingCycle.Kind
TransferStatus = BankTransferPayment.Status

ENDED_STATES = (State.SUSPENDED, State.CANCELLED, State.CANCELLED_NO_PAYMENT_METHOD)
# The states each transition may start from.
FROM_STATES = {
    "trial_started": (),
    "trial_ended_no_method": (State.TRIAL_ACTIVE,),
    "first_payment_succeeded": (State.TRIAL_ACTIVE,),
    "first_payment_failed": (State.TRIAL_ACTIVE,),
    "renewed": (State.ACTIVE,),
    "renewal_failed": (State.ACTIVE,),
    "recovered": (State.PAYMENT_REQUIRES_ATTENTION,),
    "suspended_after_attention": (State.PAYMENT_REQUIRES_ATTENTION,),
    "suspended_no_outcome": (State.ACTIVE, State.TRIAL_ACTIVE),
    "cancelled": (
        State.TRIAL_ACTIVE, State.ACTIVE, State.PAYMENT_REQUIRES_ATTENTION, State.SUSPENDED,
    ),
    "cancellation_withdrawn": (State.CANCELLED,),
    "reactivated": ENDED_STATES,
    "clocks_started": ENDED_STATES,
}
# What each time-driven transition leads to.
DUE_TO_STATE = {
    "trial_ended_no_method": State.CANCELLED_NO_PAYMENT_METHOD,
    "suspended_after_attention": State.SUSPENDED,
    "suspended_no_outcome": State.SUSPENDED,
}
REFERENCE_MAX_LENGTH = 64


@dataclass(frozen=True)
class Position:
    """Where a site stands at one instant."""

    state: str | None
    storefront_served: bool
    billing_only: bool
    next_change_at: datetime | None = None  # when a served site would next go off
    subscription: Subscription | None = None
    at: datetime | None = None  # the instant this was read for


NOT_SERVED = Position(state=None, storefront_served=False, billing_only=False)
PAID_TERM_RUNNING = "paid_term_running"
PAYMENT_UNDER_REVIEW = "payment_under_review"


@dataclass(frozen=True)
class DisposalStep:
    """What an account disposal does to the account's subscriptions.

    `cancelled` holds the public ids of the sites whose subscription it cancelled, or would
    cancel on a dry run. `reasons` is empty when the disposal may proceed.
    """

    cancelled: tuple = ()
    reasons: tuple = ()


def _conflict(code, message):
    return ApiError(code, message, status=409)


# Reading

def position(site, at=None):
    """The site's billing position at `at`. Writes nothing. Unknown fails closed."""
    if not settings.BILLING_ENABLED:
        return NOT_SERVED
    try:
        return _read_position(site, at or timezone.now())
    except Exception:
        logger.exception("Billing position could not be read for site %s", site.pk)
        return NOT_SERVED


def _read_position(site, now):
    subscription = Subscription.objects.filter(site=site).first()
    if subscription is None:
        return NOT_SERVED
    if subscription.billing_exempt:
        return Position(subscription.state, True, False, None, subscription, now)
    due = _due(subscription, now)
    state = DUE_TO_STATE[due[0]] if due else subscription.state
    if state == State.CANCELLED:
        served = now < subscription.service_ended_at
    else:
        served = state not in ENDED_STATES
    return Position(
        state=state,
        storefront_served=served,
        billing_only=not served,
        next_change_at=_next_change_at(subscription, state, now) if served else None,
        subscription=subscription,
        at=now,
    )


def _next_change_at(subscription, state, now):
    if state == State.TRIAL_ACTIVE:
        if now < subscription.trial_end_at:
            return subscription.trial_end_at
        return subscription.trial_end_at + plan.GRACE
    if state == State.ACTIVE:
        return _latest_cycle(subscription).end_at + plan.GRACE
    if state == State.PAYMENT_REQUIRES_ATTENTION:
        return subscription.attention_started_at + plan.GRACE
    return subscription.service_ended_at  # cancelled, still inside its paid period


def _due(subscription, now):
    """The time-driven transition that is due at `now`, as (code, effective time), or None."""
    if subscription.billing_exempt:
        return None
    if subscription.state == State.TRIAL_ACTIVE:
        trial_end = subscription.trial_end_at
        if now < trial_end:
            return None
        if not _method_at_trial_end(subscription):
            return "trial_ended_no_method", trial_end
        if now >= trial_end + plan.GRACE:
            return "suspended_no_outcome", trial_end + plan.GRACE
    elif subscription.state == State.ACTIVE:
        suspend_at = _latest_cycle(subscription).end_at + plan.GRACE
        if now >= suspend_at:
            return "suspended_no_outcome", suspend_at
    elif subscription.state == State.PAYMENT_REQUIRES_ATTENTION:
        suspend_at = subscription.attention_started_at + plan.GRACE
        if now >= suspend_at:
            return "suspended_after_attention", suspend_at
    return None


def _method_at_trial_end(subscription):
    if subscription.method_at_trial_end != Method.UNDECIDED:
        return subscription.method_at_trial_end == Method.YES
    return bool(methods.active_at(subscription.site, subscription.trial_end_at))


def _latest_cycle(subscription):
    return subscription.cycles.order_by("-sequence").first()


def _ever_paid(subscription):
    return subscription.cycles.filter(settlement=Settlement.PAID).exists()


def state_filter(now, states):
    """A condition on Site rows: the site's subscription is in one of `states` at `now`.

    It mirrors `position` in one query, for a read over many sites. A site with no
    subscription never matches. An exempt subscription matches on its stored state, as
    `position` reports it. A trial past its end whose answer at trial end is not yet frozen
    counts as having had a payment method only when a proof was under review at that
    instant, which is what `methods.active_at` answers. While payment collection is off the
    payment table is not read.
    """
    cutoff = now - plan.GRACE
    cycle_still_in_grace = Exists(
        BillingCycle.objects.filter(subscription=OuterRef("pk"), end_at__gt=cutoff)
    )
    trial = Q(state=State.TRIAL_ACTIVE)
    had_method = Q(method_at_trial_end=Method.YES)
    if plan.collection_live():
        had_method |= Q(method_at_trial_end=Method.UNDECIDED) & Exists(
            BankTransferPayment.objects.filter(
                methods.under_review_at(OuterRef("trial_end_at")), subscription=OuterRef("pk")
            )
        )
    reported = {
        State.TRIAL_ACTIVE: trial & (
            Q(trial_end_at__gt=now) | (had_method & Q(trial_end_at__gt=cutoff))
        ),
        State.CANCELLED_NO_PAYMENT_METHOD: Q(state=State.CANCELLED_NO_PAYMENT_METHOD) | (
            trial & Q(trial_end_at__lte=now) & ~had_method
        ),
        State.ACTIVE: Q(state=State.ACTIVE) & Q(cycle_still_in_grace),
        State.PAYMENT_REQUIRES_ATTENTION: Q(
            state=State.PAYMENT_REQUIRES_ATTENTION, attention_started_at__gt=cutoff
        ),
        State.SUSPENDED: (
            Q(state=State.SUSPENDED)
            | (trial & had_method & Q(trial_end_at__lte=cutoff))
            | (Q(state=State.ACTIVE) & ~Q(cycle_still_in_grace))
            | Q(state=State.PAYMENT_REQUIRES_ATTENTION, attention_started_at__lte=cutoff)
        ),
        State.CANCELLED: Q(state=State.CANCELLED),
    }
    computed = Q(pk__in=[])
    for state in states:
        computed |= reported[state]
    condition = (Q(billing_exempt=True) & Q(state__in=list(states))) | (
        Q(billing_exempt=False) & computed
    )
    return Q(Exists(Subscription.objects.filter(condition, site=OuterRef("pk"))))


def subscription_of(site):
    """The site's subscription, or None: billing is off, or the site has none yet."""
    if not settings.BILLING_ENABLED:
        return None
    return Subscription.objects.filter(site=site).first()


def overview(site, at=None):
    """What `GET billing/` answers for the site at `at`, or None. Writes nothing.

    The state and the dates are the ones in force at `at`, even when the sweep has not yet
    written them.
    """
    subscription = subscription_of(site)
    if subscription is None:
        return None
    now = at or timezone.now()
    due = _due(subscription, now)
    state = DUE_TO_STATE[due[0]] if due else subscription.state
    exempt = subscription.billing_exempt
    service_end = subscription.service_ended_at
    download_until, deletion_due = subscription.download_until, subscription.deletion_due_at
    if due:
        service_end = due[1]
        download_until, deletion_due = _dates_after(subscription, service_end)
    if state == State.CANCELLED:
        served = now < service_end
    else:
        served = state not in ENDED_STATES
    served = served or exempt
    latest = _latest_cycle(subscription)
    # A cycle paid ahead has not started yet: the current one is the one that is running.
    cycle = latest
    if latest.start_at > now:
        cycle = subscription.cycles.filter(start_at__lte=now).order_by("-sequence").first() or latest
    next_due_at = attention_ends_at = None
    if state == State.TRIAL_ACTIVE:
        next_due_at = subscription.trial_end_at
    elif state == State.ACTIVE:
        next_due_at = latest.end_at
    elif state == State.PAYMENT_REQUIRES_ATTENTION:
        next_due_at = subscription.attention_started_at
        attention_ends_at = subscription.attention_started_at + plan.GRACE
    return {
        "state": state,
        "storefront_served": served,
        "billing_only": not served,
        "plan": {
            "name": plan.PLAN_NAME,
            "price_centavos": plan.PRICE_CENTAVOS,
            "currency": plan.CURRENCY,
            "interval": "month",
            "trial_days": plan.TRIAL_DAYS,
        },
        "trial_start_at": subscription.trial_start_at,
        "trial_end_at": subscription.trial_end_at,
        "current_cycle": {
            "sequence": cycle.sequence,
            "start_at": cycle.start_at,
            "end_at": cycle.end_at,
            "settlement": cycle.settlement,
        },
        "next_due_at": next_due_at,
        "attention_ends_at": attention_ends_at,
        "cancel_requested_at": subscription.cancel_requested_at,
        "service_end_at": service_end,
        "download_until": download_until,
        "deletion_due_at": deletion_due,
        "pay_by": deletion_due,  # the last date to pay is always the deletion date
        "clocks_started": deletion_due is not None,
        "billing_exempt": exempt,
        "outstanding_centavos": usage.outstanding(subscription, now),
        "can_cancel": not exempt and state in FROM_STATES["cancelled"],
        "can_reactivate": not exempt and state == State.CANCELLED and now < service_end,
    }


def end_dates(site, at=None):
    """(service end, deletion due) of a site whose service has ended at `at`, as `overview`
    reads them (the stored dates, or the ones a due transition would write). None when the
    site is still served, exempt or has no subscription. Writes nothing. Deletion due is None
    while no clock has started (R-4a)."""
    subscription = subscription_of(site)
    if subscription is None or subscription.billing_exempt:
        return None
    now = at or timezone.now()
    due = _due(subscription, now)
    state = DUE_TO_STATE[due[0]] if due else subscription.state
    if state not in ENDED_STATES:
        return None
    if due:
        service_end = due[1]
        deletion_due = _dates_after(subscription, service_end)[1]
    else:
        service_end, deletion_due = subscription.service_ended_at, subscription.deletion_due_at
    if service_end is None or (state == State.CANCELLED and now < service_end):
        return None  # a cancellation inside the paid period is still served
    return service_end, deletion_due


def deletion_point(site, at=None):
    """The instant the site's data is due for deletion, or None when there is no clock: the site
    is served, exempt, has no subscription, or never paid and collection is not live yet.

    The stored `deletion_due_at`, or the date a time-driven transition that is due but not yet
    swept would write, so the answer never waits for the sweep. Writes nothing."""
    found = end_dates(site, at)
    return None if found is None else found[1]


def deletion_reached(site, at=None):
    """Whether the site's deletion point has been reached at `at` (now). False when there is no
    clock. Writes nothing."""
    now = at or timezone.now()
    point = deletion_point(site, now)
    return point is not None and now >= point


# Writing

def _write(subscription, at, change, *, key=None):
    """Run one write under the subscription's row lock and return its transition.

    `key` is the write's idempotency key, or a function of the locked row that gives it: a
    key already written returns its transition and changes nothing. The time-driven
    transitions due at `at` are written first and are kept even when `change` refuses, so a
    refusal never leaves the stored rows behind.
    """
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if key is not None:
            key = key(subscription) if callable(key) else key
            done = _already_done(subscription, key)
            if done is not None:
                return done
        _refuse_exempt(subscription)
        catch_up(subscription, at)
        try:
            with transaction.atomic():
                return change(subscription, key) if key is not None else change(subscription)
        except ApiError as refusal:
            refused = refusal
    raise refused


def _apply(subscription, code, to_state, key, effective_at, actor, detail=None):
    """Write one transition and the subscription's new state. The caller holds the row lock."""
    from_state = "" if code == "trial_started" else subscription.state
    if code != "trial_started" and from_state not in FROM_STATES[code]:
        raise _conflict("invalid_transition", "The subscription is not in a state that allows this.")
    transition = SubscriptionTransition.objects.create(
        subscription=subscription,
        code=code,
        from_state=from_state,
        to_state=to_state,
        actor=actor,
        idempotency_key=key,
        effective_at=effective_at,
        detail=detail or {},
    )
    subscription.state = to_state
    subscription.save()
    return transition


def _already_done(subscription, key):
    """The transition a key already wrote, so a replay changes nothing."""
    transition = SubscriptionTransition.objects.filter(idempotency_key=key).first()
    if transition is not None and transition.subscription_id != subscription.pk:
        raise _conflict("reference_reused", "This reference belongs to another subscription.")
    return transition


def _refuse_exempt(subscription):
    if subscription.billing_exempt:
        raise _conflict("billing_exempt", "This site is exempt from billing.")


def _require(subscription, code):
    if subscription.state not in FROM_STATES[code]:
        raise _conflict("invalid_transition", "The subscription is not in a state that allows this.")


def _freeze_method(subscription, active):
    """Record, once, whether a payment method was active at the trial-end instant."""
    value = Method.YES if active else Method.NO
    if subscription.method_at_trial_end not in (Method.UNDECIDED, value):
        raise RuntimeError("The answer at trial end is frozen and cannot be changed.")
    subscription.method_at_trial_end = value


def _end_service(subscription, at):
    """Service ended at `at`: record it and the dates that count from it.

    A subscription that never paid gets no dates until payment collection is live (R-4a).
    """
    subscription.service_ended_at = at
    subscription.download_until, subscription.deletion_due_at = _dates_after(subscription, at)


def _dates_after(subscription, at):
    """(download until, deletion due) for a service that ended at `at`."""
    ever_paid = _ever_paid(subscription)
    if not ever_paid and not plan.collection_live():
        return None, None
    if not ever_paid:
        # A site made after its trial had ended could not have been paid for earlier, so its
        # days to pay count from its own creation (B2a, question 9).
        created_at = subscription.site.created_at
        if created_at > subscription.trial_end_at:
            at = max(at, created_at)
    return at + plan.DOWNLOAD_WINDOW, at + (
        plan.DELETION_AFTER_PAID if ever_paid else plan.DELETION_NEVER_PAID
    )


def _clocks_due(subscription):
    """Whether the dates of an ended subscription that never paid are still to be started.

    They are left empty while NegosyoOnline cannot be paid (R-4a) and start, once, when
    payment collection first goes live.
    """
    return (
        not subscription.billing_exempt
        and subscription.state in ENDED_STATES
        and subscription.service_ended_at is not None
        and subscription.deletion_due_at is None
        and plan.collection_live()
    )


def _clear_service_end(subscription):
    subscription.cancel_requested_at = None
    subscription.service_ended_at = None
    subscription.download_until = None
    subscription.deletion_due_at = None


def _open_cycle(subscription, start_at, settlement, *, paid_at=None, reference="", bought=None):
    """The next paid cycle, from `start_at` to 3:00 PM Manila on the anchor day of the month
    `bought.months` months on, with the plan, version, price and term it was opened under.

    A cycle opened for a payment that has not happened (pending, lapsed) holds the terms that
    were due: the plan that follows the latest cycle.
    """
    bought = bought or plan.following(_latest_cycle(subscription))
    return BillingCycle.objects.create(
        subscription=subscription,
        sequence=_latest_cycle(subscription).sequence + 1,
        kind=Kind.PAID,
        start_at=start_at,
        end_at=clock.cycle_end(start_at, subscription.anchor_day, bought.months),
        settlement=settlement,
        paid_at=paid_at,
        payment_reference=reference,
        **plan.snapshot(bought),
    )


def _open_first_cycle(subscription, settlement, **payment):
    subscription.anchor_day = clock.anchor_day(subscription.trial_end_at)
    return _open_cycle(subscription, subscription.trial_end_at, settlement, **payment)


def _lapse_pending(subscription):
    for cycle in subscription.cycles.filter(settlement=Settlement.PENDING):
        cycle.settlement = Settlement.LAPSED
        cycle.save()
        usage.release_held(cycle, paid=False)


def start_subscription(site, account, *, actor=Actor.SITE_CREATION, now=None):
    """Create the site's subscription and its trial, counted from the account's creation.

    Called inside the transaction that creates the site. A trial whose 14 days have already
    passed is ended in the same transaction, with service end at the trial-end instant.
    """
    if not settings.BILLING_ENABLED:
        return None
    now = now or timezone.now()
    with transaction.atomic():
        existing = Subscription.objects.filter(site=site).first()
        if existing is not None:
            return existing
        start = account.created_at
        subscription = Subscription.objects.create(
            site=site,
            state=State.TRIAL_ACTIVE,
            trial_start_at=start,
            trial_end_at=clock.trial_end(start),
        )
        BillingCycle.objects.create(
            subscription=subscription,
            sequence=0,
            kind=Kind.TRIAL,
            start_at=start,
            end_at=subscription.trial_end_at,
            settlement=Settlement.NOT_BILLABLE,
        )
        _apply(
            subscription, "trial_started", State.TRIAL_ACTIVE,
            f"trial_started:{subscription.pk}", start, actor, {"cycle": 0},
        )
        catch_up(subscription, now, actor=actor)
    return subscription


def catch_up(subscription, now, *, actor=Actor.SWEEP):
    """Write the time-driven transition that is due at `now`, if any, and return it.

    The caller holds the subscription's row lock. Its effective time is the rule's time,
    never `now`; the one exception is `clocks_started`, whose rule is "from now".
    """
    due = _due(subscription, now)
    if due is None:
        if (
            subscription.state == State.TRIAL_ACTIVE
            and now >= subscription.trial_end_at
            and subscription.method_at_trial_end == Method.UNDECIDED
            and not subscription.billing_exempt
        ):
            _freeze_method(subscription, True)  # a method was active; the outcome is awaited
            subscription.save()
        if _clocks_due(subscription):
            subscription.download_until, subscription.deletion_due_at = _dates_after(
                subscription, now
            )
            return _apply(
                subscription, "clocks_started", subscription.state,
                f"clocks_started:{subscription.pk}", now, actor,
            )
        return None
    code, at = due
    pk = subscription.pk
    if code == "trial_ended_no_method":
        _freeze_method(subscription, False)
        _end_service(subscription, at)
        return _apply(subscription, code, DUE_TO_STATE[code], f"trial_ended:{pk}", at, actor, {"cycle": 0})
    if code == "suspended_no_outcome":
        if subscription.state == State.TRIAL_ACTIVE:
            _freeze_method(subscription, True)
            cycle = _open_first_cycle(subscription, Settlement.LAPSED)
        else:
            cycle = _open_cycle(
                subscription, _latest_cycle(subscription).end_at, Settlement.LAPSED
            )
    else:  # suspended_after_attention
        cycle = subscription.cycles.get(settlement=Settlement.PENDING)
        _lapse_pending(subscription)
    _end_service(subscription, at)
    return _apply(
        subscription, code, DUE_TO_STATE[code], f"suspended:{pk}:{cycle.sequence}", at, actor,
        {"cycle": cycle.sequence},
    )


def cancel(subscription, *, now=None, actor=Actor.OWNER):
    """Cancel. A paid period that is still running is kept to its end; otherwise service ends now."""
    now = now or timezone.now()

    def change(subscription):
        if subscription.state in (State.CANCELLED, State.CANCELLED_NO_PAYMENT_METHOD):
            raise _conflict("already_cancelled", "This subscription is already cancelled.")
        if subscription.state == State.SUSPENDED:
            pass  # service ended at the suspension; a cancellation does not restart the clock
        elif subscription.state == State.ACTIVE and now < _latest_cycle(subscription).end_at:
            _end_service(subscription, _latest_cycle(subscription).end_at)
        else:
            _lapse_pending(subscription)  # the pending base fee is not due
            _end_service(subscription, now)
        subscription.cancel_requested_at = now
        number = subscription.transitions.filter(code="cancelled").count() + 1
        return _apply(
            subscription, "cancelled", State.CANCELLED,
            f"cancelled:{subscription.pk}:{number}", now, actor,
        )

    return _write(subscription, now, change)


def withdraw_cancellation(subscription, *, now=None):
    """Take back a cancellation whose paid period is still running. Anything else needs a payment."""
    now = now or timezone.now()

    def change(subscription):
        if subscription.state != State.CANCELLED or now >= subscription.service_ended_at:
            raise _conflict("payment_required", "A payment is needed to continue this subscription.")
        _clear_service_end(subscription)
        number = subscription.transitions.filter(code="cancellation_withdrawn").count() + 1
        return _apply(
            subscription, "cancellation_withdrawn", State.ACTIVE,
            f"withdrawn:{subscription.pk}:{number}", now, Actor.OWNER,
        )

    return _write(subscription, now, change)


# Payment outcomes. Billing Build B2 calls these; in B1 only tests do. Each is judged by the
# outcome time it is given, not by when it is recorded.

def _paid_key(reference):
    if not isinstance(reference, str) or not 0 < len(reference) <= REFERENCE_MAX_LENGTH:
        raise ValueError("A payment needs our own reference, at most 64 characters.")
    return f"paid:{reference}"


def _not_due():
    return _conflict("not_due", "No payment is due for this subscription yet.")


def _paid_detail(cycle, reference, detail=None):
    return {"cycle": cycle.sequence, "reference": reference, **(detail or {})}


def first_payment_succeeded(
    subscription, *, paid_at, reference, early=False, detail=None, bought=None
):
    """`early` allows a payment before the trial ends: cycle 1 still starts at the trial end,
    so the trial days are kept."""
    def change(subscription, key):
        _require(subscription, "first_payment_succeeded")
        if paid_at < subscription.trial_end_at and not early:
            raise _not_due()
        cycle = _open_first_cycle(
            subscription, Settlement.PAID, paid_at=paid_at, reference=reference, bought=bought
        )
        return _apply(
            subscription, "first_payment_succeeded", State.ACTIVE, key,
            min(paid_at, subscription.trial_end_at), Actor.PAYMENT,
            _paid_detail(cycle, reference, detail),
        )

    return _write(subscription, paid_at, change, key=_paid_key(reference))


def first_payment_failed(subscription, *, at):
    def change(subscription, key):
        _require(subscription, "first_payment_failed")
        if at < subscription.trial_end_at:
            raise _not_due()
        cycle = _open_first_cycle(subscription, Settlement.PENDING)
        subscription.attention_started_at = subscription.trial_end_at
        return _apply(
            subscription, "first_payment_failed", State.PAYMENT_REQUIRES_ATTENTION, key,
            subscription.trial_end_at, Actor.PAYMENT, {"cycle": cycle.sequence},
        )

    return _write(subscription, at, change, key=f"failed:{subscription.pk}:1")


def renewed(subscription, *, paid_at, reference, early=False, detail=None, bought=None):
    """`early` allows a payment before the due date: the next cycle still starts at it."""
    def change(subscription, key):
        _require(subscription, "renewed")
        due_at = _latest_cycle(subscription).end_at
        if paid_at < due_at and not early:
            raise _not_due()
        cycle = _open_cycle(
            subscription, due_at, Settlement.PAID, paid_at=paid_at, reference=reference,
            bought=bought,
        )
        return _apply(
            subscription, "renewed", State.ACTIVE, key, min(paid_at, due_at), Actor.PAYMENT,
            _paid_detail(cycle, reference, detail),
        )

    return _write(subscription, paid_at, change, key=_paid_key(reference))


def renewal_failed(subscription, *, at):
    def key(subscription):
        """The failure of the cycle that is due: the pending one on a replay, else the next."""
        pending = subscription.cycles.filter(settlement=Settlement.PENDING).first()
        due = pending.sequence if pending else _latest_cycle(subscription).sequence + 1
        return f"failed:{subscription.pk}:{due}"

    def change(subscription, key):
        _require(subscription, "renewal_failed")
        due_at = _latest_cycle(subscription).end_at
        if at < due_at:
            raise _not_due()
        cycle = _open_cycle(subscription, due_at, Settlement.PENDING)
        subscription.attention_started_at = due_at
        return _apply(
            subscription, "renewal_failed", State.PAYMENT_REQUIRES_ATTENTION, key, due_at,
            Actor.PAYMENT, {"cycle": cycle.sequence},
        )

    return _write(subscription, at, change, key=key)


def recovered(subscription, *, paid_at, reference, detail=None, bought=None):
    """A payment during the 7 days of attention. At or after them the site is suspended."""
    def change(subscription, key):
        _require(subscription, "recovered")
        cycle = subscription.cycles.get(settlement=Settlement.PENDING)
        cycle.settlement, cycle.paid_at, cycle.payment_reference = Settlement.PAID, paid_at, reference
        paid_for = bought or plan.version_of(cycle.plan_code, cycle.plan_version)
        # The pending cycle holds the terms that were due. What was paid for replaces them.
        cycle.end_at = clock.cycle_end(cycle.start_at, subscription.anchor_day, paid_for.months)
        for name, value in plan.snapshot(paid_for).items():
            setattr(cycle, name, value)
        cycle.save()
        usage.release_held(cycle, paid=True)
        subscription.attention_started_at = None
        return _apply(
            subscription, "recovered", State.ACTIVE, key, paid_at, Actor.PAYMENT,
            _paid_detail(cycle, reference, detail),
        )

    return _write(subscription, paid_at, change, key=_paid_key(reference))


def reactivated(subscription, *, paid_at, reference, detail=None, bought=None):
    """A payment on a suspended or ended subscription: a new paid cycle from the payment time.

    Refused at or after the deletion date, which is the last date to pay.
    """
    def change(subscription, key):
        _require(subscription, "reactivated")
        if subscription.state == State.CANCELLED and paid_at < subscription.service_ended_at:
            raise _conflict(
                "invalid_transition", "The paid period of this subscription is still running."
            )
        if subscription.deletion_due_at is not None and paid_at >= subscription.deletion_due_at:
            raise _conflict("past_deletion_date", "This site can no longer be paid for.")
        _lapse_pending(subscription)
        _clear_service_end(subscription)
        subscription.attention_started_at = None
        subscription.anchor_day = clock.anchor_day(paid_at)
        cycle = _open_cycle(
            subscription, paid_at, Settlement.PAID, paid_at=paid_at, reference=reference,
            bought=bought,
        )
        return _apply(
            subscription, "reactivated", State.ACTIVE, key, paid_at, Actor.PAYMENT,
            _paid_detail(cycle, reference, detail),
        )

    return _write(subscription, paid_at, change, key=_paid_key(reference))


# Bank transfer with proof (Billing Build B2a). A proof is never a payment: only an operator's
# confirmation settles a cycle, and its time is the outcome time.

REJECT_NOTE_MAX_LENGTH = 300


def _require_transfers():
    if not plan.collection_live():
        raise ApiError("not_found", "Not found.", status=404)


def _require_operator(operator):
    if operator is None:
        raise ValueError("This action needs the operator who takes it.")


def _invalid(fields):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.",
        fields={name: [message] for name, message in fields.items()},
    )


def _transfer_event(payment, code, actor, detail=None):
    return BankTransferEvent.objects.create(
        payment=payment, code=code, actor=actor, idempotency_key=f"bt_{code}:{payment.pk}",
        detail=detail or {},
    )


def _cancel_scheduled():
    return _conflict(
        "cancel_scheduled",
        "This subscription is cancelled and its paid period is still running. "
        "Withdraw the cancellation first.",
    )


def _paid_ahead():
    return _conflict("paid_ahead", "The next period of this subscription is already paid.")


def _refuse_unpayable(subscription, now):
    """The reasons a subscription cannot be paid for at `now`, after `catch_up`."""
    if subscription.state == State.CANCELLED and now < subscription.service_ended_at:
        raise _cancel_scheduled()
    if subscription.deletion_due_at is not None and now >= subscription.deletion_due_at:
        raise _conflict("past_deletion_date", "This site can no longer be paid for.")
    latest = _latest_cycle(subscription)
    if latest.start_at > now:
        raise _paid_ahead()
    if _too_early(subscription, latest, now):
        raise _conflict(
            "too_early",
            "This term can be paid for again from "
            f"{_payable_from(latest).astimezone(clock.MANILA).date().isoformat()}.",
        )


def _too_early(subscription, latest, now):
    """A running multi-month term cannot be paid for again until its last 30 days."""
    return (
        subscription.state == State.ACTIVE
        and latest.term_months > 1
        and latest.end_at - now > plan.RENEWAL_WINDOW
    )


def _payable_from(latest):
    return latest.end_at - plan.RENEWAL_WINDOW


def _usage_only_due(subscription, now):
    """What a payment of overage alone would pay, or a refusal. The overage of a closed account
    (a cancelled one, in or after its paid term) and of a running multi-month term, whose next
    base payment is not yet open, is collected this way, with no base fee and no new cycle."""
    if subscription.deletion_due_at is not None and now >= subscription.deletion_due_at:
        raise _conflict("past_deletion_date", "This site can no longer be paid for.")
    latest = _latest_cycle(subscription)
    allowed = subscription.state == State.CANCELLED or _too_early(subscription, latest, now)
    owed = usage.due(subscription, now) if allowed else usage.Due()
    if not owed.centavos:
        raise _invalid({"plan": "This plan is not offered."})
    return owed


SENDER_TEXT_FIELDS = (
    ("sender_bank", 80, "The sender's bank is needed.", "The sender's bank is too long."),
    ("sender_first_name", 80, "The sender's first name is needed.",
     "The sender's first name is too long."),
    ("sender_last_name", 80, "The sender's last name is needed.",
     "The sender's last name is too long."),
)
ACCOUNT_NUMBER_RE = re.compile(r"^[0-9 -]{6,34}$")
AMOUNT_RE = re.compile(r"^(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?$")
MAX_CENTAVOS = 2_147_483_647  # the column's limit


def _centavos_of(text):
    """Pesos typed as text ("4,999.00") as integer centavos, or None when it is not a positive
    amount with at most two decimals. No floats."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not AMOUNT_RE.match(text):
        return None
    pesos, _, decimals = text.replace(",", "").partition(".")
    centavos = int(pesos) * 100 + int((decimals + "00")[:2])
    return centavos if 0 < centavos <= MAX_CENTAVOS else None


def submit_transfer(
    subscription, account, *, plan_code, content, transferred_at=None, transferred_on=None,
    transfer_reference="", sender_bank=None, sender_first_name=None, sender_last_name=None,
    sender_account_number=None, amount_sent=None, renewal_consent=None, consent_version=None,
    now=None,
):
    """The owner sends a proof of a bank transfer. Returns (payment, created).

    A plan that needs consent (the Launch Promo) is accepted only with `renewal_consent` true
    and the `consent_version` the offer showed; the consent is stored with the payment. A plan
    that does not need it ignores both.

    The sender's details are all required: `transferred_at` (an aware datetime), the bank, the
    sender's names, the account number, the bank's reference and `amount_sent` (pesos as text).
    `transferred_on` is optional; when sent it must be the Manila date of `transferred_at`,
    which is what is stored. An amount that differs from the amount due is accepted and stored:
    staff see the difference.

    Nothing about the subscription changes. A proof sent while another is under review
    replaces it; the same file for the same plan again returns the payment already there.
    """
    _require_transfers()
    now = now or timezone.now()
    fields = {}
    content = bytes(content or b"")
    content_type = proofs.sniff(content)
    if not content:
        fields["proof"] = "A proof of the transfer is needed."
    elif len(content) > proofs.MAX_BYTES:
        fields["proof"] = "The file is too large."
    elif content_type is None:
        fields["proof"] = "The file must be a JPEG, a PNG or a PDF."
    only_usage = plan_code == plan.USAGE_ONLY and plan.usage_enabled()
    chosen = None
    if not only_usage:
        chosen = plan.PLANS.get(plan_code) if isinstance(plan_code, str) else None
        if chosen is None:
            fields["plan"] = "This plan is not offered."
    if chosen is not None and chosen.needs_consent:
        if renewal_consent is not True and renewal_consent != "true":
            fields["renewal_consent"] = "The terms after the first term must be accepted."
        if consent_version != plan.CONSENT_VERSION:
            fields["consent_version"] = "The terms shown are out of date. Reload and read them again."
    sent_at = None
    if not isinstance(transferred_at, datetime) or transferred_at.tzinfo is None:
        fields["transferred_at"] = "The date and time of the transfer are needed."
    elif transferred_at > now:
        fields["transferred_at"] = "The time of the transfer cannot be in the future."
    else:
        sent_at = transferred_at.astimezone(clock.MANILA)
    if transferred_on is not None:
        if not isinstance(transferred_on, date) or isinstance(transferred_on, datetime):
            fields["transferred_on"] = "The date of the transfer is not valid."
        elif transferred_on > now.astimezone(clock.MANILA).date():
            fields["transferred_on"] = "The date of the transfer cannot be in the future."
        elif sent_at is not None and transferred_on != sent_at.date():
            fields["transferred_on"] = "The date does not match the time of the transfer."
    if not isinstance(transfer_reference, str) or not transfer_reference.strip():
        fields["transfer_reference"] = "The reference number is needed."
    else:
        transfer_reference = transfer_reference.strip()
        if len(transfer_reference) > REFERENCE_MAX_LENGTH:
            fields["transfer_reference"] = "The reference number is too long."
    typed = {"sender_bank": sender_bank, "sender_first_name": sender_first_name,
             "sender_last_name": sender_last_name}
    for name, limit, needed, too_long in SENDER_TEXT_FIELDS:
        value = typed[name]
        value = value.strip() if isinstance(value, str) else ""
        if not value:
            fields[name] = needed
        elif len(value) > limit:
            fields[name] = too_long
        typed[name] = value
    number = sender_account_number.strip() if isinstance(sender_account_number, str) else ""
    if not number:
        fields["sender_account_number"] = "The sender's account number is needed."
    elif not ACCOUNT_NUMBER_RE.match(number):
        fields["sender_account_number"] = (
            "The account number is 6 to 34 digits, with spaces or dashes only."
        )
    amount_sent_centavos = _centavos_of(amount_sent)
    if amount_sent_centavos is None:
        fields["amount_sent"] = "Enter the amount sent in pesos, with at most two decimals."
    if fields:
        raise _invalid(fields)
    sha256 = hashlib.sha256(content).hexdigest()

    def change(subscription):
        if only_usage:
            owed = _usage_only_due(subscription, now)
            code, version, months = plan.USAGE_ONLY, None, None
            amount = owed.centavos
        else:
            _refuse_unpayable(subscription, now)
            if chosen not in plan.offers(subscription, now):
                raise _invalid({"plan": "This plan is not offered."})
            owed = usage.due(subscription, now)  # overage of ended periods rides on the term
            code, version, months = chosen.code, chosen.version, chosen.months
            amount = chosen.price_centavos + owed.centavos
        waiting = (
            subscription.transfers.select_for_update()
            .filter(status=TransferStatus.SUBMITTED)
            .first()
        )
        if waiting is not None:
            if (
                waiting.proof_sha256 == sha256 and waiting.plan_code == code
                and waiting.amount_centavos == amount
            ):
                return waiting, False
            waiting.status, waiting.decided_at = TransferStatus.REPLACED, now
            waiting.save()
            _transfer_event(waiting, "replaced", BankTransferEvent.Actor.OWNER)
        # The file is stored before its row, inside the transaction: a row never points at a
        # missing file. If the row then fails, the file is removed again.
        try:
            key = proofs.save(content, content_type)
        except StoreError:
            raise _proof_store_unavailable() from None
        saved.append(key)
        try:
            payment = BankTransferPayment.objects.create(
                subscription=subscription,
                plan_code=code,
                plan_version=version,
                term_months=months,
                amount_centavos=amount,
                usage_centavos=owed.centavos,
                usage_line_max_id=owed.max_id,
                currency=plan.CURRENCY,
                transferred_on=sent_at.date(),
                transferred_at=sent_at,
                transfer_reference=transfer_reference,
                sender_bank=typed["sender_bank"],
                sender_first_name=typed["sender_first_name"],
                sender_last_name=typed["sender_last_name"],
                sender_account_number=number,
                amount_sent_centavos=amount_sent_centavos,
                proof_key=key,
                proof_content_type=content_type,
                proof_size=len(content),
                proof_sha256=sha256,
                submitted_by=account,
                submitted_at=now,
            )
            if chosen is not None and chosen.needs_consent:
                _record_consent(subscription, payment, chosen, account, now)
            _transfer_event(payment, "submitted", BankTransferEvent.Actor.OWNER)
        except BaseException:
            proofs.discard(key)
            raise
        return payment, True

    saved = []
    try:
        return _write(subscription, now, change)
    except BaseException:
        # The transaction rolled back after `change` returned (for example the commit
        # failed), so a file saved inside it has no row. Discard never raises. A rollback
        # decided by a caller's own outer transaction is not seen here; the only caller is
        # the owner route, which has none.
        for key in saved:
            proofs.discard(key)
        raise


def _proof_store_unavailable():
    return ApiError(
        "proof_store_unavailable", "The proof could not be stored. Try again in a moment.",
        status=503,
    )


def _read_proof(key):
    """The stored bytes of a proof. A store that fails answers 503; a key the store does not
    accept answers 404."""
    try:
        return proofs.read(key)
    except StoreError:
        raise _proof_store_unavailable() from None
    except ValueError:
        raise ApiError("not_found", "Not found.", status=404) from None


def _term_start(subscription, state):
    """Where the next term would start and its anchor day, or (None, None) when it starts at
    the confirmation (an ended site). `payment_options` shows the same dates."""
    latest = _latest_cycle(subscription)
    if state == State.TRIAL_ACTIVE:
        return subscription.trial_end_at, clock.anchor_day(subscription.trial_end_at)
    if state == State.ACTIVE:
        return latest.end_at, subscription.anchor_day
    if state == State.PAYMENT_REQUIRES_ATTENTION:
        return latest.start_at, subscription.anchor_day
    return None, None


def _consent_shown(offered, state, subscription):
    """What the buyer must accept before `offered` is bought, or None."""
    if not offered.needs_consent:
        return None
    start_at, anchor = _term_start(subscription, state)
    first_due = clock.cycle_end(start_at, anchor, offered.months) if start_at else None
    return {
        "required": True,
        "version": plan.CONSENT_VERSION,
        "then": _then(offered, first_due),
        "first_due_at": first_due,
    }


def _record_consent(subscription, payment, bought, account, now):
    """What the buyer was told about the price after the term, kept with the proof."""
    follows = plan.PLANS[bought.renews_to]
    shown = _consent_shown(bought, subscription.state, subscription)
    RenewalConsent.objects.create(
        subscription=subscription,
        payment=payment,
        plan_code=bought.code,
        plan_version=bought.version,
        then_plan_code=follows.code,
        then_price_centavos=follows.price_centavos,
        shown_first_due_at=shown["first_due_at"],
        starts_at_confirmation=shown["first_due_at"] is None,
        text_version=plan.CONSENT_VERSION,
        accepted_by=account,
        accepted_at=now,
    )


def _settle_transfer(subscription, payment, at):
    """Settle exactly one cycle with a confirmed payment, by the state at `at`. Returns the
    transition. The caller holds the subscription's row lock and `catch_up` has run."""
    bought = plan.version_of(payment.plan_code, payment.plan_version or 1)
    if bought.code == plan.LAUNCH_PROMO.code and not plan.never_paid(subscription):
        # The promo is bought once. A proof sent while it was still offered is not settled
        # after another payment was; the operator rejects it.
        raise _conflict("plan_not_offered", "This plan can no longer be bought for this site.")
    paid = {
        "paid_at": at,
        "reference": payment.reference,
        "detail": {
            "plan": payment.plan_code, "version": bought.version,
            "amount_centavos": payment.amount_centavos,
        },
        "bought": bought,
    }
    state = subscription.state
    if state == State.CANCELLED and at < subscription.service_ended_at:
        raise _cancel_scheduled()
    if state == State.TRIAL_ACTIVE:
        return first_payment_succeeded(subscription, early=True, **paid)
    if state == State.ACTIVE:
        if _latest_cycle(subscription).start_at > at:
            raise _paid_ahead()
        return renewed(subscription, early=True, **paid)
    if state == State.PAYMENT_REQUIRES_ATTENTION:
        return recovered(subscription, **paid)
    return reactivated(subscription, **paid)


def confirm_transfer(payment, operator, *, amount_received_centavos, now=None):
    """The operator confirms that the money arrived. This, and nothing else, settles a cycle.

    The amount the operator saw must equal the amount due. A payment already confirmed is
    returned as it is; the confirmation, the cycle, the transition, the event and the audit
    entry are written together or not at all.
    """
    _require_operator(operator)
    _require_transfers()
    now = now or timezone.now()

    def change(subscription):
        row = BankTransferPayment.objects.select_for_update().get(pk=payment.pk)
        if row.status == TransferStatus.CONFIRMED:
            return row
        if row.status != TransferStatus.SUBMITTED:
            raise _conflict("not_under_review", "This payment is no longer under review.")
        if (
            isinstance(amount_received_centavos, bool)
            or amount_received_centavos != row.amount_centavos
        ):
            raise _conflict(
                "amount_mismatch", "The amount received is not the amount due for this payment."
            )
        if row.usage_centavos and usage.frozen_total(row) != row.usage_centavos:
            raise _conflict(
                "usage_changed", "The overage in this payment has changed. Reject it and ask "
                "for a new proof."
            )
        cycle = None
        if row.plan_code != plan.USAGE_ONLY:
            transition = _settle_transfer(subscription, row, now)
            cycle = BillingCycle.objects.get(
                subscription=subscription, sequence=transition.detail["cycle"]
            )
        row.status = TransferStatus.CONFIRMED
        row.decided_at, row.decided_by = now, operator
        row.amount_received_centavos = amount_received_centavos
        row.cycle = cycle
        row.save()
        settled_lines = usage.settle(row, now) if row.usage_centavos else 0
        sequence = cycle.sequence if cycle else None
        event = {"cycle": sequence}
        if row.usage_centavos:
            event["usage_lines"] = settled_lines
        _transfer_event(row, "confirmed", BankTransferEvent.Actor.OPERATOR, event)
        # The register's pending invoice item, in its own savepoint: an error there never
        # fails the confirmation, and the safety net opens the item later. Off, this does nothing.
        from apps.invoices import services as invoice_services  # noqa: PLC0415

        invoice_services.open_item_safely(row, now=now)
        audit.record(
            operator, "billing_payment_confirmed", target_type="site",
            target_id=subscription.site.public_id,
            detail={
                "payment": row.public_id, "plan": row.plan_code,
                "amount_centavos": row.amount_centavos, "cycle": sequence,
                **({"usage_centavos": row.usage_centavos} if row.usage_centavos else {}),
            },
        )
        return row

    return _write(Subscription(pk=payment.subscription_id), now, change)


def reject_transfer(payment, operator, *, reason, note="", now=None):
    """The operator rejects a proof. Nothing about the subscription changes, so the lifecycle
    goes on as if no proof had been sent, and the owner may send another."""
    _require_operator(operator)
    _require_transfers()
    now = now or timezone.now()
    fields = {}
    if reason not in BankTransferPayment.RejectReason.values:
        fields["reason"] = "Choose one of the listed reasons."
    note = note.strip() if isinstance(note, str) else None
    if note is None or len(note) > REJECT_NOTE_MAX_LENGTH:
        fields["note"] = "The note is too long."
    elif reason == BankTransferPayment.RejectReason.OTHER and not note:
        fields["note"] = "A note is needed for this reason."
    if fields:
        raise _invalid(fields)
    with transaction.atomic():
        # The same lock a confirmation and a replacing proof take, and no `catch_up`.
        subscription = Subscription.objects.select_for_update().get(pk=payment.subscription_id)
        row = BankTransferPayment.objects.select_for_update().get(pk=payment.pk)
        if row.status == TransferStatus.REJECTED:
            return row
        if row.status != TransferStatus.SUBMITTED:
            raise _conflict("not_under_review", "This payment is no longer under review.")
        row.status = TransferStatus.REJECTED
        row.decided_at, row.decided_by = now, operator
        row.reject_reason, row.reject_note = reason, note
        row.save()
        _transfer_event(row, "rejected", BankTransferEvent.Actor.OPERATOR, {"reason": reason})
        audit.record(
            operator, "billing_payment_rejected", target_type="site",
            target_id=subscription.site.public_id,
            detail={"payment": row.public_id, "reason": reason},
        )
    return row


def _latest_paid_cycle(subscription):
    return subscription.cycles.filter(settlement=Settlement.PAID).order_by("-sequence").first()


def renewal_terms(subscription):
    """What is due after the latest paid cycle, and when. Writes nothing.

    The plan that follows is what the cycle's own plan renews to, at the version sold now:
    a term is protected from a price change, its renewal is not. The amount of a cycle that
    has been paid is never read from here. None when nothing has been paid yet.
    """
    cycle = _latest_paid_cycle(subscription)
    if cycle is None:
        return None
    following = plan.following(cycle)
    return {
        "plan": following.code,
        "version": following.version,
        "name": following.name,
        "amount_centavos": following.price_centavos,
        "currency": plan.CURRENCY,
        "months": following.months,
        "due_at": cycle.end_at,
        "anchor_day": subscription.anchor_day,
    }


def term_running(subscription, at):
    """Whether a paid cycle covers `at`. B2 asks this before it lets a payment method go."""
    return subscription.cycles.filter(
        settlement=Settlement.PAID, start_at__lte=at, end_at__gt=at
    ).exists()


def _then(offered, end_at):
    """What is due after the term `offered` buys. Monthly's own renewal is not described here."""
    if offered is plan.MONTHLY:
        return None
    # After the promo's one term the price is the Monthly one; Annual renews as Annual.
    follows = plan.PLANS[offered.renews_to]
    return {
        "plan": follows.code,
        "amount_centavos": follows.price_centavos,
        "interval": "year" if follows.months == 12 else "month",
        "first_due_at": end_at,
    }


def payment_options(subscription, at=None):
    """What the Billing page needs to show how to pay, at `at`. Writes nothing.

    As `overview`, it reports the state in force at `at` even when the sweep has not yet
    written it.
    """
    _require_transfers()
    now = at or timezone.now()
    due = _due(subscription, now)
    state = DUE_TO_STATE[due[0]] if due else subscription.state
    service_end, deletion_due = subscription.service_ended_at, subscription.deletion_due_at
    if due:
        service_end = due[1]
        deletion_due = _dates_after(subscription, service_end)[1]
    latest = _latest_cycle(subscription)
    reason = None
    if subscription.billing_exempt:
        reason = "billing_exempt"
    elif state == State.CANCELLED and now < service_end:
        reason = "cancel_scheduled"
    elif deletion_due is not None and now >= deletion_due:
        reason = "past_deletion_date"
    elif latest.start_at > now:
        reason = "paid_ahead"
    elif _too_early(subscription, latest, now) and state == State.ACTIVE:
        reason = "too_early"
    offers = []
    owed = usage.due(subscription, now)
    alone = (
        owed.centavos > 0
        and not subscription.billing_exempt
        and (deletion_due is None or now < deletion_due)
        and (state == State.CANCELLED or (state == State.ACTIVE and reason == "too_early"))
    )
    if reason is None:
        start_at, anchor = None, None  # an ended site: the period starts at the confirmation
        if state == State.TRIAL_ACTIVE:
            start_at = subscription.trial_end_at
            anchor = clock.anchor_day(start_at)
        elif state == State.ACTIVE:
            start_at, anchor = latest.end_at, subscription.anchor_day
        elif state == State.PAYMENT_REQUIRES_ATTENTION:
            start_at, anchor = latest.start_at, subscription.anchor_day
        for offered in plan.offers(subscription, now):
            end_at = clock.cycle_end(start_at, anchor, offered.months) if start_at else None
            offers.append({
                "plan": offered.code,
                "name": offered.name,
                "amount_centavos": offered.price_centavos + owed.centavos,
                "usage_centavos": owed.centavos,
                "currency": plan.CURRENCY,
                "months": offered.months,
                "period_start_at": start_at,
                "period_end_at": end_at,
                "starts_at_confirmation": start_at is None,
                "version": offered.version,
                "term_months": offered.months,
                "then": _then(offered, end_at),
                "consent": _consent_shown(offered, state, subscription),
            })
    transfers = subscription.transfers.select_related("cycle")
    return {
        "payable": reason is None,
        "not_payable_reason": reason,
        "payable_from": _payable_from(latest) if reason == "too_early" else None,
        "offers": offers,
        "usage": {"centavos": owed.centavos, "lines": owed.lines, "payable_alone": alone},
        "transfer_accounts": plan.transfer_accounts(),
        "under_review": transfers.filter(status=TransferStatus.SUBMITTED).first(),
        "latest": transfers.filter(
            status__in=(TransferStatus.CONFIRMED, TransferStatus.REJECTED)
        ).order_by("-pk").first(),
        "proof_rules": {"max_bytes": proofs.MAX_BYTES, "content_types": list(proofs.CONTENT_TYPES)},
    }


def transfers_of(subscription):
    """The subscription's payments, for its owner. Nothing is read while collection is off."""
    _require_transfers()
    return subscription.transfers.select_related("cycle")


def transfer_of(subscription, payment_id):
    """One payment of this subscription by its public id. Any other id is not found."""
    payment = None
    if is_public_id(payment_id, "btp"):
        payment = transfers_of(subscription).filter(public_id=payment_id).first()
    if payment is None:
        raise ApiError("not_found", "Not found.", status=404)
    return payment


def proof_for_owner(subscription, payment_id):
    """The proof file as (content, content type), for the owner of the subscription it was
    sent for. The payment is found first, so no file is read for another id."""
    payment = transfer_of(subscription, payment_id)
    return _read_proof(payment.proof_key), payment.proof_content_type


def proof_for_operator(payment, operator):
    """The proof file as (content, content type), for the operator. Each read is audited."""
    _require_operator(operator)
    _require_transfers()
    content = _read_proof(payment.proof_key)
    audit.record(
        operator, "billing_proof_viewed", target_type="site",
        target_id=payment.subscription.site.public_id, detail={"payment": payment.public_id},
    )
    return content, payment.proof_content_type


def review_queue(status):
    """The payments in one status, for the Finance page. Nothing is read while collection
    is off."""
    _require_transfers()
    return BankTransferPayment.objects.filter(status=status).select_related(
        "subscription__site", "cycle", "decided_by"
    )


def transfer_for_operator(payment_id):
    """One payment by its public id, for the Finance page. Any other id is not found."""
    _require_transfers()
    payment = None
    if is_public_id(payment_id, "btp"):
        payment = BankTransferPayment.objects.select_related(
            "subscription__site__account", "cycle", "decided_by"
        ).filter(public_id=payment_id).first()
    if payment is None:
        raise ApiError("not_found", "Not found.", status=404)
    return payment


# Joins with the other apps

def backfill(now=None, *, apply=False):
    """Give every site made before billing its subscription. Returns the counts.

    Without `apply` it only counts. A removed site gets none. Each site is written in its own
    transaction, exactly as site creation writes it, and a site that already has a
    subscription is skipped, so a second run writes nothing.
    """
    if not settings.BILLING_ENABLED:
        return {}
    now = now or timezone.now()
    counts = Counter()
    missing = Site.objects.filter(deleted_at__isnull=True, subscription__isnull=True)
    for site in missing.select_related("account").order_by("pk"):
        trial_running = now < clock.trial_end(site.account.created_at)
        if apply:
            with transaction.atomic():
                # The lock every site write and a disposal of this account take.
                Onboarding.objects.select_for_update().filter(account=site.account_id).first()
                still_missing = Site.objects.filter(
                    pk=site.pk, deleted_at__isnull=True, subscription__isnull=True
                ).exists()
                if not still_missing:
                    continue
                start_subscription(site, site.account, actor=Actor.BACKFILL, now=now)
        counts["sites_without_subscription"] += 1
        counts["trial_still_running" if trial_running else "trial_already_ended"] += 1
    return dict(counts)


def set_exempt(site, *, operator, reason):
    """Mark the site's subscription as never billed (an approved test site). Set once.

    Returns False when it was already exempt, and then writes nothing. Nothing clears it.
    """
    reason = (reason or "").strip()
    if operator is None or not reason:
        raise ValueError("An exemption needs an operator and a reason.")
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().filter(site=site).first()
        if subscription is None:
            raise ValueError("This site has no subscription.")
        if subscription.billing_exempt:
            return False
        subscription.billing_exempt = True
        subscription.save(update_fields=["billing_exempt", "updated_at"])
        audit.record(
            operator, "billing_exempt_set", target_type="site", target_id=site.public_id, reason=reason,
            detail={"state": subscription.state},
        )
    return True


def prepare_disposal(account, now, actor=Actor.OPERATOR, dry_run=False):
    """Cancel the account's subscriptions before its disposal, and say whether it may go on.

    Call it inside the disposal's transaction. A subscription on trial, needing payment
    attention or suspended is cancelled and service ends at once (a suspended one keeps its
    dates). One inside a paid term is cancelled to the end of that term, and the disposal is
    refused for now with `paid_term_running` and that date. An ended or exempt subscription is
    left as it is. One with a proof of payment under review is left as it is too, and the
    disposal is refused with `payment_under_review` until an operator has decided it. A dry
    run writes nothing and reports what a real run would do.
    """
    if not settings.BILLING_ENABLED:
        return DisposalStep()
    rows = Subscription.objects.filter(site__account=account).order_by("pk")
    if not dry_run:
        # Lock order everywhere: Account, then Onboarding, then Subscription.
        Onboarding.objects.select_for_update().filter(account=account).first()
        rows = rows.select_for_update(of=("self",))
    cancelled, reasons = [], []
    for subscription in rows.select_related("site"):
        if subscription.billing_exempt:
            continue
        site_id = subscription.site.public_id
        if plan.collection_live() and subscription.transfers.filter(
            status=TransferStatus.SUBMITTED
        ).exists():
            reasons.append({"reason": PAYMENT_UNDER_REVIEW, "site": site_id})
            continue
        if dry_run:
            due = _due(subscription, now)
            state = DUE_TO_STATE[due[0]] if due else subscription.state
        else:
            catch_up(subscription, now)
            state = subscription.state
        if state == State.CANCELLED_NO_PAYMENT_METHOD:
            continue
        if state == State.CANCELLED:
            if now < subscription.service_ended_at:
                reasons.append(_term_running(site_id, subscription.service_ended_at))
            continue
        term_end = _latest_cycle(subscription).end_at if state == State.ACTIVE else None
        if not dry_run:
            cancel(subscription, now=now, actor=actor)
        cancelled.append(site_id)
        if term_end is not None and now < term_end:
            reasons.append(_term_running(site_id, term_end))
    return DisposalStep(tuple(cancelled), tuple(reasons))


def export_transfers_of(account):
    """The account's bank transfer proofs as plain values, sender details included, for the
    account export. Never where the file is kept, its hash or who decided. Reads only."""
    if not settings.BILLING_ENABLED:
        return {}

    def iso(moment):
        return moment.isoformat() if moment else None

    rows = (
        BankTransferPayment.objects.filter(subscription__site__account=account)
        .select_related("subscription__site").order_by("pk")
    )
    return {"bank_transfers": [
        {
            "site": row.subscription.site.public_id,
            "id": row.public_id,
            "status": row.status,
            "plan": row.plan_code,
            "amount_centavos": row.amount_centavos,
            "currency": row.currency,
            "transferred_on": row.transferred_on.isoformat(),
            "transferred_at": iso(row.transferred_at),
            "transfer_reference": row.transfer_reference,
            "sender_bank": row.sender_bank,
            "sender_first_name": row.sender_first_name,
            "sender_last_name": row.sender_last_name,
            "sender_account_number": row.sender_account_number,
            "amount_sent_centavos": row.amount_sent_centavos,
            "submitted_at": iso(row.submitted_at),
            "decided_at": iso(row.decided_at),
            "reject_reason": row.reject_reason,
        }
        for row in rows
    ]}


def _term_running(site_id, until):
    return {"reason": PAID_TERM_RUNNING, "site": site_id, "until": until}


# The sweep

def _due_ids(now):
    """Subscriptions whose stored rows are behind what `position` reports at `now`."""
    cutoff = now - plan.GRACE
    trial = Q(state=State.TRIAL_ACTIVE, trial_end_at__lte=now) & (
        ~Q(method_at_trial_end=Method.YES) | Q(trial_end_at__lte=cutoff)
    )
    attention = Q(state=State.PAYMENT_REQUIRES_ATTENTION, attention_started_at__lte=cutoff)
    active = Q(state=State.ACTIVE, latest_end__lte=cutoff)
    due = trial | attention | active
    if plan.collection_live():
        # Ended before NegosyoOnline could be paid: the dates start now (`_clocks_due`).
        due |= Q(
            state__in=ENDED_STATES, service_ended_at__isnull=False, deletion_due_at__isnull=True
        )
    return list(
        Subscription.objects.filter(billing_exempt=False)
        .annotate(latest_end=Max("cycles__end_at"))
        .filter(due)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def sweep(now=None, *, dry_run=False):
    """Catch every due subscription up with the time. Returns a count per transition code.

    Each subscription is written in its own transaction, so one failure does not stop the
    rest, and a subscription another writer holds is left for the next run.
    """
    if not settings.BILLING_ENABLED:
        return {}
    now = now or timezone.now()
    counts = Counter()
    for pk in _due_ids(now):
        try:
            if dry_run:
                subscription = Subscription.objects.get(pk=pk)
                due = _due(subscription, now)
                code = due[0] if due else None
                if code is None and _clocks_due(subscription):
                    code = "clocks_started"
            else:
                with transaction.atomic():
                    subscription = (
                        Subscription.objects.select_for_update(skip_locked=True)
                        .filter(pk=pk)
                        .first()
                    )
                    if subscription is None:
                        continue
                    transition = catch_up(subscription, now)
                    code = transition.code if transition else None
        except Exception:
            logger.exception("Billing sweep failed for subscription %s", pk)
            code = "failed"
        if code:
            counts[code] += 1
    if not dry_run and plan.usage_enabled():
        written_off = usage.write_off_unreturned(now)  # R-4c
        if written_off:
            counts["usage_written_off"] += written_off
    result = dict(sorted(counts.items()))
    logger.info("Billing sweep%s: %s", " (dry run)" if dry_run else "", result or "nothing due")
    return result


def conversion_decision(subscription, at):
    """What a charge at the first renewal of a Launch Promo term would be allowed to do.

    Only a decision: nothing is charged, opened or written here, and a repeated call gives the
    same answer and the same key. At launch the answer is `manual`: the Subscriber sends a
    Monthly proof.
    """
    none = {"action": "none", "reason": None, "terms": None, "key": None}
    if subscription.billing_exempt or subscription.state in (
        State.CANCELLED, State.CANCELLED_NO_PAYMENT_METHOD, State.SUSPENDED,
    ):
        return none
    cycle = _latest_paid_cycle(subscription)
    if cycle is None or cycle.plan_code != plan.LAUNCH_PROMO.code or at < cycle.end_at:
        return none
    terms = renewal_terms(subscription)
    payment = BankTransferPayment.objects.filter(cycle=cycle).first()
    consent = RenewalConsent.objects.filter(payment=payment).first() if payment else None
    if (
        consent is None
        or consent.then_plan_code != terms["plan"]
        or consent.then_price_centavos != terms["amount_centavos"]
    ):
        return {**none, "action": "manual", "reason": "no_valid_consent", "terms": terms}
    if not methods.reusable(subscription.site):
        return {**none, "action": "manual", "reason": "no_reusable_method", "terms": terms}
    next_sequence = _latest_cycle(subscription).sequence + 1
    return {
        "action": "charge", "reason": None, "terms": terms,
        "key": f"renewal:{subscription.pk}:{next_sequence}",
    }


def allowance_period_at(subscription, at):
    """The paid cycle covering `at`, which monthly allowance period of it `at` falls in, and
    that period's bounds; None when no paid cycle covers `at`. Reads, stores nothing."""
    cycle = subscription.cycles.filter(
        settlement=Settlement.PAID, start_at__lte=at, end_at__gt=at
    ).first()
    if cycle is None:
        return None
    bought = plan.version_of(cycle.plan_code, cycle.plan_version)
    periods = clock.allowance_periods(
        cycle.start_at, subscription.anchor_day, cycle.term_months, bought.allowance_periods
    )
    for index, (start, end) in enumerate(periods, start=1):
        if start <= at < end:
            return {"cycle": cycle, "index": index, "start_at": start, "end_at": end}
    return None
