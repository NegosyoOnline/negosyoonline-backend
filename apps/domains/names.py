"""Domain names: normalisation, the Public Suffix List and the refusal list (design 2.3 steps 1, 2).

The Public Suffix List is a data file that is NOT downloaded by this code (approvals D1): it is
read from `settings.DOMAINS_PSL_FILE` when it exists. When it is missing every name is refused
(fail closed) and `apps.domains.checks` warns. Only ASCII names are accepted; punycode (`xn--`)
is refused in the MVP.
"""
import os
import re
from urllib.parse import urlsplit

from django.conf import settings

from apps.sites import custom_hosts

NAME_MAX_LENGTH = 253
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TLD = re.compile(r"^[a-z]{2,63}$")
# Both spellings are real domains held by NegosyoOnline; neither can be claimed (nor anything under).
PLATFORM_NAMES = ("negosyonline.com", "negosyoonline.com")
PSL_MISSING = "The public suffix list is not installed, so no name can be checked."


class NameRefused(Exception):
    """The name cannot be a claim. `str(error)` is a plain reason that never names a provider."""


def normalise(raw):
    """The lowercase ASCII host of a typed name, or NameRefused. Strips one trailing dot."""
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise NameRefused("Enter a domain name.")
    if not raw.isascii():
        raise NameRefused("Only English letters, digits and hyphens are accepted.")
    if any(ch in raw for ch in ":/@?#*_ \\"):
        raise NameRefused("Enter the bare name, with no scheme, port, path or wildcard.")
    host = raw.lower().removesuffix(".")
    if not host or len(host) > NAME_MAX_LENGTH:
        raise NameRefused("That name is too long.")
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        raise NameRefused("Use letters, digits and hyphens in a dotted name.")
    if not _TLD.fullmatch(labels[-1]):
        raise NameRefused("The ending must be letters only.")
    if any(label.startswith("xn--") for label in labels):
        raise NameRefused("International names are not available.")
    if all(label.isdigit() for label in labels):
        raise NameRefused("An address is not a name.")
    return host


# --- Public Suffix List ------------------------------------------------------------------------

_cache = {"key": None, "rules": None}


def _load_rules():
    """(rules, wildcards, exceptions) from the data file, or None when it is missing or empty.
    Cached per (path, mtime)."""
    path = settings.DOMAINS_PSL_FILE
    try:
        stat = os.stat(path)
    except (OSError, TypeError):
        return None
    key = (path, stat.st_mtime_ns)
    if _cache["key"] == key:
        return _cache["rules"]
    rules, wildcards, exceptions = set(), set(), set()
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                entry = line.split()[0] if line.strip() and not line.startswith("//") else ""
                if not entry or not entry.isascii():
                    continue
                entry = entry.lower()
                if entry.startswith("!"):
                    exceptions.add(entry[1:])
                elif entry.startswith("*."):
                    wildcards.add(entry[2:])
                else:
                    rules.add(entry)
    except OSError:
        return None
    parsed = (rules, wildcards, exceptions) if rules or wildcards else None
    _cache.update(key=key, rules=parsed)
    return parsed


def psl_available():
    return _load_rules() is not None


def public_suffix(host):
    """The public suffix of a normalised host, or None when the list is not installed."""
    loaded = _load_rules()
    if loaded is None:
        return None
    rules, wildcards, exceptions = loaded
    labels = host.split(".")
    best = 1  # an unknown ending counts as a one-label suffix (the PSL's default rule "*")
    for i in range(len(labels)):
        candidate = ".".join(labels[i:])
        if candidate in exceptions:
            return ".".join(labels[i + 1:])
        count = len(labels) - i
        if candidate in rules:
            best = max(best, count)
        parent = ".".join(labels[i + 1:])
        if parent and parent in wildcards:
            best = max(best, count)
    return ".".join(labels[-best:])


def is_public_suffix(host):
    suffix = public_suffix(host)
    return suffix is not None and suffix == host


def registrable_domain(host):
    """One label plus the public suffix, or None (a suffix itself, or the list is missing)."""
    suffix = public_suffix(host)
    if suffix is None or host == suffix:
        return None
    labels = host.split(".")
    return ".".join(labels[-(len(suffix.split(".")) + 1):])


# --- Refusal list ------------------------------------------------------------------------------

def _under(host, base):
    return bool(base) and (host == base or host.endswith("." + base))


def platform_hosts():
    """Every host the platform answers on: the allowed hosts, the dashboard and site URLs, the
    HQ host and staff origin, and the parent host."""
    hosts = set(custom_hosts.platform_hosts())
    for url in (getattr(settings, "HQ_STAFF_ORIGIN", ""),):
        name = urlsplit(url or "").hostname
        if name:
            hosts.add(name.lower())
    if settings.SITE_PARENT_HOST:
        hosts.add(settings.SITE_PARENT_HOST)
    return {h for h in hosts if h}


def refusal(raw, *, exclude_claim=None):
    """Why `raw` can never be a claim, or "" when it can. Fails closed with no public suffix list.
    The name must be a registrable domain (the apex): never a public suffix, a subdomain, a
    platform name or host, a protected zone, or a name another claim holds."""
    from apps.domains.models import DomainHostname, HOLDING_STATES  # local: models import names

    try:
        host = normalise(raw)
    except NameRefused as error:
        return str(error)
    if not psl_available():
        return PSL_MISSING
    if is_public_suffix(host):
        return "That is not a name anyone can own."
    if registrable_domain(host) != host:
        return "Choose the main domain, not a part of one."
    if host.startswith("www."):
        return "Choose the main domain, without www."
    if any(_under(host, base) for base in PLATFORM_NAMES):
        return "That name belongs to NegosyoOnline."
    parent = settings.SITE_PARENT_HOST
    if parent:
        parent_registrable = registrable_domain(parent) or parent
        if _under(host, parent_registrable) or _under(parent, host):
            return "That name belongs to NegosyoOnline."
    for base in platform_hosts():
        if _under(host, base) or _under(base, host):
            return "That name belongs to NegosyoOnline."
    for zone in settings.DOMAINS_PLATFORM_ZONES:
        if _under(host, zone) or _under(zone, host):
            return "That name belongs to NegosyoOnline."
    if custom_hosts._table_ready() and custom_hosts.SiteCustomHost.objects.filter(
        host__in=[host, f"www.{host}"]
    ).exists():
        return "That name is already connected to a store."
    held = DomainHostname.objects.filter(hostname__in=[host, f"www.{host}"], holding=True)
    if exclude_claim is not None:
        held = held.exclude(claim=exclude_claim)
    if held.exists():
        return "That name is already connected to a store."
    return ""
