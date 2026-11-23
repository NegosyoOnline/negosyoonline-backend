"""Custom public hosts: the rules for a host a site is served on besides its subdomain, the lookup
and the one canonical public address of a site. The host is untrusted input everywhere here."""
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection

from apps.sites.models import SiteCustomHost

HOST_MAX_LENGTH = 253
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TLD = re.compile(r"^[a-z]{2,63}$")


def key(raw):
    """The lookup form of a request host: lowercase, no port, no trailing dot. Not validated."""
    if not isinstance(raw, str) or not 0 < len(raw) <= 255:
        return ""
    name, colon, port = raw.strip().lower().partition(":")
    if colon and not (port.isascii() and port.isdigit()):
        return ""
    return name.removesuffix(".")


def platform_hosts():
    """Hosts the platform itself answers on; a custom host may never be one of them."""
    hosts = {h.lower() for h in settings.ALLOWED_HOSTS if h and not h.startswith(".")}
    for url in (settings.DASHBOARD_URL, settings.SITE_URL, *settings.API_ALLOWED_ORIGINS):
        name = urlsplit(url).hostname
        if name:
            hosts.add(name.lower())
    if settings.HQ_HOST:
        hosts.add(settings.HQ_HOST.lower())
    return hosts


def problem(raw):
    """Why this cannot be a custom host, or "" when it can. Accepts only a bare ASCII host."""
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        return "Host is empty or has surrounding space."
    if not raw.isascii():
        return "Host must be ASCII; internationalised names are not accepted."
    if ":" in raw or "/" in raw or "@" in raw or "?" in raw or "#" in raw:
        return "Host must be a bare host name: no scheme, port, path or credentials."
    host = raw.lower().removesuffix(".")
    if len(host) > HOST_MAX_LENGTH:
        return "Host is too long."
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        return "Host must be a dotted name of letters, digits and hyphens."
    if not _TLD.fullmatch(labels[-1]):
        return "Host must end in a letters-only top-level domain."
    if any(label.startswith("xn--") for label in labels):
        return "Punycode (xn--) names are not accepted."
    parent = settings.SITE_PARENT_HOST
    if parent and (host == parent or host.endswith("." + parent)):
        return "Host is the platform's site parent host or under it."
    if host in platform_hosts():
        return "Host is a platform host."
    return ""


def validated(raw):
    """The stored form of a host, or ValidationError."""
    reason = problem(raw)
    if reason:
        raise ValidationError(reason)
    return raw.lower().removesuffix(".")


_table_exists = False


def _table_ready():
    """True once the SiteCustomHost table exists, so a deploy that runs before sites.0004 cannot
    break store lookups. Only a True answer is cached (per process); while the table is missing it
    is checked again on each call. Database errors are not caught."""
    global _table_exists
    if _table_exists:
        return True
    if SiteCustomHost._meta.db_table in connection.introspection.table_names():
        _table_exists = True
    return _table_exists


def active_row_for_host(host, include_held=False):
    """The active custom-host row a request host names, with its site, or None."""
    name = key(host)
    if not name or not _table_ready():
        return None
    return (
        SiteCustomHost.objects.filter(
            host=name, state=SiteCustomHost.State.ACTIVE,
            site__deleted_at__isnull=True,
            **({} if include_held else {"site__operator_hold": False}),
        )
        .select_related("site")
        .first()
    )


def active_host(site):
    if _table_ready():
        row = site.custom_hosts.filter(state=SiteCustomHost.State.ACTIVE).first()
        if row is not None:
            return row.host
    # A custom domain connected by apps.domains, only while its claim is live (off by default).
    from apps.domains import hosts as domain_hosts

    return domain_hosts.canonical_host(site) or None


def custom_host_if_asked(site, host):
    """The site's active custom host when the looked-up `host` is exactly that host, else "". The
    storefront uses it to tell a custom-domain visit from a platform-address visit."""
    active = active_host(site)
    return active if active and key(host) == active else ""


def public_host(site):
    """The canonical host a buyer reaches this site on: its active custom host, else its
    subdomain host, else ""."""
    custom = active_host(site)
    if custom:
        return custom
    subdomain = site.subdomains.filter(state="active").order_by("pk").first()
    if subdomain is None or not settings.SITE_PARENT_HOST:
        return ""
    return f"{subdomain.name}.{settings.SITE_PARENT_HOST}"


def public_base_url(site):
    host = public_host(site)
    return f"https://{host}" if host else ""
