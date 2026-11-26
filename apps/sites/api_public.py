"""The one endpoint a storefront calls without a sign-in: which site answers on this host.

    GET /v1/public/sites/by-host/?host=anas-store.negosyoonline.com

The host is untrusted. It is matched exactly against an explicit custom host, else only its first
label under the parent host is used, as a lookup value. A site is served
only when its name is active, it is not removed, it is not on operator hold and its
subscription says it is served at this moment. Everything else answers the same 404, whatever
the reason: unknown fails closed.
"""
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from ninja import Router, Schema

from apps.billing import services as billing
from apps.common.errors import error_body
from apps.sites import custom_hosts, subdomains
from apps.sites.models import Subdomain

router = Router(tags=["public"])

HOST_MAX_LENGTH = 255
SERVED_MAX_AGE = 60  # seconds a storefront may keep a 200
NOT_FOUND_MAX_AGE = 30


class PublicSiteOut(Schema):
    id: str
    template: str
    display_name: str
    accent_color: str
    facebook_url: str
    instagram_url: str
    tiktok_url: str
    custom_host: str = ""


def site_label(host):
    """The subdomain a host names, or None. The host must be one label under the parent host.

    A reserved label (a platform or brand name) is returned only when an ACTIVE `Subdomain` row
    with that name sits on a site that is not deleted (LABEL-R2, Coach Ed, 2026-11-26). Sign-up,
    availability and rename refuse reserved names, so such a row is always an operator assignment
    (audit action `site_label_assigned`). With no such row it is None, as before. The extra query
    runs only for a reserved label."""
    parent = settings.SITE_PARENT_HOST
    if not parent or not isinstance(host, str) or not 0 < len(host) <= HOST_MAX_LENGTH:
        return None
    name, colon, port = host.lower().partition(":")
    if colon and not (port.isascii() and port.isdigit()):
        return None
    name = name.removesuffix(".")
    suffix = f".{parent}"
    if not name.endswith(suffix):
        return None
    label = name[: -len(suffix)]
    if not subdomains.is_valid_format(label):
        return None
    if subdomains.is_reserved(label) and not Subdomain.objects.filter(
        name=label, state=Subdomain.State.ACTIVE, site__deleted_at__isnull=True
    ).exists():
        return None
    return label


def site_for_host(host, include_held=False):
    """The site whose active name this host is, or None: not removed, not on operator hold
    (`include_held=True` is for the buyer portal only, which stays open for a held store).
    Nothing about its billing position: `served` adds that. An explicit custom host is matched
    first, exactly; a host under SITE_PARENT_HOST can never be one, so the subdomain path below is
    unchanged."""
    custom = custom_hosts.active_row_for_host(host, include_held=include_held)
    if custom is not None:
        return custom.site
    label = site_label(host)
    if label is None:
        # The second branch, only for a host that is not under the parent host: a custom domain
        # connected by apps.domains (a `live` or `attention` claim; off unless DOMAINS_ENABLED).
        from apps.domains import hosts as domain_hosts

        return domain_hosts.site_for_hostname(host, include_held=include_held)
    row = (
        Subdomain.objects.filter(
            name=label, state=Subdomain.State.ACTIVE,
            site__deleted_at__isnull=True,
            **({} if include_held else {"site__operator_hold": False}),
        )
        .select_related("site")
        .first()
    )
    return None if row is None else row.site


def served(host, include_held=False):
    """(site, its billing position) when the host's site is served right now, else None.
    `include_held=True` is for the member routes only: a store HQ has suspended by hand keeps
    its members' sign-in and their read access to their own orders (H3, decision C)."""
    site = site_for_host(host, include_held=include_held)
    if site is None:
        return None
    position = billing.position(site)
    return (site, position) if position.storefront_served else None


def _max_age(position):
    """Never past the moment the site would next go off."""
    if position.next_change_at is None or position.at is None:
        return SERVED_MAX_AGE
    left = int((position.next_change_at - position.at).total_seconds())
    return max(0, min(SERVED_MAX_AGE, left))


@router.get("/public/sites/by-host/", response=PublicSiteOut)
def site_by_host(request, response: HttpResponse, host: str = ""):
    found = served(host)
    if found is None:
        refused = JsonResponse(error_body("not_found", "Not found."), status=404)
        refused["Cache-Control"] = f"public, max-age={NOT_FOUND_MAX_AGE}"
        return refused
    site, position = found
    response["Cache-Control"] = f"public, max-age={_max_age(position)}"
    return {
        "id": site.public_id,
        "template": site.template,
        "display_name": site.display_name,
        "accent_color": site.accent_color,
        "facebook_url": site.facebook_url,
        "instagram_url": site.instagram_url,
        "tiktok_url": site.tiktok_url,
        "custom_host": custom_hosts.custom_host_if_asked(site, host),
    }
