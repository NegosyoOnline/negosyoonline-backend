"""The one endpoint a storefront calls without a sign-in: which site answers on this host.

    GET /v1/public/sites/by-host/?host=anas-store.negosyoonline.com

The host is untrusted. Only its first label is ever used, as a lookup value. A site is served
only when its name is active, it is not removed, it is not on operator hold and its
subscription says it is served at this moment. Everything else answers the same 404, whatever
the reason: unknown fails closed.
"""
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from ninja import Router, Schema

from apps.billing import services as billing
from apps.common.errors import error_body
from apps.sites import subdomains
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


def site_label(host):
    """The subdomain a host names, or None. The host must be one label under the parent host."""
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
    if not subdomains.is_valid_format(label) or subdomains.is_reserved(label):
        return None
    return label


def site_for_host(host):
    """The site whose active name this host is, or None: not removed, not on operator hold.
    Nothing about its billing position: `served` adds that."""
    label = site_label(host)
    if label is None:
        return None
    row = (
        Subdomain.objects.filter(
            name=label, state=Subdomain.State.ACTIVE,
            site__deleted_at__isnull=True, site__operator_hold=False,
        )
        .select_related("site")
        .first()
    )
    return None if row is None else row.site


def served(host):
    """(site, its billing position) when the host's site is served right now, else None."""
    site = site_for_host(host)
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
    }
