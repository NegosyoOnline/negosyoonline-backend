"""Which site a custom hostname belongs to, and a site's canonical public host (design 6).

`site_for_hostname` is the second branch of `apps.sites.api_public.site_for_host`; it is tried
only when the host is not under the platform parent host. A hostname answers only while its claim
is in a served state (`live`, `attention`), the hostname is the claim's own `www.` name, and that
name is the site's own `succeeded` registration. Everything off, missing or odd answers None.
"""
from django.conf import settings

from apps.domains.models import (
    SERVED_STATES, ClaimState, DomainHostname, DomainRegistration, RegistrationState,
)
from apps.sites import custom_hosts

PROBE_STATES = (ClaimState.ACTIVATING, ClaimState.LIVE, ClaimState.ATTENTION)


def _own(row):
    """True when the hostname row is the www name of its site's own succeeded registration."""
    claim = row.claim
    reg = DomainRegistration.objects.filter(site_id=claim.site_id).first()
    return (
        reg is not None and reg.state == RegistrationState.SUCCEEDED and bool(reg.name)
        and claim.name == reg.name and row.hostname == f"www.{reg.name}"
        and row.role == DomainHostname.Role.WWW
    )


def _row(host, states):
    if not settings.DOMAINS_ENABLED:
        return None
    name = custom_hosts.key(host)
    if not name:
        return None
    row = (
        DomainHostname.objects.filter(
            hostname=name, role=DomainHostname.Role.WWW, holding=True, claim__state__in=states,
            claim__site__deleted_at__isnull=True,
        )
        .select_related("claim", "claim__site")
        .first()
    )
    return row if row is not None and _own(row) else None


def site_for_hostname(host):
    """The served site of a custom hostname, not removed and not on operator hold, or None.
    Nothing about billing: `served` adds that."""
    row = _row(host, SERVED_STATES)
    if row is None or row.claim.site.operator_hold:
        return None
    return row.claim.site


def site_for_probe(host):
    """The site a hostname is being connected for, by domain state only (a suspended site's
    domain still probes). Not a served answer."""
    row = _row(host, PROBE_STATES)
    return None if row is None else row.claim.site


def canonical_host(site):
    """`www.<domain>` while the site's claim is live, else "". The three emailed links and
    `custom_host` use it behind "claim live"; the caller falls back to the subdomain."""
    if not settings.DOMAINS_ENABLED:
        return ""
    row = (
        DomainHostname.objects.filter(
            claim__site=site, role=DomainHostname.Role.WWW, holding=True,
            claim__state__in=SERVED_STATES,
        )
        .select_related("claim")
        .first()
    )
    return row.hostname if row is not None and _own(row) else ""
