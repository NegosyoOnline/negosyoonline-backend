"""The daily sweep (design 2.3 step 11, R4.6, R4.14.4): the zone audit, the Vercel comparison and
the registration read. Reads only. Alerts name zones and domains (public names), never a token,
a body or an id."""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.domains import machine, names
from apps.domains.models import ClaimState, DomainHostname, DomainRegistration, RegistrationState
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
