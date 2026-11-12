"""The catalog reads of the storefront surface (registered on apps/storefront/api.py).

    GET /storefront/v1/catalog/products/?host=...
    GET /storefront/v1/catalog/products/{public_id}/?host=...

Behind SANTE_CATALOG_ENABLED. An unknown host, a site that is not a Santé site, the switch
off, an unknown product and an inactive product all answer the same 404. Active products only,
in creation order. The credential check wraps every route in apps/storefront/api.py.

A product is shown by `services.public_product_out`: never the earnings rate, a writer, the
revision history, `is_active`, a weight, a size or a file key. Prices are whole centavos.
"""
from ninja import Router, Schema

from apps.catalog import services
from apps.common.errors import ApiError
from apps.storefront import services as storefront

router = Router()


class ProductPublic(Schema):
    id: str
    code: str
    name: str
    category: str
    short_description: str
    long_description: str
    image_url: str | None
    srp_centavos: int
    unit_members_price_centavos: int
    revision: int


class ProductListPublic(Schema):
    products: list[ProductPublic]


def _site(host):
    site = storefront.served_sante_site(host) if services.enabled() else None
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site


@router.get("/catalog/products/", response=ProductListPublic)
def catalog_products(request, host: str = ""):
    return {"products": services.storefront_products(_site(host))}


@router.get("/catalog/products/{public_id}/", response=ProductPublic)
def catalog_product(request, public_id: str, host: str = ""):
    return services.storefront_product(_site(host), public_id)
