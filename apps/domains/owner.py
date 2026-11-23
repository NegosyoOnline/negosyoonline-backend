"""What the Subscriber's Domain screen does (design R4.7): check a name, buy it, try again, and
read the status. Every function here only writes rows: nothing calls a provider and nothing
enqueues the purchase (a Check may queue a read-only task; the beat sweep is its backstop).

The gate (ACTIVE, a paid cycle, not on hold, not exempt) is read at Check, Buy and Retry. A Buy of
the name of the site's own `succeeded` registration is a re-attach: it spends nothing, sends no
`register`, starts a claim at `registered`, and needs neither DOMAINS_PURCHASE_ENABLED nor the caps.
Any other Buy needs DOMAINS_PURCHASE_ENABLED. One registration per site for life.
"""
import calendar
import logging
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.domains import events, gate, machine, names, purchase
from apps.domains.models import (
    ActorType, ClaimState, Domain, DomainPurchaseLedger, DomainRegistration, RegistrationState,
)
from apps.domains.services import offered_tlds, tld_of

logger = logging.getLogger(__name__)

LABELS = {
    ClaimState.CHECKING: "Checking the name",
    ClaimState.QUOTED: "Available, included in your plan",
    ClaimState.CONFIRMED: "We will buy it shortly",
    ClaimState.AWAITING_GATE: "We will buy it shortly",
    ClaimState.REGISTERING: "Buying your domain",
    ClaimState.REGISTERED: "Setting up your domain",
    ClaimState.ZONE: "Setting up your domain",
    ClaimState.CONNECTING: "Connecting your website",
    ClaimState.ACTIVATING: "Almost done",
    ClaimState.LIVE: "Connected",
    ClaimState.ATTENTION: "Your website is not answering yet",
    ClaimState.FAILED: "Something went wrong",
    ClaimState.CONFLICT: "Waiting for the NegosyoOnline team",
    ClaimState.REMOVAL_PENDING: "Disconnecting",
    ClaimState.ENDED: "",
}


def _error(code, message, status=409, fields=None):
    return ApiError(code, message, status=status, fields=fields)


def _invalid(message):
    return ApiError("invalid_request", "Some values are missing or invalid.", fields={"name": [message]})


def active_claim(site, *, lock=False):
    rows = Domain.objects.filter(site=site).exclude(state=ClaimState.ENDED)
    return (rows.select_for_update() if lock else rows).first()


def _registration(site):
    reg, _ = DomainRegistration.objects.get_or_create(site=site)
    return DomainRegistration.objects.select_for_update().get(pk=reg.pk)


def _require_gate(site, now):
    code = gate.refusal(site, now)
    if code:
        raise _error("not_eligible", gate.message(code))


def _name(raw, claim, *, check_extension, reg=None):
    try:
        name = names.normalise(raw)
    except names.NameRefused as error:
        raise _invalid(str(error)) from None
    reason = names.refusal(name, exclude_claim=claim)
    if reason:
        raise _invalid(reason)
    if check_extension and tld_of(name) not in offered_tlds():
        raise _invalid(purchase.EXTENSION)
    return name


def _own_name_error(reg):
    return _error("domain_exists", f"Your domain is {reg.name}.")


def _enqueue_check(claim_id):
    def run():
        try:
            from apps.domains import tasks  # noqa: PLC0415

            tasks.check_name.delay(claim_id)
        except Exception:  # noqa: BLE001 - the beat sweep picks the claim up
            logger.exception("domains check task not queued claim_id=%s", claim_id)

    transaction.on_commit(run, robust=True)


# --- the screen's actions --------------------------------------------------------------------

def check(account, site, raw, now=None):
    """The owner types a name. Writes the claim (`checking`) and queues a read-only Check."""
    now = now or timezone.now()
    hit("domains-check-site", site.pk, limit=settings.DOMAINS_CHECKS_PER_HOUR_SITE, window=3600)
    hit("domains-check-all", "all", limit=settings.DOMAINS_CHECKS_PER_HOUR_ACCOUNT, window=3600)
    _require_gate(site, now)
    with transaction.atomic():
        reg = _registration(site)
        claim = active_claim(site, lock=True)
        if reg.state in (RegistrationState.SENT, RegistrationState.CONFLICT):
            raise _error("domain_in_progress", "Your domain is being set up.")
        if claim is not None and claim.state not in (ClaimState.CHECKING, ClaimState.QUOTED):
            raise _error("request_in_progress", "You already have a domain request in progress.")
        name = _name(raw, claim, check_extension=reg.state != RegistrationState.SUCCEEDED)
        if reg.state == RegistrationState.SUCCEEDED:
            if reg.name != name:
                raise _own_name_error(reg)
            return _quote_own(site, reg, claim, name, now)
        if reg.name is not None:
            raise _error("request_in_progress", "You already have a domain request in progress.")
        try:
            if claim is None:
                claim = Domain.objects.create(site=site, name=name, state=ClaimState.CHECKING)
            else:
                claim.name, claim.quote_expires_at = name, None
                machine.move(claim, ClaimState.CHECKING, now=now)
        except IntegrityError:
            raise _error("request_in_progress", "You already have a domain request in progress.") from None
        purchase.clear_check(reg)  # a quote of another name never carries over
        reg.save()
        events.log(site, "check_requested", claim=claim, registration=reg, actor_type=ActorType.SUBSCRIBER,
                      actor_id=account.pk, detail={"name": name}, now=now)
        _enqueue_check(claim.pk)
    return status(site, now)


def _quote_own(site, reg, claim, name, now):
    """The site's own registered name: a re-attach starts from a quoted claim, with no provider call."""
    try:
        if claim is None:
            claim = Domain.objects.create(
                site=site, name=name, state=ClaimState.QUOTED, quote_expires_at=now + purchase.QUOTE_VALID
            )
        else:
            claim.name = name
            machine.move(claim, ClaimState.QUOTED, now=now, quote_expires_at=now + purchase.QUOTE_VALID)
    except IntegrityError:
        raise _error("request_in_progress", "You already have a domain request in progress.") from None
    return status(site, now)


def buy(account, site, raw, now=None):
    """The owner presses Buy. Only writes: it never enqueues and never calls a provider."""
    now = now or timezone.now()
    _require_gate(site, now)
    purchase.ledger()
    with transaction.atomic():
        DomainPurchaseLedger.objects.select_for_update().get(pk=1)  # the lock order of every writer: ledger first
        reg = _registration(site)
        claim = active_claim(site, lock=True)
        reattach = reg.state == RegistrationState.SUCCEEDED
        name = _name(raw, claim, check_extension=not reattach)
        if reattach:
            if reg.name != name:
                raise _own_name_error(reg)
            _reattach(account, site, reg, claim, name, now)
            return status(site, now)
        if reg.state in (RegistrationState.SENT, RegistrationState.CONFLICT):
            raise _error("domain_in_progress", "Your domain is being set up.")
        if not settings.DOMAINS_PURCHASE_ENABLED:
            raise _error("purchase_not_open", "Domains are not open for buying yet.")
        if claim is not None and claim.state in (ClaimState.CONFIRMED, ClaimState.AWAITING_GATE):
            if claim.name != name and not purchase.rename_intent(claim, reg, name, actor_id=account.pk, now=now):
                raise _error("request_in_progress", "You already have a domain request in progress.")
            return status(site, now)
        if claim is None or claim.state != ClaimState.QUOTED or claim.name != name:
            raise _error("check_first", "Check the name first.")
        if claim.quote_expires_at is None or now > claim.quote_expires_at:
            raise _error("quote_expired", "The check has expired. Please check the name again.")
        shown = (reg.check_registration_cost_cents, reg.check_renewal_cost_cents)
        reg.name = name
        purchase.clear_check(reg)
        try:
            with transaction.atomic():
                reg.save()
        except IntegrityError:
            raise _error("name_taken", "That name is taken. Please choose another.") from None
        machine.move(claim, ClaimState.CONFIRMED, now=now)
        events.log(
            site, "buy", claim=claim, registration=reg, actor_type=ActorType.SUBSCRIBER, actor_id=account.pk, now=now,
            detail={"name": name, "shown_registration_cents": shown[0], "shown_renewal_cents": shown[1]},
        )
    return status(site, now)


def _reattach(account, site, reg, claim, name, now):
    """A claim at `registered` over the site's own succeeded registration: no `register`, no spend."""
    try:
        if claim is None:
            claim = Domain.objects.create(site=site, name=name, state=ClaimState.REGISTERED)
            machine.record_event(claim, "state", to_state=ClaimState.REGISTERED, detail={"reason": "re-attach"}, now=now)
        elif claim.state in (ClaimState.CHECKING, ClaimState.QUOTED) and claim.name == name:
            machine.move(claim, ClaimState.REGISTERED, reason="re-attach", now=now)
        else:
            raise _error("request_in_progress", "You already have a domain request in progress.")
    except IntegrityError:
        raise _error("request_in_progress", "You already have a domain request in progress.") from None
    events.log(site, "reattach", claim=claim, registration=reg, actor_type=ActorType.SUBSCRIBER,
                  actor_id=account.pk, detail={"name": name}, now=now)


def retry(account, site, now=None):
    """Try again after a setup failure: back to `zone`, never to a purchase step."""
    now = now or timezone.now()
    _require_gate(site, now)
    with transaction.atomic():
        claim = active_claim(site, lock=True)
        if claim is None or claim.state != ClaimState.FAILED or not machine.retry(claim, now=now):
            raise _error("nothing_to_retry", "There is nothing to try again.")
        events.log(site, "retry", claim=claim, actor_type=ActorType.SUBSCRIBER, actor_id=account.pk, now=now)
    return status(site, now)


def add_months(moment, months):
    """`moment` plus whole calendar months, the day clamped to the month's length."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def free_until(reg):
    """The last day of the free year of a registered domain, as a date (UTC), or None. The registrar's
    `expires_at` when recorded, else the registration date plus 12 months. No price, no year-2 amount."""
    if reg is None or reg.state != RegistrationState.SUCCEEDED:
        return None
    moment = reg.expires_at
    if moment is None:
        base = reg.completed_at or reg.register_sent_at
        moment = add_months(base, 12) if base else None
    return moment.astimezone(dt_timezone.utc).date() if moment else None


def status(site, now=None):
    """What `GET .../domain/` answers: never a price, a provider id or a token."""
    now = now or timezone.now()
    reg = DomainRegistration.objects.filter(site=site).first()
    claim = active_claim(site) or Domain.objects.filter(site=site).order_by("-pk").first()
    locked = gate.refusal(site, now)
    data = None
    if claim is not None:
        data = {
            "id": claim.public_id, "name": claim.name, "state": claim.state,
            "label": LABELS.get(claim.state, ""), "reason": claim.reason,
            "quote_expires_at": claim.quote_expires_at.isoformat() if claim.quote_expires_at else None,
            "can_buy": (
                claim.state == ClaimState.QUOTED and bool(claim.quote_expires_at) and now <= claim.quote_expires_at
            ),
            "can_retry": claim.state == ClaimState.FAILED,
        }
    return {
        "eligible": not locked,
        "locked_reason": gate.message(locked) if locked else "",
        "extensions": [f".{tld}" for tld in offered_tlds(now)],
        "registered_name": reg.name if reg and reg.state == RegistrationState.SUCCEEDED else "",
        "free_until": free_until(reg).isoformat() if free_until(reg) else None,
        "domain": data,
    }
