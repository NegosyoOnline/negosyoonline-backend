"""Shared catalog endpoints. Shapes here are the frontend's contract.

Every endpoint is signed-in only and reaches its site through
site_for(account, site_id, template=templates.SANTE), so a missing site, a removed site,
another account's site and a site that is not a Santé site all answer the same 404. While
the catalog is switched off every route answers that 404 too, signed in or not.

These are owner functions, so they follow Billing's rule (plan D11) through the sites API's
own check: once the site's subscription is suspended or ended they answer 403 `billing_only`,
after the 404 check.

Both checks run in the router's sign-in step, which comes before the body is read: a body
sent to a site that is not the caller's is never looked at.

Amounts are whole centavos, as JSON integers. The earnings rate, the weight, the volume slot
and the sizes are fixed-format text. A product is named by its public id; no row number
appears in a route or a response.
"""
from datetime import datetime

from ninja import Field, Query, Router, Schema
from pydantic import ConfigDict, StrictBool, StrictInt

from apps.accounts.api import signed_in
from apps.catalog import services
from apps.common.errors import ApiError
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for


def catalog_site(request):
    """The router's sign-in check. It also finds the caller's Santé site, before any body is
    read, and leaves it on the request."""
    if not services.enabled():
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site_for(account, site_id, template=templates.SANTE)
        request.catalog_site = _owner_site(request, site_id)
    return account


router = Router(tags=["catalog"], auth=catalog_site)


class StrictSchema(Schema):
    """An unknown key is refused, so a body can never name a writer or a product code to
    change."""

    model_config = ConfigDict(extra="forbid")


class ProductCreateIn(StrictSchema):
    code: str
    name: str
    srp_centavos: StrictInt
    earnings_rate: str
    short_description: str = ""
    long_description: str = ""
    features: str = ""  # the four texts: at most 5000 characters each
    specifications: str = ""
    advantages: str = ""
    benefits: str = ""
    category: str = ""  # barley, drinks, essentials, beauty, or blank
    weight_kg: str = "0.00"
    volume_slot: str | None = None  # left out or null: not set
    shipping_qty: StrictInt | None = None
    length_cm: str | None = None  # all three sizes or none
    width_cm: str | None = None
    height_cm: str | None = None


class ProductOrderIn(StrictSchema):
    """Every product of the catalog, active or not, once each, in the order to show them."""

    product_ids: list[str] = Field(max_length=2000)


class ProductUpdateIn(StrictSchema):
    """Only the keys sent are changed. A null for a value is refused with that field's
    message; there is no `code`, which is fixed after creation."""

    revision: StrictInt = Field(ge=1)
    name: str | None = None
    short_description: str | None = None
    long_description: str | None = None
    features: str | None = None
    specifications: str | None = None
    advantages: str | None = None
    benefits: str | None = None
    category: str | None = None
    weight_kg: str | None = None
    volume_slot: str | None = None
    shipping_qty: StrictInt | None = None
    length_cm: str | None = None
    width_cm: str | None = None
    height_cm: str | None = None
    srp_centavos: StrictInt | None = None
    earnings_rate: str | None = None
    is_active: StrictBool = None


class ProductValues(Schema):
    code: str
    name: str
    short_description: str
    long_description: str
    features: str
    specifications: str
    advantages: str
    benefits: str
    category: str
    image_url: str | None  # photo slot 1
    image_urls: list[str | None]  # always 5: slots 1 to 5, null where empty
    weight_kg: str
    srp_centavos: int
    earnings_rate: str
    volume_slot: str | None
    shipping_qty: int | None
    length_cm: str | None
    width_cm: str | None
    height_cm: str | None
    is_active: bool
    revision: int


class ProductOut(ProductValues):
    id: str
    unit_members_price_centavos: int
    updated_at: datetime


class ProductListOut(Schema):
    can_edit: bool
    products: list[ProductOut]


class RevisionOut(ProductValues):
    source: str
    changed_at: datetime


class RevisionListOut(Schema):
    revisions: list[RevisionOut]


@router.get("/sites/{site_id}/catalog/products/", response=ProductListOut)
def product_list(request, site_id: str):
    return services.list_for(request.catalog_site)


@router.post("/sites/{site_id}/catalog/products/", response={201: ProductOut})
def product_create(request, site_id: str, payload: ProductCreateIn):
    product = services.create_product(request.account, request.catalog_site, payload.dict())
    return 201, services.product_out(product)


@router.post("/sites/{site_id}/catalog/products/order/", response=ProductListOut)
def product_order(request, site_id: str, payload: ProductOrderIn):
    """Arrange (owner only): answers the owner list in the new order."""
    return services.reorder_products(request.account, request.catalog_site, payload.product_ids)


@router.patch("/sites/{site_id}/catalog/products/{product_id}/", response=ProductOut)
def product_update(request, site_id: str, product_id: str, payload: ProductUpdateIn):
    product = services.update_product(
        request.account, request.catalog_site, product_id, payload.dict(exclude_unset=True)
    )
    return services.product_out(product)


@router.post("/sites/{site_id}/catalog/products/{product_id}/image/", response=ProductOut)
def product_image_set(request, site_id: str, product_id: str, slot: int = Query(1)):
    """A form post (multipart): `file` is a JPEG, PNG or WebP of at most 2 MB, and `revision`
    is the revision the caller loaded. `?slot=1..5` names the photo slot (default 1)."""
    revision = request.POST.get("revision", "")
    if not revision.isdigit() or not 1 <= int(revision) <= 2**31:
        raise ApiError(
            "invalid_request", services.INVALID_MSG,
            fields={"revision": ["Send the revision you loaded."]},
        )
    product = services.set_image(
        request.account, request.catalog_site, product_id, request.FILES.get("file"),
        int(revision), slot,
    )
    return services.product_out(product)


@router.delete("/sites/{site_id}/catalog/products/{product_id}/image/", response=ProductOut)
def product_image_clear(
    request, site_id: str, product_id: str, revision: int = Query(..., ge=1),
    slot: int = Query(1),
):
    """Clears only the photo in `?slot=1..5` (default 1)."""
    product = services.clear_image(
        request.account, request.catalog_site, product_id, revision, slot
    )
    return services.product_out(product)


class ShippingPreviewIn(StrictSchema):
    weight_kg: str
    length_cm: str | None = None
    width_cm: str | None = None
    height_cm: str | None = None
    shipping_qty: StrictInt | None = None


class ShippingPreviewOut(Schema):
    ncr_centavos: int | None
    mindanao_centavos: int | None
    billable_kg: str | None
    reason: str


@router.post("/sites/{site_id}/catalog/shipping-preview/", response=ShippingPreviewOut)
def shipping_preview(request, site_id: str, payload: ShippingPreviewIn):
    """Owner only, read-only: the fee for one unit to NCR and to Mindanao. Nothing is stored."""
    return services.shipping_preview(request.catalog_site, payload.dict())


@router.get("/sites/{site_id}/catalog/products/{product_id}/revisions/", response=RevisionListOut)
def product_revisions(request, site_id: str, product_id: str):
    return services.revisions_for(request.catalog_site, product_id)
