"""The claim state machine, from `registered` to `ended` (design R4.3, plan r4.3 item 4).

`advance(claim)` takes one step for one claim and never raises: a provider failure that may pass
is retried by the next sweep and counts against the timers (72 hours in `zone` and `connecting`,
24 in `activating`, 14 days in `attention`); anything unsafe stops the step. The purchase states
(`checking` to `registering`) belong to B2 and are never advanced here. Before every provider
call the claim's name is checked against the site's own registration (R6-M3), and every
provider call takes its names from that registration, never from a request.

The clock is a parameter (`now`), so tests inject it.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.domains import gate, names, probe as probe_module
from apps.domains.models import (
    HOLDING_STATES, ClaimState, Domain, DomainDnsRecord, DomainEvent, DomainHostname,
    EmailState, NameMismatch, RegistrationState,
)
from apps.domains.providers.base import Paused, ProviderError

logger = logging.getLogger("domains")

CONNECTING_LIMIT = timedelta(hours=72)
ACTIVATING_LIMIT = timedelta(hours=24)
ATTENTION_AFTER = timedelta(hours=6)
ATTENTION_LIMIT = timedelta(days=14)
STEP_STATES = (
    ClaimState.REGISTERED, ClaimState.ZONE, ClaimState.CONNECTING, ClaimState.ACTIVATING,
    ClaimState.REMOVAL_PENDING,
)
CHECK_STATES = (ClaimState.LIVE, ClaimState.ATTENTION)
DNS_TTL = 1  # automatic


def alert(kind, claim=None, **detail):
    """An operator alert: an error log line with fixed fields only (no body, no token)."""
    logger.error("domains alert %s claim=%s %s", kind, getattr(claim, "public_id", "-"), detail)


def record_event(claim, kind, *, from_state="", to_state="", detail=None, now=None):
    return DomainEvent.objects.create(
        site_id=claim.site_id, claim=claim, kind=kind, from_state=from_state, to_state=to_state,
        detail=detail or {}, created_at=now or timezone.now(),
    )


def sync_holding(claim):
    claim.hostnames.update(holding=claim.state in HOLDING_STATES)


def move(claim, state, *, reason="", now=None, allow_name_release=False, **fields):
    """Write a state change with its event. `fields` are timer columns set at the same time.
    `allow_name_release` is the explicit exemption of the name assertion (cancel, a refused
    worker Check, a rename of an unsent request); nothing else passes it."""
    now = now or timezone.now()
    old = claim.state
    claim.state, claim.reason, claim.state_changed_at = state, reason[:200], now
    for key, value in fields.items():
        setattr(claim, key, value)
    claim.save(allow_name_release=allow_name_release)
    sync_holding(claim)
    record_event(claim, "state", from_state=old, to_state=state, detail={"reason": reason[:200]}, now=now)


def _lock_key(claim_id):
    return f"domains:claim:{claim_id}"


def _registration(claim):
    reg = claim.registration()
    if reg is None or reg.state != RegistrationState.SUCCEEDED or not reg.name:
        return None
    if reg.name != claim.name:
        raise NameMismatch("name")
    return reg


def _site_ok(site):
    """The setup steps run only while the payment gate holds (state ACTIVE with a paid cycle, not
    on hold, not exempt): a re-attach on a suspended, cancelled or held site waits (R8-m1)."""
    return not gate.refusal(site)


def advance(claim_id, *, providers, now=None, probe_fn=None):
    """One step for one claim. Returns a short word for logs and tests; never raises."""
    now = now or timezone.now()
    if providers is None:
        return "off"
    if not cache.add(_lock_key(claim_id), 1, 300):
        return "busy"  # single flight
    try:
        claim = Domain.objects.select_related("site").get(pk=claim_id)
        if claim.state not in STEP_STATES:
            return "idle"
        try:
            reg = _registration(claim)
            claim.assert_name_bound()
        except NameMismatch:
            alert("name_mismatch", claim)
            record_event(claim, "refused_name_mismatch", now=now)
            return "refused"
        if reg is None:
            return "waiting"  # a registration that is not `succeeded` is never worked on
        try:
            return _step(claim, reg, providers, now, probe_fn or probe_module.probe)
        except Paused:
            return "paused"
        except ProviderError as error:
            if error.retryable:
                return "retry"
            alert("provider_refused", claim, code=error.code)
            record_event(claim, "provider_refused", detail={"code": error.code}, now=now)
            return "refused"
    except Exception:  # noqa: BLE001 - one claim never stops the sweep
        logger.exception("domains advance failed claim_id=%s", claim_id)
        return "error"
    finally:
        cache.delete(_lock_key(claim_id))


def _step(claim, reg, providers, now, probe_fn):
    state = claim.state
    if state == ClaimState.REMOVAL_PENDING:
        return _remove(claim, reg, providers, now)
    if not _site_ok(claim.site):
        return "site_off"
    if state == ClaimState.REGISTERED:
        move(claim, ClaimState.ZONE, now=now)
        return "zone"
    if state == ClaimState.ZONE:
        return _zone(claim, reg, providers, now)
    if state == ClaimState.CONNECTING:
        return _connect(claim, reg, providers, now)
    if state == ClaimState.ACTIVATING:
        return _activate(claim, reg, now, probe_fn)
    return "idle"


def _zone(claim, reg, providers, now):
    if providers.dns.zone_for(claim.site, reg.name) is None:
        if now - claim.state_changed_at > CONNECTING_LIMIT:
            move(claim, ClaimState.FAILED, reason="The DNS zone was not found.", now=now)
            return "failed"
        return "waiting_zone"
    move(claim, ClaimState.CONNECTING, now=now, connecting_started_at=claim.connecting_started_at or now)
    return "connecting"


def _hostnames(claim, reg):
    """The two hostnames, made only from the registration name; created once."""
    made = {}
    for role, host in ((DomainHostname.Role.WWW, f"www.{reg.name}"), (DomainHostname.Role.APEX, reg.name)):
        row = claim.hostnames.filter(role=role).first()
        if row is None:
            row = DomainHostname(claim=claim, role=role, hostname=host, holding=True)
            row.save()
        made[role] = row
    return made


def _ensure_records(claim, hostname, zone, providers, now):
    """Write Vercel's required records DNS-only, recording each id. List before create: a record
    of the same type and name that we did not write stops the claim (conflict)."""
    config = providers.vercel.config(hostname.hostname, allowed=_allowed(claim))
    existing = providers.dns.list_records(zone)
    wanted = "CNAME" if hostname.role == DomainHostname.Role.WWW else "A"  # apex by A, www by CNAME
    for rec in (r for r in config["records"] if r["type"] == wanted):
        mine = DomainDnsRecord.objects.filter(
            claim=claim, hostname=hostname, record_type=rec["type"], name=hostname.hostname,
            content=rec["content"], removed_at__isnull=True,
        ).exists()
        if mine:
            continue
        clash = [r for r in existing if r["type"] == rec["type"] and r["name"] == hostname.hostname]
        if clash:
            raise _Clash()
        created = providers.dns.create_record(
            zone, type=rec["type"], name=hostname.hostname, content=rec["content"], ttl=DNS_TTL
        )
        DomainDnsRecord.objects.create(
            claim=claim, hostname=hostname, purpose=DomainDnsRecord.Purpose.VERCEL, zone_id=zone.id,
            record_id=created["id"], record_type=rec["type"], name=hostname.hostname,
            content=rec["content"], ttl=DNS_TTL, created_at=now,
        )
    return config


class _Clash(Exception):
    pass


def _allowed(claim):
    return frozenset(claim.hostnames.values_list("hostname", flat=True))


def _connect(claim, reg, providers, now):
    if now - (claim.connecting_started_at or claim.state_changed_at) > CONNECTING_LIMIT:
        move(claim, ClaimState.FAILED, reason="Connecting took too long.", now=now)
        return "failed"
    try:
        with transaction.atomic():
            rows = _hostnames(claim, reg)
    except IntegrityError:
        move(claim, ClaimState.CONFLICT, reason="The name is held by another claim.", now=now)
        alert("hostname_held", claim)
        return "conflict"
    zone = providers.dns.zone_for(claim.site, reg.name)
    if zone is None:
        return "waiting_zone"
    allowed = _allowed(claim)
    www = rows[DomainHostname.Role.WWW]
    try:
        _connect_one(claim, www, zone, providers, allowed, now, redirect_to=None)
    except _Clash:
        move(claim, ClaimState.CONFLICT, reason="A record we did not write is in the way.", now=now)
        alert("dns_clash", claim)
        return "conflict"
    except ProviderError as error:
        if error.code == "conflict":
            move(claim, ClaimState.CONFLICT, reason="The domain is on another Vercel project.", now=now)
            alert("vercel_other_project", claim)
            return "conflict"
        raise
    apex = rows[DomainHostname.Role.APEX]
    try:  # a failing apex never fails www
        _connect_one(claim, apex, zone, providers, allowed, now, redirect_to=f"www.{reg.name}")
    except (ProviderError, _Clash) as error:
        record_event(claim, "apex_failed", detail={"code": getattr(error, "code", "dns_clash")}, now=now)
    www.refresh_from_db()
    if www.vercel_verified and www.vercel_cert_state == "ok":
        move(claim, ClaimState.ACTIVATING, now=now, activating_started_at=now)
        return "activating"
    return "connecting"


def _connect_one(claim, hostname, zone, providers, allowed, now, *, redirect_to):
    name = hostname.hostname
    if hostname.vercel_added_at is None:
        try:
            providers.vercel.add(name, allowed=allowed, redirect_to=redirect_to)
        except ProviderError as error:
            if error.code != "bad_request":  # 400: already on this project, read it instead
                raise
            providers.vercel.get(name, allowed=allowed)
        hostname.vercel_domain_name, hostname.vercel_added_at = name, now
        hostname.redirect_to = redirect_to or ""
        hostname.save()
    config = _ensure_records(claim, hostname, zone, providers, now)
    data = providers.vercel.verify(name, allowed=allowed)
    hostname.vercel_verified = data["verified"]
    ok = data["verified"] and not config["misconfigured"]
    hostname.vercel_cert_state = "ok" if ok else "pending"
    if ok:
        hostname.last_config_ok_at = now
    hostname.save()


def _activate(claim, reg, now, probe_fn):
    if names.refusal(reg.name, exclude_claim=claim):
        move(claim, ClaimState.FAILED, reason="That name can no longer be connected.", now=now)
        return "failed"
    www = claim.hostnames.get(role=DomainHostname.Role.WWW)
    www.assert_own_name()
    if _probe_ok(claim, www, probe_fn, now):
        www.probe_failing_since = None
        www.save()
        move(claim, ClaimState.LIVE, now=now)
        return "live"
    if now - (claim.activating_started_at or claim.state_changed_at) > ACTIVATING_LIMIT:
        move(claim, ClaimState.FAILED, reason="The website did not answer over HTTPS.", now=now)
        return "failed"
    return "activating"


def _probe_ok(claim, hostname, probe_fn, now):
    nonce = probe_module.make_nonce()
    result = probe_fn(hostname.hostname, nonce)
    hostname.last_probe_at = now
    hostname.save()
    return bool(result.ok and result.site == claim.site.public_id)


def hourly_check(claim_id, *, providers, now=None, probe_fn=None):
    """A live or attention claim: read Vercel and probe. Six hours of failure is `attention`;
    14 days of it is `failed`; a recovery returns it to `live`. Never raises."""
    now = now or timezone.now()
    if providers is None:
        return "off"
    try:
        claim = Domain.objects.select_related("site").get(pk=claim_id)
        if claim.state not in CHECK_STATES:
            return "idle"
        try:
            reg = _registration(claim)
        except NameMismatch:
            alert("name_mismatch", claim)
            return "refused"
        if reg is None:
            return "waiting"
        www = claim.hostnames.get(role=DomainHostname.Role.WWW)
        www.assert_own_name()
        healthy = True
        try:
            data = providers.vercel.get(www.hostname, allowed=_allowed(claim))
            conf = providers.vercel.config(www.hostname, allowed=_allowed(claim))
            healthy = data["verified"] and not conf["misconfigured"]
        except ProviderError as error:
            if not error.retryable and error.code != "not_found":
                alert("provider_refused", claim, code=error.code)
            healthy = False
        probed = _probe_ok(claim, www, probe_fn or probe_module.probe, now)
        if healthy and probed:
            www.probe_failing_since, www.last_config_ok_at = None, now
            www.save()
            if claim.state == ClaimState.ATTENTION:
                move(claim, ClaimState.LIVE, now=now, attention_since=None)
            return "ok"
        if www.probe_failing_since is None:
            www.probe_failing_since = now
            www.save()
        if claim.state == ClaimState.LIVE and now - www.probe_failing_since >= ATTENTION_AFTER:
            move(claim, ClaimState.ATTENTION, reason="The website is not answering.", now=now, attention_since=now)
            alert("attention", claim)
            return "attention"
        if claim.state == ClaimState.ATTENTION and now - (claim.attention_since or now) >= ATTENTION_LIMIT:
            move(claim, ClaimState.FAILED, reason="The website has not answered for 14 days.", now=now)
            return "failed"
        return "failing"
    except Exception:  # noqa: BLE001
        logger.exception("domains hourly check failed claim_id=%s", claim_id)
        return "error"


def retry(claim, *, now=None):
    """Setup steps only: `failed` goes back to `zone` (never to a purchase step). Returns True if moved."""
    now = now or timezone.now()
    if claim.state != ClaimState.FAILED:
        from apps.domains import email_track  # noqa: PLC0415
        return email_track.retry(claim, now=now)  # a failed email track, the website untouched
    reg = claim.registration()
    if reg is None or reg.state != RegistrationState.SUCCEEDED:
        return False
    claim.connecting_started_at = None
    move(claim, ClaimState.ZONE, now=now)
    return True


def request_removal(claim, *, reason="", now=None):
    """Put a claim into `removal_pending`: it leaves the served set at once (a state outside
    SERVED_STATES). The provider entries are removed by the next sweep."""
    now = now or timezone.now()
    if claim.state in (ClaimState.REMOVAL_PENDING, ClaimState.ENDED):
        return False
    move(claim, ClaimState.REMOVAL_PENDING, reason=reason, now=now)
    return True


def _remove(claim, reg, providers, now):
    """Remove only recorded entries: Vercel names we added, DNS records we wrote. The
    registration is never touched. A refusal or error keeps `removal_pending` and alerts."""
    allowed = _allowed(claim)
    for host in claim.hostnames.exclude(vercel_added_at=None).exclude(vercel_domain_name=""):
        try:
            providers.vercel.remove(host.vercel_domain_name, allowed=allowed)
        except ProviderError as error:
            if error.code != "not_found":  # the entry's own not-found counts as removed
                raise
    resend_done, resend_error = False, None
    try:  # the email track's Resend domain: independent of the DNS step below, finished before `ended`
        from apps.domains import email_track  # noqa: PLC0415 - email_track imports this module
        resend_done = email_track.remove(claim, reg, providers, now)
    except ProviderError as error:
        resend_error = error
    pending = claim.dns_records.filter(removed_at__isnull=True)
    if pending.exists():
        zone = providers.dns.zone_for(claim.site, reg.name)
        if zone is None:
            alert("zone_missing_at_removal", claim)
            return "stuck"
        for rec in pending:
            try:
                providers.dns.delete_record(zone, rec.record_id)
            except ProviderError as error:
                if error.code != "not_found":
                    raise
            rec.removed_at = now
            rec.save()
    if resend_error is not None:
        raise resend_error
    if not resend_done:
        return "stuck"
    if claim.resend_domain_id:
        claim.email_state, claim.resend_status = EmailState.NONE, ""
    claim.hostnames.update(vercel_verified=False, vercel_cert_state="")
    move(claim, ClaimState.ENDED, reason=claim.reason, now=now)
    return "ended"
