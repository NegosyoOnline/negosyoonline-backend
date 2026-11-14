"""The buyer's and the storefront's product-review routes of the storefront surface (registered by
one line in apps/storefront/api.py):

    POST  /storefront/v1/shop/orders/{id}/lines/{position}/review/
        {host, visitor_ip, view_token | member_token, rating, body?, display_name?} -> the review
    PATCH the same path, with only the values to change -> the review (one change, within 7 days)
    GET   /storefront/v1/shop/products/{product_id}/reviews/?host=...&page=1
        -> {rating_average, rating_count, page, page_size, has_more, reviews: [...]}

Same surface rules as the shop (apps/shop/api.py): the server credential, tokens as data in a body
and never in a URL, the one 404 for an unknown, foreign or non-Santé host, a wrong order, a wrong
token and another site's order. The rules are in apps/shop/product_reviews.py."""
from typing import Any

from ninja import Router, Schema

from apps.catalog.models import CatalogProduct
from apps.catalog.services import products_for_site
from apps.common.errors import ApiError
from apps.shop import constants as C, product_reviews as reviews
from apps.shop.api import _hit, _ip, _site

router = Router()

BODY_KEYS = ("rating", "body", "display_name")


class ReviewIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    view_token: str = ""
    member_token: str = ""
    rating: Any = None
    body: Any = None
    display_name: Any = None


def _values(payload):
    given = payload.model_dump(exclude_unset=True)
    return {name: given[name] for name in BODY_KEYS if name in given}


@router.post("/shop/orders/{order_id}/lines/{position}/review/")
def review_create(request, order_id: str, position: int, payload: ReviewIn):
    site = _site(payload.host)
    _hit(C.REVIEW_IP_LIMIT, site, _ip(payload.visitor_ip))
    review = reviews.create(
        site, order_id, position, view_token=payload.view_token, member_token=payload.member_token,
        data=_values(payload),
    )
    return reviews.buyer_out(review)


@router.patch("/shop/orders/{order_id}/lines/{position}/review/")
def review_edit(request, order_id: str, position: int, payload: ReviewIn):
    site = _site(payload.host)
    _hit(C.REVIEW_IP_LIMIT, site, _ip(payload.visitor_ip))
    review = reviews.edit(
        site, order_id, position, view_token=payload.view_token, member_token=payload.member_token,
        data=_values(payload),
    )
    return reviews.buyer_out(review)


@router.get("/shop/products/{product_id}/reviews/")
def product_reviews(request, product_id: str, host: str = "", page: int = 1):
    site = _site(host)
    if not products_for_site(site).filter(public_id=product_id).exists():
        raise ApiError("not_found", "Not found.", status=404)
    return reviews.storefront_page(site, product_id, page)
