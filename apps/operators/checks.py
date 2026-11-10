"""HQ_HOST must be a bare host name that nothing else answers on.

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


@register()
def check_hq_host(app_configs, **kwargs):
    problem = hq_host_problem()
    return [Error(problem, hint="hq stays off until it is fixed.", id="operators.E001")] if problem else []
