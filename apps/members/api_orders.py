"""The member's Orders log (ClickUp z8uq9meze0, M5'), added to the storefront surface next to the
member routes (apps/storefront/api.py mounts this router under /member/):

    POST /storefront/v1/member/order-log/               {host, token, q?, date_from?, date_to?, status?, page?}
                                                      -> {orders, page, pages, count, tiles, header}
    POST /storefront/v1/member/order-log/create/        {host, token, order_number, ordered_on, customer_first_name,
                                                      city, payment, total, status} -> 201 {order}
    POST /storefront/v1/member/order-log/export/        {host, token, q?, date_from?, date_to?, status?} -> text/csv
    POST /storefront/v1/member/order-log/{id}/          {host, token} -> {order}
    POST /storefront/v1/member/order-log/{id}/update/   the create fields -> {order}
    POST /storefront/v1/member/order-log/{id}/delete/   {host, token} -> {deleted: true}

Same gates as the other member routes: the storefronts server's credential, MEMBERS_ENABLED, a
served Santé host (404 not_found), and a live member session of that host's site (401
member_session_invalid). Rows are read and written under the session's own site and member only;
another member's id or another site's id answers the same 404 as a missing one. Schemas refuse
unknown keys. Wrong values answer 400 `invalid_order` with `fields`.
"""
from datetime import date, datetime
from typing import Any

from ninja import Router, Schema
from pydantic import ConfigDict

from . import order_log
from .api import _session, _site

router = Router()


class SessionIn(Schema):
    model_config = ConfigDict(extra="forbid")
    host: str = ""
    token: str = ""
    visitor_ip: str = ""  # every dashboard call of the storefronts server carries it; not used here


class FiltersIn(SessionIn):
    q: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    status: str | None = None


class ListIn(FiltersIn):
    page: int = 1


class OrderIn(SessionIn):
    # Every field is required, but a missing or wrong one is checked by the service so the answer
    # is the same 400 invalid_order with fields, whatever is wrong.
    order_number: str | None = None
    ordered_on: str | None = None
    customer_first_name: str | None = None
    city: str | None = None
    payment: str | None = None
    total: Any = None  # the service checks it: a JSON number or a string, never a bool or a list
    status: str | None = None


class OrderOut(Schema):
    id: str
    order_number: str
    ordered_on: date
    customer_first_name: str
    city: str
    payment: str
    total: str
    status: str
    created_at: datetime
    updated_at: datetime


class OneOut(Schema):
    order: OrderOut


class TilesOut(Schema):
    for_approval: int
    for_booking: int
    for_pickup: int
    shipping: int
    delivered: int
    closed: int


class HeaderOut(Schema):
    orders_this_month: int
    sales_this_month: str
    delivered_this_month: int
    needs_action: int


class ListOut(Schema):
    orders: list[OrderOut]
    page: int
    pages: int
    count: int
    tiles: TilesOut
    header: HeaderOut


class DeletedOut(Schema):
    deleted: bool


def _filters(body):
    return {"q": body.q, "date_from": body.date_from, "date_to": body.date_to, "status": body.status}


def _fields(body):
    return body.dict(exclude={"host", "token", "visitor_ip"})


@router.post("/order-log/", response=ListOut)
def orders_list(request, body: ListIn):
    session = _session(_site(body.host), body.token)
    return order_log.listing(session, page=body.page, **_filters(body))


@router.post("/order-log/create/", response={201: OneOut})
def orders_create(request, body: OrderIn):
    session = _session(_site(body.host), body.token)
    return 201, {"order": order_log.order_out(order_log.create(session, _fields(body)))}


@router.post("/order-log/export/")
def orders_export(request, body: FiltersIn):
    session = _session(_site(body.host), body.token)
    return order_log.export_csv(session, **_filters(body))


@router.post("/order-log/{order_id}/", response=OneOut)
def orders_get(request, order_id: str, body: SessionIn):
    session = _session(_site(body.host), body.token)
    return {"order": order_log.order_out(order_log.get(session, order_id))}


@router.post("/order-log/{order_id}/update/", response=OneOut)
def orders_update(request, order_id: str, body: OrderIn):
    session = _session(_site(body.host), body.token)
    return {"order": order_log.order_out(order_log.update(session, order_id, _fields(body)))}


@router.post("/order-log/{order_id}/delete/", response=DeletedOut)
def orders_delete(request, order_id: str, body: SessionIn):
    session = _session(_site(body.host), body.token)
    order_log.delete(session, order_id)
    return {"deleted": True}
