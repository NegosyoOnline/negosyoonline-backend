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

from ninja import Field, Router, Schema
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
    weight_kg: str = "0.00"
    volume_slot: str | None = None  # left out or null: not set
    shipping_qty: StrictInt | None = None
    length_cm: str | None = None  # all three sizes or none
    width_cm: str | None = None
    height_cm: str | None = None


class ProductUpdateIn(StrictSchema):
    """Only the keys sent are changed. A null for a value is refused with that field's
    message; there is no `code`, which is fixed after creation."""

    revision: StrictInt = Field(ge=1)
    name: str | None = None
    short_description: str | None = None
    long_description: str | None = None
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


@router.patch("/sites/{site_id}/catalog/products/{product_id}/", response=ProductOut)
def product_update(request, site_id: str, product_id: str, payload: ProductUpdateIn):
    product = services.update_product(
        request.account, request.catalog_site, product_id, payload.dict(exclude_unset=True)
    )
    return services.product_out(product)


@router.get("/sites/{site_id}/catalog/products/{product_id}/revisions/", response=RevisionListOut)
def product_revisions(request, site_id: str, product_id: str):
    return services.revisions_for(request.catalog_site, product_id)
