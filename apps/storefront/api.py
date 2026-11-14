"""The storefront surface: the calls the storefronts server makes to draw a site's pages.

    GET /storefront/v1/site/?host=anas-store.negosyoonline.com
        Authorization: Bearer <STOREFRONT_SERVER_SECRET>

    POST /storefront/v1/contact/
        Authorization: Bearer <STOREFRONT_SERVER_SECRET>
        {"host": "...", "visitor_ip": "...", "name": "...", "email": "...", "subject": "...",
         "message": "...", "website": ""}

The second emails a visitor's "Send a Message" form to the store's own contact email and
answers {"status": "sent"}. `website` is the form's hidden field: filled, the answer is the
same and nothing is sent.

It is outside /v1/, so it is not in the published API and the browser Origin rules do not
apply to it. Every call carries the server credential (apps/storefront/auth.py) and the host
the visitor asked for. The host is untrusted and is only ever a lookup value. A host whose
site is not served right now answers 404, whatever the reason.

`urlpatterns` is the only way in: each route below, and every unknown path, is wrapped in the
credential check, so a route added later cannot be reached without it.
"""
from django.http import HttpResponse, JsonResponse
from django.urls import URLPattern, re_path
from ninja import NinjaAPI, Schema

from apps.catalog.storefront_api import router as catalog_storefront_router
from apps.common.errors import ApiError, error_body, register_error_handlers
from apps.members.api import router as members_router
from apps.members.api_content import router as member_content_router
from apps.meta_ads.api_storefront import router as meta_storefront_router
from apps.pack.api import router as pack_checkout_router
from apps.pack.api_portal import router as portal_router
from apps.sante import pack_api
from apps.shop.api import router as shop_router
from apps.sites import seal
from apps.storefront import services
from apps.storefront.auth import server_only

api = NinjaAPI(
    title="NegosyoOnline storefront surface",
    urls_namespace="storefront",
    openapi_url=None,
    docs_url=None,
)
register_error_handlers(api)
api.add_router("/member/", members_router)
api.add_router("/member/", member_content_router)  # notices and the bell (apps/members/api_content.py)
api.add_router("/portal/", portal_router)  # the buyer portal for a site that is off (apps/pack/api_portal.py)


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
    meta_pixel_id: str = ""


def _site(host):
    site = services.served_sante_site(host)
    if site is None:
        raise ApiError("not_found", "Not found.", status=404, event="site_not_found", reason="missing")
    return site


@api.get("/site/", response=SiteOut)
def site(request, host: str = ""):
    return services.site_chrome(_site(host))


@api.get("/seal/")
def seal_image(request, host: str = ""):
    """The store's BIR registration badge, as the owner uploaded it. 404 when none is, as for an unknown host."""
    found = seal.image_of(_site(host))
    if found is None:
        raise ApiError("not_found", "Not found.", status=404)
    response = HttpResponse(found[0], content_type=found[1])
    response["Cache-Control"] = "private, no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = 'inline; filename="business-seal"'
    return response


class ContactIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    name: str = ""
    email: str = ""
    subject: str = ""
    message: str = ""
    website: str = ""


class ContactOut(Schema):
    status: str


@api.post("/contact/", response=ContactOut)
def contact(request, body: ContactIn):
    services.send_contact_message(_site(body.host), body.dict(), body.visitor_ip)
    return {"status": "sent"}


api.add_router("", pack_checkout_router)  # the Membership Pack checkout (apps/pack/api.py)
api.add_router("", pack_api.router)  # the Membership Pack reads (apps/sante/pack_api.py)
api.add_router("", catalog_storefront_router)  # the catalog reads (apps/catalog/storefront_api.py)
api.add_router("", shop_router)  # the Santé shop (apps/shop/api.py)
api.add_router("", meta_storefront_router)  # the Meta Conversions API events (apps/meta_ads)


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
