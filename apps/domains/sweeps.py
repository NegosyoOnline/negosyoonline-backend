"""The daily sweep (design 2.3 step 11, R4.6, R4.14.4): the zone audit, the Vercel comparison and
the registration read. Reads only. Alerts name zones and domains (public names), never a token,
a body or an id."""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.domains import machine, names
from apps.domains.models import (
    ClaimState, Domain, DomainHostname, DomainRegistration, EmailState, NameMismatch, RegistrationState,
)
from apps.domains.providers.base import NotConfigured, Paused, ProviderError

EXPIRY_WARNING = timedelta(days=60)


def zone_audit(providers):
    """Alert, by name, on a zone that is neither a succeeded registration nor a platform zone,
    and on a platform zone the DNS token can see (a denied zone is not listed). With
    DOMAINS_PLATFORM_ZONES empty it reports an error and reads nothing (fail closed)."""
    if not settings.DOMAINS_PLATFORM_ZONES:
        machine.alert("zone_audit_no_platform_zones")
        return {"error": "platform_zones_unset"}
    try:
        zones = providers.dns.list_zone_names()
    except ProviderError as error:
        machine.alert("zone_audit_failed", code=error.code)
        return {"error": error.code}
    registered = set(
        DomainRegistration.objects.filter(state=RegistrationState.SUCCEEDED).values_list("name", flat=True)
    )
    platform = set(settings.DOMAINS_PLATFORM_ZONES)
    unknown = sorted(z for z in zones if z not in registered and z not in platform)
    visible = sorted(z for z in zones if z in platform)
    for zone in unknown:
        machine.alert("zone_unknown", zone=zone)
    for zone in visible:
        machine.alert("platform_zone_not_denied", zone=zone)
    return {"zones": len(zones), "unknown": unknown, "platform_visible": visible}


def vercel_compare(providers):
    """The project's domain list against the recorded hostnames. Names under the platform parent
    host are ignored. An unrecorded name, or a recorded one that is missing, alerts."""
    try:
        listed = set(providers.vercel.list())
    except ProviderError as error:
        machine.alert("vercel_list_failed", code=error.code)
        return {"error": error.code}
    parent = settings.SITE_PARENT_HOST
    listed = {n for n in listed if not (parent and (n == parent or n.endswith("." + parent)))}
    recorded = set(
        DomainHostname.objects.exclude(vercel_added_at=None).filter(holding=True)
        .values_list("vercel_domain_name", flat=True)
    )
    extra, missing = sorted(listed - recorded), sorted(recorded - listed)
    for name in extra:
        machine.alert("vercel_unrecorded", name=name)
    for name in missing:
        machine.alert("vercel_missing", name=name)
    return {"unrecorded": extra, "missing": missing}


def registration_read(providers, now=None):
    """Read each succeeded registration (expiry, auto-renew) through the registrar read. B1 has no
    Registrar adapter, so with none (the real mode today) it reads nothing and says so."""
    now = now or timezone.now()
    reader = providers.registrar_read
    if reader is None:
        return {"skipped": "no registrar adapter (B2)"}
    read = 0
    for reg in DomainRegistration.objects.filter(state=RegistrationState.SUCCEEDED).exclude(name=None):
        try:
            data = reader.get(reg.name)
        except Paused:
            return {"skipped": "paused"}
        except NotConfigured:
            return {"skipped": "no registrar token"}
        except ProviderError as error:
            machine.alert("registration_read_failed", name=reg.name, code=error.code)
            continue
        if data is None:
            machine.alert("registration_not_found", name=reg.name)
            continue
        reg.expires_at = data.get("expires_at")
        reg.auto_renew = data.get("auto_renew")
        reg.auto_renew_checked_at = now
        reg.save(update_fields=["expires_at", "auto_renew", "auto_renew_checked_at", "updated_at"])
        subscribed = reg.site.domain_claims.exclude(state__in=(ClaimState.ENDED, ClaimState.REMOVAL_PENDING)).exists()
        if subscribed and reg.auto_renew is False:
            machine.alert("auto_renew_off", name=reg.name)
        if reg.expires_at and reg.expires_at - now <= EXPIRY_WARNING:
            machine.alert("expires_soon", name=reg.name)
        read += 1
    return {"read": read}


def resend_daily(providers, now=None):
    """The daily Resend read (design R4.14.7 (a)): re-read the status of every verified domain of
    ours, so the sender lookup sees a `temporary_failure` or `failed` at once (it falls back) and a
    stale reading falls back too; then compare Resend's list with the recorded ids. Reads only."""
    now = now or timezone.now()
    resend = getattr(providers, "resend", None)
    if resend is None:
        return {"skipped": "no resend adapter"}
    read = 0
    for claim in Domain.objects.select_related("site").filter(
        email_state__in=(EmailState.VERIFIED, EmailState.ACTIVE)
    ).exclude(state__in=(ClaimState.ENDED, ClaimState.REMOVAL_PENDING)).exclude(resend_domain_id=""):
        try:
            reg = machine._registration(claim)
            claim.assert_name_bound()
            if reg is None:
                continue
            data = resend.get(claim.resend_domain_id)
        except (NameMismatch, Paused, NotConfigured):
            continue
        except ProviderError as error:
            machine.alert("resend_read_failed", claim, code=error.code)  # the old reading ages out
            continue
        if data["name"] != reg.name:
            machine.alert("resend_name_mismatch", claim)
            claim.resend_status = ""  # unknown: the sender falls back
        else:
            if data["status"] in ("temporary_failure", "failed", "partially_failed"):
                machine.alert("resend_not_verified", claim, status=data["status"])
            claim.resend_status = data["status"]
            if data["status"] == "verified" and claim.email_state == EmailState.VERIFIED:
                claim.email_state = EmailState.ACTIVE
        claim.resend_checked_at = now
        claim.save(update_fields=["resend_status", "resend_checked_at", "email_state", "updated_at"])
        read += 1
    try:
        listed = resend.list()
    except ProviderError as error:
        machine.alert("resend_list_failed", code=error.code)
        return {"read": read, "error": error.code}
    platform = settings.DOMAINS_PLATFORM_ZONES
    ours = {n for n in (i["name"] for i in listed)
            if not any(n == z or n.endswith("." + z) for z in platform)}
    recorded_ids = set(Domain.objects.exclude(resend_domain_id="").values_list("resend_domain_id", flat=True))
    extra = sorted(i["name"] for i in listed if i["name"] in ours and i["id"] not in recorded_ids)
    missing = sorted(set(
        Domain.objects.exclude(resend_domain_id="").exclude(state=ClaimState.ENDED).values_list("resend_domain_id", flat=True)
    ) - {i["id"] for i in listed})
    for name in extra:
        machine.alert("resend_unrecorded", name=name)
    for _ in missing:
        machine.alert("resend_missing")
    return {"read": read, "unrecorded": extra, "missing": len(missing)}
