"""HQ_HOST must be a bare host name that nothing else answers on, and HQ_STAFF_ORIGIN the one origin that may call it.

`manage.py check` reports a bad value as an error. A running service treats a bad value as
unset, so hq is off rather than half-routed.
"""
import logging
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.core.checks import Error, register

logger = logging.getLogger(__name__)

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})+$")
_ORIGIN = re.compile(rf"^(https?)://({_LABEL}(?:\.{_LABEL})+)(?::(\d{{1,5}}))?$")


def hq_host_problem():
    """Why HQ_HOST cannot be used, or "" when it is empty or fine."""
    host = settings.HQ_HOST
    if not host:
        return ""
    if not _HOST.fullmatch(host):
        return "HQ_HOST must be a bare lower-case host name: no scheme, port or path."
    taken = {
        urlsplit(url).hostname
        for url in (settings.DASHBOARD_URL, settings.SITE_URL, *settings.API_ALLOWED_ORIGINS)
    }
    if host in taken:
        return "HQ_HOST is also the host of DASHBOARD_URL, SITE_URL or an API_ALLOWED_ORIGINS entry."
    parent = settings.SITE_PARENT_HOST
    if parent and host == parent:
        return "HQ_HOST is the same as SITE_PARENT_HOST."
    if parent and host.endswith("." + parent):
        from apps.sites.subdomains import is_reserved

        label = host[: -len(parent) - 1]
        if "." not in label and not is_reserved(label):
            return "HQ_HOST sits under SITE_PARENT_HOST with a name a subscriber site could take."
    return ""


def active_hq_host():
    """The host hq answers on, or "" when hq is off."""
    host = settings.HQ_HOST
    if not host:
        return ""
    problem = hq_host_problem()
    if problem:
        if cache.add(f"hq-host-refused:{host}", 1, 3600):
            logger.error("hq-host-refused: %s hq is off.", problem)
        return ""
    return host


def staff_origin_problem():
    """Why HQ_STAFF_ORIGIN cannot be used, or "" when it is empty or fine."""
    origin = settings.HQ_STAFF_ORIGIN
    if not origin:
        return ""
    hq_host = settings.HQ_HOST
    if not hq_host or hq_host_problem():
        return "HQ_STAFF_ORIGIN is set but HQ_HOST is empty or refused."
    match = _ORIGIN.fullmatch(origin)
    if match is None or len(match.group(2)) > 253:
        return (
            "HQ_STAFF_ORIGIN must be one origin: https://, a lower-case host name, and nothing "
            "after it."
        )
    scheme, host, port = match.groups()
    if not settings.DEBUG and (scheme != "https" or port):
        return "HQ_STAFF_ORIGIN must be https with no port."
    if host == hq_host:
        return "HQ_STAFF_ORIGIN is the hq host itself."
    taken = {
        urlsplit(url).hostname
        for url in (settings.DASHBOARD_URL, settings.SITE_URL, *settings.API_ALLOWED_ORIGINS)
    }
    if host in taken:
        return (
            "HQ_STAFF_ORIGIN is also the host of DASHBOARD_URL, SITE_URL or an "
            "API_ALLOWED_ORIGINS entry."
        )
    if host.partition(".")[2] != hq_host.partition(".")[2]:
        return (
            "HQ_STAFF_ORIGIN and HQ_HOST must be names directly under the same parent, so that "
            "the hq cookie is sent with SameSite=Strict."
        )
    parent = settings.SITE_PARENT_HOST
    if parent and host.endswith("." + parent):
        from apps.sites.subdomains import is_reserved

        label = host[: -len(parent) - 1]
        if "." not in label and not is_reserved(label):
            return "HQ_STAFF_ORIGIN sits under SITE_PARENT_HOST with a name a subscriber site could take."
    return ""


def active_staff_origin():
    """The one origin that may call the staff routes, or "" when they are off."""
    origin = settings.HQ_STAFF_ORIGIN
    if not origin:
        return ""
    problem = staff_origin_problem()
    if problem:
        if cache.add(f"hq-staff-origin-refused:{origin}", 1, 3600):
            logger.error("hq-staff-origin-refused: %s The staff routes are off.", problem)
        return ""
    return origin


@register()
def check_hq_host(app_configs, **kwargs):
    problem = hq_host_problem()
    return [Error(problem, hint="hq stays off until it is fixed.", id="operators.E001")] if problem else []


@register()
def check_staff_origin(app_configs, **kwargs):
    problem = staff_origin_problem()
    if not problem:
        return []
    return [Error(problem, hint="The staff routes stay off until it is fixed.", id="operators.E002")]
