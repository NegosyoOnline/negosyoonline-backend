"""The shop routes of the storefront surface (registered by one line in apps/storefront/api.py):

    POST /storefront/v1/shop/quote/           {host, visitor_ip, lines: [{id, qty}], member_token?,
        ship_mode?, address?} -> the priced cart (lines, subtotal, shipping, total, fingerprint)
    GET  /storefront/v1/shop/payment-accounts/?host=...    -> the store's active accounts
    GET  /storefront/v1/shop/payment-accounts/{id}/qr/?host=...
    POST /storefront/v1/shop/orders/          multipart: host, visitor_ip, lines (JSON text), member_token?,
        ship_mode?, address (JSON text)?, receiver_name?, receiver_mobile?, contact_email?, fingerprint,
        submit_key, account_id, reference_number, amount_sent, proof (the file)
        -> {order_id, order_number, view_token, replay}: `view_token` is answered once ("" on a replay)
    POST /storefront/v1/shop/orders/{id}/view/   {host, view_token} -> the placing buyer's order
    POST /storefront/v1/member/shop-orders/      {host, token} -> the member's own orders

Every call carries the storefronts server's credential (the surface checks it). Tokens travel as
data in a body, never in a URL; Django sets no cookie. While SHOP_ENABLED is off, and for an
unknown, foreign or non-Santé host, every route answers the same 404 before any shop table is
read. The browser never sends a price: the server decides them (apps/shop/quote.py)."""
import hmac
import ipaddress
import json

from django.conf import settings
from django.http import HttpResponse
from ninja import File, Form, Router, Schema
from ninja.files import UploadedFile

from apps.common import throttle
from apps.common.errors import ApiError
from apps.members import sessions
from apps.payments import services as payments
from apps.sante import pack as sante_pack
from apps.shop import constants as C, placement, quote
from apps.shop.api_owner import fulfilment_status
from apps.shop.models import ShopOrder
from apps.storefront.services import served_sante_site

router = Router()

TRACKER = ("Order Placed", "Order Confirmed", "Shipped", "Delivered")
STATUS_LABELS = {
    "placed": "Payment under review", "confirmed": "Payment confirmed", "shipped": "Shipped",
    "delivered": "Delivered", "cancelled": "This order was cancelled.",
}


def _site(host):
    site = served_sante_site(host) if settings.SHOP_ENABLED else None
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site


def _ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return ""


def _hit(scope, site, ip):
    throttle.hit(scope[0], f"{site.pk}:{ip or 'unknown'}", limit=scope[1], window=scope[2])


class LineIn(Schema):
    id: str = ""
    qty: int = 0


class QuoteIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    lines: list[LineIn] = []
    member_token: str = ""
    ship_mode: str = ""
    address: dict | None = None


@router.post("/shop/quote/")
def shop_quote(request, body: QuoteIn):
    site = _site(body.host)
    _hit(C.QUOTE_IP_LIMIT, site, _ip(body.visitor_ip))
    return quote.quote(
        site, [{"id": l.id, "qty": l.qty} for l in body.lines], member_token=body.member_token,
        ship_mode=body.ship_mode, address=body.address,
    )


def _account_out(row):
    return {
        "id": row.public_id, "kind": row.kind, "account_type": row.account_type,
        "provider_name": row.provider_name, "account_name": row.account_name,
        "account_number": row.account_number, "account_number_last4": row.account_number[-4:],
        "has_qr": bool(row.qr_file_key),
    }


@router.get("/shop/payment-accounts/")
def shop_payment_accounts(request, host: str = ""):
    site = _site(host)
    if not payments.enabled() or not payments.any_method(site):
        return {"available": False, "accounts": []}
    return {"available": True, "accounts": [_account_out(a) for a in payments.active_accounts(site)]}


@router.get("/shop/payment-accounts/{account_id}/qr/")
def shop_payment_account_qr(request, account_id: str, host: str = ""):
    found = sante_pack.qr_of(_site(host), account_id)
    if found is None:
        raise ApiError("not_found", "Not found.", status=404)
    response = HttpResponse(found[0], content_type=found[1])
    response["Cache-Control"] = "private, no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = 'inline; filename="payment-qr"'
    return response


class PlaceIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    lines: str = ""  # JSON text: [{"id": "...", "qty": 1}]
    member_token: str = ""
    ship_mode: str = ""
    address: str = ""  # JSON text of the address object
    receiver_name: str = ""
    receiver_mobile: str = ""
    contact_email: str = ""
    fingerprint: str = ""
    submit_key: str = ""
    account_id: str = ""
    reference_number: str = ""
    amount_sent: str = ""


def _json(text, kind, default):
    if not (text or "").strip():
        return default
    try:
        value = json.loads(text)
    except ValueError:
        raise ApiError("invalid_request", "Some values are missing or invalid.", status=422) from None
    if not isinstance(value, kind):
        raise ApiError("invalid_request", "Some values are missing or invalid.", status=422)
    return value


class PlaceOut(Schema):
    order_id: str
    order_number: str
    view_token: str
    replay: bool


@router.post("/shop/orders/", response=PlaceOut)
def shop_place(request, body: Form[PlaceIn], proof: File[UploadedFile] = None):
    site, ip = _site(body.host), _ip(body.visitor_ip)
    _hit(C.PLACE_IP_LIMIT, site, ip)
    placed = placement.place(
        site, lines=_json(body.lines, list, []), member_token=body.member_token,
        ship_mode=body.ship_mode, address=_json(body.address, dict, None),
        receiver={
            "receiver_name": body.receiver_name, "receiver_mobile": body.receiver_mobile,
            "contact_email": body.contact_email,
        },
        fingerprint=body.fingerprint, submit_key=body.submit_key,
        data={
            "account_id": body.account_id, "reference_number": body.reference_number,
            "amount_sent": body.amount_sent,
        },
        upload=proof, ip=ip,
    )
    return {
        "order_id": placed.order.public_id, "order_number": placed.order.order_number,
        "view_token": placed.view_token, "replay": placed.replay,
    }


def _buyer_view(order):
    """What the buyer sees of an order: the tracker, the lines and the money. Never the proof,
    the fingerprint, the token hash or the Subscriber's note."""
    status = fulfilment_status(order)
    return {
        "id": order.public_id, "order_number": order.order_number, "status": status,
        "status_label": STATUS_LABELS[status],
        "tracker": list(TRACKER), "cancelled": status == "cancelled",
        "created_at": order.created_at.isoformat(), "ship_mode": order.ship_mode,
        "receiver_name": order.receiver_name, "delivery_address": order.delivery_address,
        "subtotal_centavos": order.subtotal_centavos,
        "shipping_charged_centavos": order.shipping_charged_centavos,
        "total_centavos": order.total_centavos, "currency": order.currency,
        "courier": order.courier, "tracking_no": order.tracking_no,
        "shipped_on": order.shipped_on.isoformat() if order.shipped_on else None,
        "delivered_on": order.delivered_on.isoformat() if order.delivered_on else None,
        "lines": [
            {
                "code": l.code, "name": l.name, "quantity": l.quantity,
                "line_charged_centavos": l.line_charged_centavos,
            }
            for l in order.lines.all()
        ],
    }


class ViewIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    view_token: str = ""


@router.post("/shop/orders/{order_id}/view/")
def shop_order_view(request, order_id: str, body: ViewIn):
    """The placing buyer's order, for the done page: the token must match the hash on the order.
    A wrong id, a wrong token and another site's order all answer the same 404."""
    site = _site(body.host)
    _hit(C.VIEW_IP_LIMIT, site, _ip(body.visitor_ip))
    order = ShopOrder.objects.prefetch_related("lines").filter(site=site, public_id=order_id).first()
    given = placement.token_hash(body.view_token) if (body.view_token or "").strip() else ""
    if order is None or not given or not hmac.compare_digest(given, order.view_token_hash):
        raise ApiError("not_found", "Not found.", status=404)
    return _buyer_view(order)


class MemberIn(Schema):
    host: str = ""
    token: str = ""


@router.post("/member/shop-orders/")
def member_shop_orders(request, body: MemberIn):
    """My Orders: the member's own shop orders, newest first. Another member's orders never appear."""
    site = _site(body.host)
    session = sessions.find_session(site, body.token) if settings.MEMBERS_ENABLED else None
    if session is None:
        raise ApiError("member_session_invalid", C.MSG_SESSION, status=401)
    throttle.hit(
        C.MEMBER_LIST_LIMIT[0], session.member_id, limit=C.MEMBER_LIST_LIMIT[1], window=C.MEMBER_LIST_LIMIT[2]
    )
    orders = (
        ShopOrder.objects.prefetch_related("lines").filter(site=site, member=session.member)
        .order_by("-created_at", "-pk")[:C.OWNER_LIST_MAX]
    )
    return {"cards": [_buyer_view(o) for o in orders]}
