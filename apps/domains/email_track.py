"""The email track of a claim: a Resend sending domain on the customer's own domain (design R4.5,
R4.14.7, R4.14.8, plan r4.3 item 3). It runs beside the website track and never changes it: the
track writes only the claim's email columns, an `email_failed` never fails the website, and a
claim that waits for capacity never blocks or refuses the website or the purchase.

Steps, one per pass (the minute sweep), each recorded before it is relied on:

* `email_none` / `email_waiting_capacity`: a slot is needed (occupied Resend domains of ours under
  `DOMAINS_MAX_EMAIL_DOMAINS`, unset = 0); with none the claim waits, with the time it began and
  an operator alert, and resumes when a slot exists.
* `email_creating`: Resend `create` (name = the site's registered name, fixed region, sending only).
  The id is recorded. A create that answers "exists" is never adopted (`email_failed`).
* `email_dns`: the records Resend returned are read again from Resend (never hard-coded) and
  written into the zone DNS-only, each id recorded, list before create.
* `email_verifying`: Resend verify, then a poll every few minutes for up to 72 hours.
* `email_verified` then `email_active` (the daily read confirmed it): the sender lookup in
  `apps.notifications.store_email` may use the domain while Resend says `verified`.

Everything runs under the claim's single-flight lock, in the worker, only while
`DOMAINS_EMAIL_ENABLED` is on, and takes every name from the site's own succeeded registration.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.domains import events, machine
from apps.domains.models import (
    ClaimState, Domain, DomainDnsRecord, EmailState, NameMismatch,
)
from apps.domains.providers.base import Paused, ProviderError
from apps.domains.providers.resend_domains import RECORD_TYPES

logger = logging.getLogger("domains")

EMAIL_LIMIT = timedelta(hours=72)  # Resend's own window; also the limit of the other phases
POLL_EVERY = timedelta(minutes=5)
READ_FRESH = timedelta(hours=72)  # an older reading of Resend is "unknown", so the sender falls back
RUN_STATES = (
    ClaimState.REGISTERED, ClaimState.ZONE, ClaimState.CONNECTING, ClaimState.ACTIVATING,
    ClaimState.LIVE, ClaimState.ATTENTION,
)
TRACK_STATES = (
    EmailState.NONE, EmailState.WAITING_CAPACITY, EmailState.CREATING, EmailState.DNS, EmailState.VERIFYING,
)
OCCUPYING = (
    EmailState.CREATING, EmailState.DNS, EmailState.VERIFYING, EmailState.VERIFIED, EmailState.ACTIVE,
)
SLOT_LOCK = "domains:email:slot"


def due_ids():
    """Claims the minute sweep gives an email step, longest-waiting first."""
    if not settings.DOMAINS_EMAIL_ENABLED:
        return []
    rows = Domain.objects.filter(state__in=RUN_STATES, email_state__in=TRACK_STATES)
    return [c.pk for c in sorted(rows, key=lambda c: (c.email_waiting_since or c.created_at, c.pk))]


def occupied(excluding=None):
    """Our Resend domains that exist or are being made: they take a slot of the plan."""
    rows = Domain.objects.exclude(state=ClaimState.ENDED).filter(
        Q(email_state__in=OCCUPYING) | ~Q(resend_domain_id="")
    )
    if excluding is not None:
        rows = rows.exclude(pk=excluding.pk)
    return rows.count()


def _event(claim, kind, *, from_state="", to_state="", now=None, **detail):
    return events.log(
        claim.site, kind, claim=claim, from_state=from_state, to_state=to_state,
        detail=events.clean(detail), now=now,
    )


def _set(claim, state, now, **fields):
    old = claim.email_state
    claim.email_state = state
    for key, value in fields.items():
        setattr(claim, key, value)
    claim.save(update_fields=["email_state", "updated_at", *fields])
    _event(claim, "email_state", from_state=old, to_state=state, now=now)


def _fail(claim, reason, now):
    _set(claim, EmailState.FAILED, now)
    _event(claim, "email_failed", now=now, reason=reason)
    machine.alert("email_failed", claim, reason=reason)
    return "failed"


def advance(claim_id, *, providers, now=None):
    """One email step for one claim. Returns a short word; never raises."""
    now = now or timezone.now()
    if providers is None or getattr(providers, "resend", None) is None or not settings.DOMAINS_EMAIL_ENABLED:
        return "off"
    if not cache.add(machine._lock_key(claim_id), 1, 300):  # one step at a time for a claim, both tracks
        return "busy"
    try:
        claim = Domain.objects.select_related("site").get(pk=claim_id)
        if claim.state not in RUN_STATES or claim.email_state not in TRACK_STATES:
            return "idle"
        try:
            reg = machine._registration(claim)
            claim.assert_name_bound()
        except NameMismatch:
            machine.alert("name_mismatch", claim)
            _event(claim, "refused_name_mismatch", now=now)
            return "refused"
        if reg is None:
            return "waiting"
        if not machine._site_ok(claim.site):
            return "site_off"
        try:
            return _step(claim, reg, providers, now)
        except Paused:
            return "paused"
        except ProviderError as error:
            if error.retryable:
                return "retry"
            machine.alert("provider_refused", claim, code=error.code, track="email")
            _event(claim, "provider_refused", code=error.code, now=now)
            if claim.email_started_at and now - claim.email_started_at > EMAIL_LIMIT:
                return _fail(claim, "provider_refused", now)
            return "refused"
    except Exception:  # noqa: BLE001 - one claim never stops the sweep
        logger.exception("domains email step failed claim_id=%s", claim_id)
        return "error"
    finally:
        cache.delete(machine._lock_key(claim_id))


def _step(claim, reg, providers, now):
    state = claim.email_state
    if state in (EmailState.NONE, EmailState.WAITING_CAPACITY):
        word = _start(claim, now)
        if word != "creating":
            return word
        state = claim.email_state
    if state == EmailState.CREATING:
        word = _create(claim, reg, providers, now)
        if word != "dns":
            return word
    if claim.email_state == EmailState.DNS:
        word = _dns(claim, reg, providers, now)
        if word != "verifying":
            return word
        return "verifying"
    if claim.email_state == EmailState.VERIFYING:
        return _verifying(claim, reg, providers, now)
    return "idle"


def _start(claim, now):
    """Take a slot, or wait for one. The slot lock spans the count and the write that uses it."""
    if not cache.add(SLOT_LOCK, 1, 60):
        return "busy"
    try:
        used, cap = occupied(excluding=claim), settings.DOMAINS_MAX_EMAIL_DOMAINS
        if used >= cap:
            if claim.email_state == EmailState.NONE:
                _set(claim, EmailState.WAITING_CAPACITY, now, email_waiting_since=now)
                _event(claim, "email_waiting_capacity", now=now, occupied=used, capacity=cap)
                machine.alert("email_waiting_capacity", claim, occupied=used, capacity=cap)
            return "waiting_capacity"
        _set(claim, EmailState.CREATING, now, email_started_at=now, email_waiting_since=None)
        return "creating"
    finally:
        cache.delete(SLOT_LOCK)


def _expired(claim, now):
    return bool(claim.email_started_at) and now - claim.email_started_at > EMAIL_LIMIT


def _create(claim, reg, providers, now):
    if _expired(claim, now):
        return _fail(claim, "too_slow", now)
    if claim.resend_domain_id:  # recorded already: never create a second one
        _set(claim, EmailState.DNS, now)
        return "dns"
    _event(claim, "resend_create_sent", now=now, region=settings.RESEND_REGION)
    try:
        made = providers.resend.create(claim.site, reg.name)
    except ProviderError as error:
        if error.code == "conflict":  # it exists already: never adopted automatically
            return _fail(claim, "domain_exists", now)
        raise
    claim.resend_domain_id, claim.resend_region = made["id"], made["region"] or settings.RESEND_REGION
    claim.resend_status, claim.resend_checked_at = made["status"], now
    claim.save(update_fields=["resend_domain_id", "resend_region", "resend_status", "resend_checked_at", "updated_at"])
    _event(claim, "resend_created", now=now, status=made["status"], region=claim.resend_region, count=len(made["records"]))
    if not made["id"] or made["name"] != reg.name:
        return _fail(claim, "answer_not_ours", now)
    _set(claim, EmailState.DNS, now)
    return "dns"


def _full_name(name, zone_name):
    """Resend names a record relative to the domain ("resend._domainkey"); the zone wants it whole."""
    if name in ("", "@"):
        return zone_name
    return name if name == zone_name or name.endswith("." + zone_name) else f"{name}.{zone_name}"


def _dns(claim, reg, providers, now):
    if _expired(claim, now):
        return _fail(claim, "too_slow", now)
    data = providers.resend.get(claim.resend_domain_id)
    if data["name"] != reg.name:
        return _fail(claim, "answer_not_ours", now)
    if not data["records"] or any(r["type"] not in RECORD_TYPES or not r["value"] for r in data["records"]):
        return _fail(claim, "records_not_allowed", now)
    zone = providers.dns.zone_for(claim.site, reg.name)
    if zone is None:
        return "waiting_zone"
    existing = providers.dns.list_records(zone)
    for rec in data["records"]:
        name = _full_name(rec["name"], reg.name)
        mine = DomainDnsRecord.objects.filter(
            claim=claim, purpose=DomainDnsRecord.Purpose.RESEND, record_type=rec["type"], name=name,
            content=rec["value"], removed_at__isnull=True,
        ).exists()
        if mine:
            continue
        if any(r["type"] == rec["type"] and r["name"] == name for r in existing):
            return _fail(claim, "dns_clash", now)  # a record we did not write is in the way
        created = providers.dns.create_record(
            zone, type=rec["type"], name=name, content=rec["value"], priority=rec["priority"], ttl=machine.DNS_TTL
        )
        DomainDnsRecord.objects.create(
            claim=claim, purpose=DomainDnsRecord.Purpose.RESEND, zone_id=zone.id, record_id=created["id"],
            record_type=rec["type"], name=name, content=rec["value"], priority=rec["priority"],
            ttl=machine.DNS_TTL, created_at=now,
        )
    providers.resend.verify(claim.resend_domain_id)
    _set(claim, EmailState.VERIFYING, now, email_started_at=now)  # the verification window starts here
    return "verifying"


def _verifying(claim, reg, providers, now):
    if _expired(claim, now):
        return _fail(claim, "not_verified_in_72_hours", now)
    if claim.resend_checked_at and now - claim.resend_checked_at < POLL_EVERY:
        return "verifying"
    data = providers.resend.get(claim.resend_domain_id)
    if data["name"] != reg.name:
        return _fail(claim, "answer_not_ours", now)
    claim.resend_status, claim.resend_checked_at = data["status"], now
    claim.save(update_fields=["resend_status", "resend_checked_at", "updated_at"])
    if data["status"] == "verified":
        _set(claim, EmailState.VERIFIED, now, email_verified_at=now)
        return "verified"
    if data["status"] == "failed":
        return _fail(claim, "resend_failed", now)
    if data["status"] == "not_started":
        providers.resend.verify(claim.resend_domain_id)
    return "verifying"


def retry(claim, *, now=None):
    """An operator retry of `email_failed`: with a Resend domain recorded it goes back to the DNS
    step (never a second create), otherwise to the start. The website is not touched."""
    now = now or timezone.now()
    if claim.email_state != EmailState.FAILED or claim.state not in RUN_STATES:
        return False
    state = EmailState.DNS if claim.resend_domain_id else EmailState.NONE
    _set(claim, state, now, email_started_at=now, email_waiting_since=None)
    return True


def remove(claim, reg, providers, now):
    """Removal: delete the recorded Resend domain (read first, name must equal the registration's).
    Returns True when nothing is left to delete. The recorded DNS records are deleted by the
    website removal, which covers both purposes."""
    if not claim.resend_domain_id:
        return True
    if getattr(providers, "resend", None) is None or not settings.DOMAINS_EMAIL_ENABLED:
        machine.alert("resend_removal_waiting", claim)
        return False
    try:
        providers.resend.delete(claim.site, claim.resend_domain_id, name=reg.name)
    except ProviderError as error:
        if error.code != "not_found":  # already gone counts as removed
            raise
    _event(claim, "resend_removed", now=now)
    return True
