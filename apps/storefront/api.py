"""The storefront surface: the calls the storefronts server makes to draw a site's pages.

    GET /storefront/v1/site/?host=anas-store.negosyoonline.com
        Authorization: Bearer <STOREFRONT_SERVER_SECRET>

It is outside /v1/, so it is not in the published API and the browser Origin rules do not
apply to it. Every call carries the server credential (apps/storefront/auth.py) and the host
the visitor asked for. The host is untrusted and is only ever a lookup value. A host whose
site is not served right now answers 404, whatever the reason.

`urlpatterns` is the only way in: each route below, and every unknown path, is wrapped in the
credential check, so a route added later cannot be reached without it.
"""
from django.http import JsonResponse
from django.urls import URLPattern, re_path
from ninja import NinjaAPI, Schema

from apps.common.errors import ApiError, error_body, register_error_handlers
from apps.storefront import services
from apps.storefront.auth import server_only

api = NinjaAPI(
    title="NegosyoOnline storefront surface",
    urls_namespace="storefront",
    openapi_url=None,
    docs_url=None,
)
register_error_handlers(api)


class BusinessOut(Schema):
    registered_name: str
    tin: str
    dti_business_number: str
    contact_email: str
    contact_mobile: str
    has_seal: bool


class DistributorOut(Schema):
    name: str
    mobile: str
    has_photo: bool


class PolicyLinkOut(Schema):
    label: str
    path: str


class SiteOut(Schema):
    id: str
    template: str
    display_name: str
    accent_color: str
    facebook_url: str
    instagram_url: str
    tiktok_url: str
    business: BusinessOut
    distributor: DistributorOut
    policy_links: list[PolicyLinkOut]
    free_shipping_banner: bool


def _site(host):
    site = services.served_sante_site(host)
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site


@api.get("/site/", response=SiteOut)
def site(request, host: str = ""):
    return services.site_chrome(_site(host))


def _no_such_endpoint(request):
    return JsonResponse(error_body("not_found", "No such endpoint."), status=404)


def _closed(patterns):
    closed = []
    for pattern in patterns:
        if not isinstance(pattern, URLPattern):  # an include would slip past the check
            raise TypeError("The storefront surface takes plain routes only.")
        if pattern.name == "api-root":  # ninja's own page; the surface's 404 answers there
            continue
        closed.append(
            URLPattern(
                pattern.pattern, server_only(pattern.callback), pattern.default_args,
                pattern.name,
            )
        )
    return closed


_routes, _app_name, namespace = api.urls
urlpatterns = (
    [*_closed(_routes), re_path(r"", server_only(_no_such_endpoint))],
    _app_name,
)
