"""The order-time snapshot. A later slice calls take() inside the transaction that places
an order and copies every value into its own order line, keeping the product id and the
revision as plain references. Earnings, refunds and reports then read the order line, never
the catalog. A cart quote is not a snapshot.

The caller passes a site it already reached through sites.services.site_for and checked
against Billing; take() checks neither.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db import connection

from apps.catalog import pricing, services
from apps.catalog.models import CatalogProduct


class ProductNotFound(Exception):
    """No such product in the catalog this site reads."""


class ProductUnavailable(Exception):
    """The product is in the site's catalog but is deactivated."""


@dataclass(frozen=True)
class LineSnapshot:
    product_id: str  # the product's public id
    code: str
    name: str
    revision: int
    srp_centavos: int
    earnings_rate: Decimal
    quantity: int
    line_srp_total_centavos: int
    line_earnings_centavos: int
    line_members_total_centavos: int


def take(site, product_id, quantity):
    """The values of `quantity` of this product as they are now, under the product's row lock."""
    if not connection.in_atomic_block:
        raise RuntimeError("snapshot.take must run inside the transaction that places the order.")
    key = services.catalog_key_for(site)
    if not key:
        raise ProductNotFound(product_id)
    product = (
        CatalogProduct.objects.select_for_update(of=("self",))  # the product row only
        .filter(public_id=product_id, catalog__template_key=key)
        .first()
    )
    if product is None:
        raise ProductNotFound(product_id)
    if not product.is_active:
        raise ProductUnavailable(product_id)
    totals = pricing.line(product.srp_centavos, product.earnings_rate, quantity)
    return LineSnapshot(
        product_id=product.public_id, code=product.code, name=product.name,
        revision=product.revision, srp_centavos=product.srp_centavos,
        earnings_rate=product.earnings_rate, quantity=quantity,
        line_srp_total_centavos=totals.line_srp_total_centavos,
        line_earnings_centavos=totals.line_earnings_centavos,
        line_members_total_centavos=totals.line_members_total_centavos,
    )
