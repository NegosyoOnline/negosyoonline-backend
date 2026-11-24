"""The purchase sequence, in the worker only (design R4.14.3, plan r4.3 item 2).

Nothing here is reached from a web request: the owner's Buy only writes rows (apps.domains.owner),
`providers.registrar.register` refuses inside a request, and the one function that calls it is
`_send` below. The order for one registration:

1. Check (read, no money, no lock), recorded on the row by a conditional write.
2. Step 1, one committed transaction: lock the ledger row, then the registration row; verify every
   condition (state `intent`, nothing sent, Check younger than 120 seconds for this very name, the
   switches, the payment gate, the price ceiling, the caps counted from `register_sent` events);
   then ONE conditional UPDATE (`intent` and unsent to `sent`, `send_count = 1`) and the
   `register_sent` event. Only a rowcount of 1 goes on.
3. Step 2, outside any transaction: the single `register` POST.
4. Step 3, a new transaction: the result. Anything unknown or unsafe goes to `conflict`.

A row that has sent is never sent again: it is only polled (`registration-status`, then a read of
the registration). There is no retry of a sent row, no second send and no manual purchase; the only
way out of `conflict` is `confirmed_registered` after a provider read. Never raises out of `advance`.
"""
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.errors import ApiError
from apps.domains import context, events, gate, machine
from apps.domains.models import (
    ActorType, ClaimState, Domain, DomainEvent, DomainPurchaseLedger, DomainRegistration, DomainTldPrice,
    NameMismatch, RegistrationState,
)
from apps.domains.providers.base import Paused, ProviderError, Refused
from apps.domains.services import tld_of

logger = logging.getLogger("domains")

CHECK_MAX_AGE = timedelta(seconds=120)
QUOTE_VALID = timedelta(minutes=15)
UNKNOWN_LIMIT = timedelta(minutes=30)
CAP_WINDOW = timedelta(hours=24)
PURCHASE_STATES = (
    ClaimState.CHECKING, ClaimState.QUOTED, ClaimState.CONFIRMED, ClaimState.AWAITING_GATE,
    ClaimState.REGISTERING,
)
UNSENT_CLAIM_STATES = (ClaimState.CHECKING, ClaimState.QUOTED, ClaimState.CONFIRMED, ClaimState.AWAITING_GATE)
BAD_STATUSES = ("failed", "action_required", "blocked")
CHECK_FIELDS = (
    "check_name", "check_at", "check_registrable", "check_tier", "check_registration_cost_cents",
    "check_renewal_cost_cents", "currency",
)
NOT_AVAILABLE = "That name is not available. Please choose another."
EXTENSION = "This name is not included. Please choose another."


# --- shared pieces ---------------------------------------------------------------------------

def ledger():
    """The purchase lock row, created by code before any transaction that locks it."""
    DomainPurchaseLedger.objects.get_or_create(pk=1)


def lock_rows(reg_id, claim_id=None):
    """Inside a transaction: the ledger first, then the registration, then the claim."""
    DomainPurchaseLedger.objects.select_for_update().get(pk=1)
    reg = DomainRegistration.objects.select_for_update().get(pk=reg_id)
    claim = Domain.objects.select_for_update().get(pk=claim_id) if claim_id else None
    return reg, claim


def clear_check(reg):
    reg.check_name, reg.check_at, reg.check_registrable, reg.check_tier = "", None, None, ""
    reg.check_registration_cost_cents = reg.check_renewal_cost_cents = None
    reg.currency = ""


def evaluate(row, name):
    """Why a Check answer refuses `name`, or "". The first-year registration price must be in USD and
    at or under the ceiling; the renewal price must be present but is not compared to the ceiling
    (it is billed at its exact price in year 2). A ceiling of 0 (unset) refuses everything."""
    if not row["registrable"]:
        return NOT_AVAILABLE
    ceiling = settings.DOMAINS_MAX_COST_CENTS
    reg_cost, ren_cost = row["registration_cost_cents"], row["renewal_cost_cents"]
    if (
        row["currency"] != "USD" or row["tier"] != "standard" or tld_of(name) not in settings.DOMAINS_ALLOWED_TLDS
        or reg_cost is None or ren_cost is None or ceiling <= 0 or reg_cost > ceiling
    ):
        return EXTENSION
    return ""


def _check_one(providers, name):
    for row in providers.registrar.check([name]):
        if row["name"] == name:
            return row
    return None


def alert(kind, claim=None, **detail):
    machine.alert(kind, claim, **detail)


# --- the worker entry point ------------------------------------------------------------------

def advance(claim_id, *, providers, now=None, only_check=False):
    """One purchase step for one claim. Returns a short word; never raises. `only_check` is the
    owner's Check task: it touches a `checking` claim and nothing else (R5-m8)."""
    now = now or timezone.now()
    if providers is None or getattr(providers, "registrar", None) is None:
        return "off"
    if not cache.add(machine._lock_key(claim_id), 1, 300):
        return "busy"
    try:
        claim = Domain.objects.select_related("site").get(pk=claim_id)
        if claim.state not in PURCHASE_STATES or (only_check and claim.state != ClaimState.CHECKING):
            return "idle"
        try:
            claim.assert_name_bound()
        except NameMismatch:
            alert("name_mismatch", claim)
            return "refused"
        try:
            if claim.state == ClaimState.CHECKING:
                return _owner_check(claim, providers, now)
            if claim.state == ClaimState.QUOTED:
                if claim.quote_expires_at and now > claim.quote_expires_at:
                    machine.move(claim, ClaimState.ENDED, reason="The quote expired. Please check the name again.", now=now)
                    return "expired"
                return "quoted"
            if claim.state == ClaimState.REGISTERING:
                return _poll(claim, providers, now)
            return _purchase(claim, providers, now)
        except Paused:
            return "paused"
        except ProviderError as error:
            if error.retryable:
                return "retry"
            alert("provider_refused", claim, code=error.code)
            return "refused"
    except Exception:  # noqa: BLE001 - one claim never stops the sweep
        logger.exception("domains purchase step failed claim_id=%s", claim_id)
        return "error"
    finally:
        cache.delete(machine._lock_key(claim_id))


# --- the owner's Check (read only) -----------------------------------------------------------

def _owner_check(claim, providers, now):
    if now - claim.state_changed_at > QUOTE_VALID:
        machine.move(claim, ClaimState.ENDED, reason="We could not check that name. Please try again.", now=now)
        return "expired"
    reg = claim.registration()
    if reg is None or reg.state != RegistrationState.INTENT:
        return "idle"
    name = claim.name
    row = _check_one(providers, name)
    if row is None:
        return "retry"
    reason = evaluate(row, name)
    if reason:
        machine.move(claim, ClaimState.ENDED, reason=reason, now=now)
        events.log(claim.site, "check_refused", claim=claim, registration=reg, detail={"name": name}, now=now)
        return "refused"
    with transaction.atomic():
        fresh = Domain.objects.select_for_update().get(pk=claim.pk)
        if fresh.state != ClaimState.CHECKING or fresh.name != name:
            return "stale"  # a newer Check took the claim: this answer writes nothing
        DomainRegistration.objects.filter(
            Q(name__isnull=True) | Q(name=name), pk=reg.pk, state=RegistrationState.INTENT,
            register_sent_at__isnull=True,
        ).update(**_check_values(row, name, now))
        machine.move(fresh, ClaimState.QUOTED, now=now, quote_expires_at=now + QUOTE_VALID)
    return "quoted"


def _check_values(row, name, now):
    return {
        "check_name": name, "check_at": now, "check_registrable": row["registrable"],
        "check_tier": row["tier"], "check_registration_cost_cents": row["registration_cost_cents"],
        "check_renewal_cost_cents": row["renewal_cost_cents"], "currency": row["currency"],
    }


# --- the purchase ----------------------------------------------------------------------------

def _wait(claim, reason, now):
    if claim.state == ClaimState.CONFIRMED:
        machine.move(claim, ClaimState.AWAITING_GATE, reason="We will buy it shortly.", now=now)
    return reason


def _purchase(claim, providers, now):
    reg = claim.registration()
    if reg is None:
        return "idle"
    if reg.state == RegistrationState.SENT:  # sent before: never again, only polled
        machine.move(claim, ClaimState.REGISTERING, now=now)
        return _poll(claim, providers, now)
    if reg.state == RegistrationState.CONFLICT:
        machine.move(claim, ClaimState.CONFLICT, reason="Waiting for the NegosyoOnline team.", now=now)
        return "conflict"
    if reg.state == RegistrationState.SUCCEEDED:
        return "idle"
    if context.in_request():  # never from a web request, not even to consume step 1
        return "in_request"
    if not settings.DOMAINS_PURCHASE_ENABLED:
        return "purchase_off"
    refusal = gate.refusal(claim.site, now)
    if refusal:
        return _wait(claim, f"gate:{refusal}", now)
    name = reg.name
    row = _check_one(providers, name)
    if row is None:
        return "retry"
    reason = evaluate(row, name)
    if reason:
        release(claim, reg, reason, kind="check_refused", now=now)
        return "refused"
    recorded = DomainRegistration.objects.filter(
        pk=reg.pk, name=name, state=RegistrationState.INTENT, register_sent_at__isnull=True
    ).update(**_check_values(row, name, now))
    if not recorded:
        return "stale"
    _wait(claim, "", now)
    ledger()
    try:
        outcome = _step1(reg.pk, claim.pk, now)
    except IntegrityError:  # a second `register_sent` event for this row: the row already sent once
        alert("second_send_refused", claim)
        return "refused"
    if outcome[0] != "sent":
        return _wait(Domain.objects.get(pk=claim.pk), outcome[1], now)
    return _send(claim.pk, reg.pk, outcome[1], providers, now)


def _step1(reg_id, claim_id, now):
    """Purchase step 1: verify and set `sent` in one transaction. ("sent", name) or ("wait", why)."""
    with transaction.atomic():
        reg, claim = lock_rows(reg_id, claim_id)
        site = claim.site
        why = _verify(reg, claim, site, now)
        if why:
            if why.startswith("cap"):
                alert("cap_hit", claim, reason=why)
            return ("wait", why)
        sent = DomainRegistration.objects.filter(
            pk=reg.pk, state=RegistrationState.INTENT, register_sent_at__isnull=True, send_count=0
        ).update(state=RegistrationState.SENT, register_sent_at=now, send_count=1)
        if sent != 1:
            return ("wait", "not_updated")
        reg.refresh_from_db()
        events.log(
            site, "register_sent", claim=claim, registration=reg, now=now,
            detail={
                "name": reg.name, "check_registration_cost_cents": reg.check_registration_cost_cents,
                "check_renewal_cost_cents": reg.check_renewal_cost_cents,
            },
        )
        machine.move(claim, ClaimState.REGISTERING, now=now)
        return ("sent", reg.name)


def _verify(reg, claim, site, now):
    """Every condition of step 1, read from the locked rows. "" when all hold."""
    if reg.state != RegistrationState.INTENT or reg.register_sent_at is not None or reg.send_count != 0:
        return "already_sent"
    if not reg.name or reg.check_name != reg.name:
        return "check_name"
    if reg.check_at is None or now - reg.check_at > CHECK_MAX_AGE:
        return "check_old"
    if not (settings.DOMAINS_ENABLED and settings.DOMAINS_PURCHASE_ENABLED) or settings.DOMAINS_PROVIDER_PAUSED:
        return "switch_off"
    if claim.state not in (ClaimState.CONFIRMED, ClaimState.AWAITING_GATE) or claim.name != reg.name:
        return "claim"
    refusal = gate.refusal(site, now)
    if refusal:
        return f"gate:{refusal}"
    row = {
        "registrable": reg.check_registrable, "tier": reg.check_tier, "currency": reg.currency,
        "registration_cost_cents": reg.check_registration_cost_cents,
        "renewal_cost_cents": reg.check_renewal_cost_cents,
    }
    if evaluate(row, reg.name):
        return "ceiling"
    recent = list(DomainEvent.objects.filter(kind="register_sent", created_at__gte=now - CAP_WINDOW))
    cap = settings.DOMAINS_DAILY_PURCHASE_CAP
    if cap <= 0 or len(recent) >= cap:
        return "cap_count"
    spent = sum(int(e.detail.get("check_registration_cost_cents") or 0) for e in recent)
    spend_cap = settings.DOMAINS_DAILY_SPEND_CAP_CENTS
    if spend_cap <= 0 or spent + reg.check_registration_cost_cents > spend_cap:
        return "cap_spend"
    return ""


def _send(claim_id, reg_id, name, providers, now):
    """Step 2 and 3: the one POST, outside any transaction, then record its result."""
    claim = Domain.objects.select_related("site").get(pk=claim_id)
    try:
        result = providers.registrar.register(name)
    except Refused as error:  # our own guard refused: nothing went out. The row stays sent and is polled.
        alert("register_not_sent", claim, code=error.code)
        return "unsent"
    except ProviderError as error:
        if error.retryable:
            return "unknown"  # timeout, 5xx, 429: the outcome is unknown; the next sweep polls
        to_conflict(claim_id, reg_id, "register_refused", code=error.code, now=now)
        return "conflict"
    if result["state"] == "succeeded":
        finalize(claim_id, reg_id, providers, now)
        return "registered"
    if result["state"] in BAD_STATUSES:
        to_conflict(claim_id, reg_id, "register_" + result["state"], status=result["state"], now=now)
        return "conflict"
    return "registering"


def _poll(claim, providers, now):
    """A sent row: read the status, never send. Unknown after 30 minutes is `conflict`."""
    reg = claim.registration()
    if reg is None or reg.state != RegistrationState.SENT:
        return "idle"
    name = reg.name
    adopt = False
    try:
        status = providers.registrar.status(name)
    except ProviderError as error:
        if error.code != "not_found":
            return _unknown(claim, reg, now)
        status, adopt = {"state": "unknown", "code": ""}, True
    state = status["state"]
    if state == "succeeded":
        finalize(claim.pk, reg.pk, providers, now)
        return "registered"
    if state in BAD_STATUSES:
        to_conflict(claim.pk, reg.pk, "status_" + state, status=state, code=status["code"], now=now)
        return "conflict"
    if adopt or state == "unknown":
        try:  # this row's own sent POST, outcome unknown: the registration may already be ours
            found = providers.registrar.get(name)
        except ProviderError:
            found = None
        if found and found["name"] == name:
            finalize(claim.pk, reg.pk, providers, now, adopted=True)
            return "registered"
    return _unknown(claim, reg, now)


def _unknown(claim, reg, now):
    if now - reg.register_sent_at > UNKNOWN_LIMIT:
        to_conflict(claim.pk, reg.pk, "outcome_unknown", now=now)
        return "conflict"
    return "registering"


def finalize(claim_id, reg_id, providers, now, *, adopted=False):
    """The registration succeeded (or was adopted from our own sent POST): record it."""
    ledger()
    with transaction.atomic():
        reg, claim = lock_rows(reg_id, claim_id)
        if reg.state != RegistrationState.SENT:
            return False
        reg.state, reg.completed_at, reg.provider_status, reg.provider_code = (
            RegistrationState.SUCCEEDED, now, "succeeded", "",
        )
        reg.save()
        if claim.state == ClaimState.REGISTERING:
            machine.move(claim, ClaimState.REGISTERED, now=now)
        events.log(
            claim.site, "registered", claim=claim, registration=reg, now=now,
            detail={"name": reg.name, "adopted": adopted},
        )
    _read_registration(reg_id, providers, now)
    return True


def _read_registration(reg_id, providers, now):
    """Best effort: the expiry and auto-renew. A failure is left to the daily read."""
    reg = DomainRegistration.objects.get(pk=reg_id)
    try:
        data = providers.registrar.get(reg.name)
    except ProviderError:
        return
    if data:
        DomainRegistration.objects.filter(pk=reg_id).update(
            expires_at=data["expires_at"], auto_renew=data["auto_renew"], auto_renew_checked_at=now
        )


def to_conflict(claim_id, reg_id, why, *, status="", code="", now=None):
    """The purchase's outcome is unknown or unsafe: the registration and its claim go to `conflict`
    and an operator is alerted. Nothing but `confirmed_registered` moves them."""
    now = now or timezone.now()
    ledger()
    with transaction.atomic():
        reg, claim = lock_rows(reg_id, claim_id)
        if reg.state not in (RegistrationState.SENT,):
            return False
        reg.state, reg.provider_status, reg.provider_code = RegistrationState.CONFLICT, status[:32], code[:64]
        reg.save()
        if claim.state in (ClaimState.REGISTERING, ClaimState.CONFIRMED, ClaimState.AWAITING_GATE):
            machine.move(claim, ClaimState.CONFLICT, reason="Waiting for the NegosyoOnline team.", now=now)
        events.log(
            claim.site, "registration_conflict", claim=claim, registration=reg, now=now,
            detail={"why": why, "status": status[:32], "code": code[:64]},
        )
    alert("registration_conflict", claim, why=why)
    return True


# --- releasing a name that was never sent ----------------------------------------------------

def release(claim, reg, reason, *, kind, actor_type=ActorType.SYSTEM, actor_id=None, now=None):
    """End an unsent request: the claim first (`ended`), then the registration's name back to NULL
    with every `check_*` field cleared, in one transaction under the purchase lock (design
    R4.14.3 item 7). Used by the refused worker Check and by cancel request; refused for any row
    that has sent."""
    now = now or timezone.now()
    ledger()
    with transaction.atomic():
        locked, fresh = lock_rows(reg.pk, claim.pk)
        if (
            locked.state != RegistrationState.INTENT or locked.register_sent_at is not None
            or locked.send_count != 0 or fresh.state not in UNSENT_CLAIM_STATES
        ):
            return False
        machine.move(fresh, ClaimState.ENDED, reason=reason, now=now, allow_name_release=True)
        locked.name = None
        clear_check(locked)
        locked.save()
        events.log(
            fresh.site, kind, claim=fresh, registration=locked, actor_type=actor_type, actor_id=actor_id,
            detail={"name": fresh.name}, now=now,
        )
    return True


def rename_intent(claim, reg, new_name, *, actor_id, now=None):
    """Change the name of an unsent request in place: the claim first, then the registration (with
    every `check_*` field cleared), under the purchase lock. The worker re-Checks before any send."""
    now = now or timezone.now()
    ledger()
    with transaction.atomic():
        locked, fresh = lock_rows(reg.pk, claim.pk)
        if (
            locked.state != RegistrationState.INTENT or locked.register_sent_at is not None
            or fresh.state not in (ClaimState.CONFIRMED, ClaimState.AWAITING_GATE)
        ):
            return False
        old = fresh.name
        fresh.name = new_name
        fresh.save(allow_name_release=True)
        locked.name = new_name
        clear_check(locked)
        locked.save()
        events.log(
            fresh.site, "request_renamed", claim=fresh, registration=locked, actor_type=ActorType.SUBSCRIBER,
            actor_id=actor_id, detail={"from": old, "to": new_name}, now=now,
        )
    return True


# --- the two audited staff actions -----------------------------------------------------------

def cancel_request(operator, claim, *, now=None):
    """HQ "cancel request": only an unsent `intent` row (no send, `send_count` 0), never while the
    claim is in `conflict`. Releases the name and ends the claim; the row stays (one per site)."""
    now = now or timezone.now()
    reg = claim.registration()
    if (
        reg is None or reg.state != RegistrationState.INTENT or reg.register_sent_at is not None
        or reg.send_count != 0 or claim.state not in UNSENT_CLAIM_STATES
    ):
        raise ApiError("not_cancellable", "Only a request that was never sent can be cancelled.", status=409)
    if not release(claim, reg, "The request was cancelled.", kind="request_cancelled",
                   actor_type=ActorType.OPERATOR, actor_id=operator.pk, now=now):
        raise ApiError("not_cancellable", "Only a request that was never sent can be cancelled.", status=409)


CONFIRM_KINDS = ("confirm_requested", "conflict_confirmed_registered", "conflict_confirm_refused")


def last_confirm(reg):
    """The newest confirm event of this registration, or None."""
    if reg is None:
        return None
    return DomainEvent.objects.filter(registration=reg, kind__in=CONFIRM_KINDS).order_by("-pk").first()


def confirm_pending(reg):
    last = last_confirm(reg)
    return last is not None and last.kind == "confirm_requested"


def request_confirmed_registered(operator, claim, *, now=None):
    """The staff route's half (the api holds no Registrar token): validate, write the
    `confirm_requested` event, and say whether a request is now pending. No provider call. A second
    request while one is pending writes nothing new. Returns "requested" or "pending"."""
    now = now or timezone.now()
    reg = claim.registration()
    if reg is None or reg.state != RegistrationState.CONFLICT or reg.name is None:
        raise ApiError("confirm_refused", "Only a registration in conflict can be confirmed.", status=409,
                       fields={"reason": ["not_in_conflict"]})
    if confirm_pending(reg):
        return "pending"
    events.log(claim.site, "confirm_requested", claim=claim, registration=reg, actor_type=ActorType.OPERATOR,
               actor_id=operator.pk, now=now)
    return "requested"


def _refuse_confirm(claim, reg, operator_id, reason, now):
    events.log(claim.site, "conflict_confirm_refused", claim=claim, registration=reg, actor_type=ActorType.OPERATOR,
               actor_id=operator_id, detail={"reason": reason}, now=now)
    return reason


def process_confirm(claim_id, providers, *, now=None):
    """The worker's half of "confirmed registered", the only way out of a registration's `conflict`.
    It records what already exists and spends nothing: no `register` call, and no change to
    `register_sent_at`, `send_count`, the name or any cap count. Under the purchase lock it re-checks
    the row is still in `conflict` and a request is pending; then it reads the provider: (b) the
    registration under our account returns this very name, (c) the status is not in_progress, failed,
    action_required or blocked, (d) the provider is configured and not paused. Any failed or
    ambiguous read refuses. Returns "done", "none" (nothing pending) or the refusal reason."""
    now = now or timezone.now()
    claim = Domain.objects.select_related("site").filter(pk=claim_id).first()
    reg = claim.registration() if claim else None
    ledger()
    with transaction.atomic():
        if reg is None:
            return "none"
        locked, _ = lock_rows(reg.pk)
        last = last_confirm(locked)
        if last is None or last.kind != "confirm_requested":
            return "none"
        operator_id = last.actor_id
        if locked.state != RegistrationState.CONFLICT or locked.name is None:
            return _refuse_confirm(claim, locked, operator_id, "not_in_conflict", now)
    if settings.DOMAINS_PROVIDER_PAUSED:
        return _refuse_confirm(claim, reg, operator_id, "paused", now)
    if providers is None or getattr(providers, "registrar", None) is None:
        return _refuse_confirm(claim, reg, operator_id, "provider_unavailable", now)
    try:
        found = providers.registrar.get(reg.name)
    except Paused:
        return _refuse_confirm(claim, reg, operator_id, "paused", now)
    except ProviderError as error:
        return _refuse_confirm(claim, reg, operator_id, "read_failed_" + error.code, now)
    if not found or found["name"] != reg.name:
        return _refuse_confirm(claim, reg, operator_id, "not_in_our_account", now)
    try:
        status = providers.registrar.status(reg.name)
    except Paused:
        return _refuse_confirm(claim, reg, operator_id, "paused", now)
    except ProviderError as error:
        if error.code != "not_found":
            return _refuse_confirm(claim, reg, operator_id, "status_failed_" + error.code, now)
        status = {"state": "unknown"}
    if status["state"] in ("in_progress",) + BAD_STATUSES:
        return _refuse_confirm(claim, reg, operator_id, "status_" + status["state"], now)
    with transaction.atomic():
        locked, fresh = lock_rows(reg.pk, claim.pk)
        if locked.state != RegistrationState.CONFLICT or not confirm_pending(locked):
            return "none"
        locked.state, locked.completed_at = RegistrationState.SUCCEEDED, now
        locked.provider_status, locked.provider_code = "succeeded", ""
        locked.expires_at, locked.auto_renew, locked.auto_renew_checked_at = (
            found["expires_at"], found["auto_renew"], now,
        )
        locked.save()
        if fresh.state == ClaimState.CONFLICT:
            machine.move(fresh, ClaimState.REGISTERED, now=now)
        events.log(
            fresh.site, "conflict_confirmed_registered", claim=fresh, registration=locked,
            actor_type=ActorType.OPERATOR, actor_id=operator_id, detail={"name": locked.name}, now=now,
        )
    return "done"


def pending_confirms():
    """Claim ids with a confirm request nobody has answered (the sweep's backstop for a lost task)."""
    out = []
    for claim in Domain.objects.filter(state=ClaimState.CONFLICT):
        if confirm_pending(claim.registration()):
            out.append(claim.pk)
    return out


# --- the daily sentinel Check (design R4.14.4) ------------------------------------------------

def sentinel(providers, now=None):
    """One Check of a random, unregistered sentinel name per allowed extension (a read, no money).
    Stores each extension's reading; withdraws an extension that is not registrable, not standard,
    not USD or whose first-year price is over the ceiling (the renewal price is stored, not capped), and alerts. A failed read leaves the old rows, which go stale."""
    now = now or timezone.now()
    tlds = list(settings.DOMAINS_ALLOWED_TLDS)
    if not tlds or getattr(providers, "registrar", None) is None:
        return {"skipped": "no extensions or no registrar"}
    ceiling = settings.DOMAINS_MAX_COST_CENTS
    done, withdrawn = 0, []
    for start in range(0, len(tlds), 20):
        chunk = {f"nego-{secrets.token_hex(8)}.{tld}": tld for tld in tlds[start:start + 20]}
        try:
            rows = providers.registrar.check(list(chunk))
        except ProviderError as error:
            alert("sentinel_failed", code=error.code)
            continue
        for row in rows:
            tld = chunk.get(row["name"])
            if tld is None or row["currency"] != "USD":
                if tld:
                    DomainTldPrice.objects.filter(tld=tld).update(allowed=False)
                    withdrawn.append(tld)
                    alert("tld_withdrawn", tld=tld, why="currency")
                continue
            allowed = (
                row["registrable"] and row["tier"] == "standard" and ceiling > 0
                and row["registration_cost_cents"] is not None and row["renewal_cost_cents"] is not None
                and row["registration_cost_cents"] <= ceiling
            )
            DomainTldPrice.objects.update_or_create(tld=tld, defaults={
                "registrable": row["registrable"], "tier": row["tier"],
                "registration_cost_cents": row["registration_cost_cents"],
                "renewal_cost_cents": row["renewal_cost_cents"], "currency": "USD",
                "sentinel_name": row["name"], "read_at": now, "allowed": allowed,
            })
            done += 1
            if not allowed:
                withdrawn.append(tld)
                alert("tld_withdrawn", tld=tld, why="over_ceiling_or_not_registrable")
    return {"read": done, "withdrawn": withdrawn}
