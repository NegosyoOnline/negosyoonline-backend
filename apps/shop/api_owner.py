"""The Subscriber's product order review, under /v1/sites/{site_id}/shop/orders/:

    GET  .../shop/orders/?filter=to_review|to_ship|shipped|cancelled|all  -> {orders: [row]} newest 200
    GET  .../shop/orders/{order_id}/                       -> detail with lines, address, proof, events
    GET  .../shop/orders/{order_id}/proofs/{proof_id}/     -> the proof file itself
    POST .../shop/orders/{order_id}/confirm-receipt/  {state_version}
    POST .../shop/orders/{order_id}/cancel/           {state_version, note}
    POST .../shop/orders/{order_id}/ship/             {state_version, courier, tracking_no?, shipped_on?}
    POST .../shop/orders/{order_id}/deliver/          {state_version, delivered_on?}

Order ids are public ids; money is integer centavos. Every route is signed-in only and reaches its
site through site_for(account, site_id, template=SANTE), so a missing, removed, foreign or non-Santé
site answers 404; then the Billing-only rule (403 `billing_only`). While SHOP_ENABLED is off every
route answers 404, signed in or not. A write with an old `state_version` answers 409 `stale`; a
wrong state 409 `wrong_status`. The permission chain is the pack's (apps/pack/api_owner.py).
"""
from datetime import date

from django.conf import settings
from django.http import HttpResponse
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.common import private_files
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.members.contacts import mask
from apps.shop import constants as C, review
from apps.shop.models import ShopOrder
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for

PROOF_FILE_NAMES = {
    "image/jpeg": "proof.jpg", "image/png": "proof.png", "image/webp": "proof.webp",
    "application/pdf": "proof.pdf",
}
FILTERS = {
    "to_review": {"payment_status": C.PENDING_REVIEW},
    "to_ship": {"payment_status": C.VERIFIED, "shipped_on__isnull": True},
    "shipped": {"shipped_on__isnull": False},
    "cancelled": {"payment_status": C.CANCELLED},
}


def shop_site(request):
    """The router's sign-in step. It finds the site before any body is read."""
    if not settings.SHOP_ENABLED:
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site_for(account, site_id, template=templates.SANTE)
        request.shop_site = _owner_site(request, site_id)
    return account


router = Router(tags=["shop-orders"], auth=shop_site)


class VersionIn(Schema):
    model_config = ConfigDict(extra="forbid")
    state_version: int


class CancelIn(VersionIn):
    note: str = ""


class ShipIn(VersionIn):
    courier: str = ""
    tracking_no: str = ""
    shipped_on: date | None = None


class DeliverIn(VersionIn):
    delivered_on: date | None = None


def _limit(request, scope):
    hit(scope[0], request.account.pk, limit=scope[1], window=scope[2])


def _order(site, order_id):
    order = ShopOrder.objects.select_related("member").filter(site=site, public_id=order_id).first()
    if order is None:
        raise ApiError("not_found", "Not found.", status=404)
    return order


def fulfilment_status(order):
    """The buyer's tracker step: placed, confirmed, shipped, delivered or cancelled."""
    if order.payment_status == C.CANCELLED:
        return "cancelled"
    if order.delivered_on is not None:
        return "delivered"
    if order.shipped_on is not None:
        return "shipped"
    return "confirmed" if order.payment_status == C.VERIFIED else "placed"


def _day(value):
    return value.isoformat() if value else None


def row(order):
    return {
        "id": order.public_id, "order_number": order.order_number,
        "receiver_name": order.receiver_name, "mobile_masked": mask("mobile", order.receiver_mobile) if order.receiver_mobile else "",
        "buyer_kind": order.buyer_kind, "ship_mode": order.ship_mode,
        "payment_status": order.payment_status, "status": fulfilment_status(order),
        "subtotal_centavos": order.subtotal_centavos,
        "shipping_charged_centavos": order.shipping_charged_centavos,
        "total_centavos": order.total_centavos, "currency": order.currency,
        "created_at": order.created_at.isoformat(), "state_version": order.state_version,
    }


def detail(order):
    proof = getattr(order, "proof", None) if hasattr(order, "proof") else None
    return {
        **row(order),
        "contact_email": order.contact_email or (order.member.email if order.member_id else ""),
        "receiver_mobile": order.receiver_mobile, "delivery_address": order.delivery_address,
        "shipping_policy": order.shipping_policy,
        "shipping_computed_centavos": order.shipping_computed_centavos,
        "courier": order.courier, "tracking_no": order.tracking_no,
        "shipped_on": _day(order.shipped_on), "delivered_on": _day(order.delivered_on),
        "verified_at": order.verified_at.isoformat() if order.verified_at else None,
        "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "cancel_note": order.cancel_note,
        "lines": [
            {
                "code": l.code, "name": l.name, "quantity": l.quantity,
                "srp_centavos": l.srp_centavos, "line_srp_total_centavos": l.line_srp_total_centavos,
                "line_members_total_centavos": l.line_members_total_centavos,
                "line_charged_centavos": l.line_charged_centavos,
            }
            for l in order.lines.all()
        ],
        "proof": None if proof is None else {
            "id": proof.public_id, "account_kind": proof.account_kind,
            "account_provider_name": proof.account_provider_name, "account_name": proof.account_name,
            "account_number_last4": proof.account_number_last4,
            "reference_number": proof.reference_number,
            "amount_sent_centavos": proof.amount_sent_centavos, "content_type": proof.content_type,
            "submitted_at": proof.submitted_at.isoformat(),
        },
        "events": [
            {
                "kind": e.kind, "note": e.note, "actor_type": e.actor_type,
                "actor_label": e.actor_label, "created_at": e.created_at.isoformat(),
            }
            for e in order.events.all()
        ],
    }


@router.get("/sites/{site_id}/shop/orders/")
def orders_list(request, site_id: str, filter: str = "all"):
    orders = (
        ShopOrder.objects.select_related("member").filter(site=request.shop_site, **FILTERS.get(filter, {}))
        .order_by("-created_at", "-pk")[:C.OWNER_LIST_MAX]
    )
    return {"orders": [row(o) for o in orders]}


@router.get("/sites/{site_id}/shop/orders/{order_id}/")
def order_detail(request, site_id: str, order_id: str):
    return detail(_order(request.shop_site, order_id))


@router.get("/sites/{site_id}/shop/orders/{order_id}/proofs/{proof_id}/")
def order_proof(request, site_id: str, order_id: str, proof_id: str):
    """The file itself, to the Subscriber it belongs to only. The proof is found first, under
    this site and this order, so no file is read for another id."""
    order = _order(request.shop_site, order_id)
    proof = getattr(order, "proof", None) if hasattr(order, "proof") else None
    if proof is None or proof.public_id != proof_id or not proof.file_key:
        raise ApiError("not_found", "Not found.", status=404)
    _limit(request, C.OWNER_LINK_LIMIT)
    response = HttpResponse(private_files.read(proof.file_key), content_type=proof.content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="{PROOF_FILE_NAMES.get(proof.content_type, "proof")}"'
    return response


@router.post("/sites/{site_id}/shop/orders/{order_id}/confirm-receipt/")
def order_confirm(request, site_id: str, order_id: str, payload: VersionIn):
    _limit(request, C.OWNER_WRITE_LIMIT)
    order = _order(request.shop_site, order_id)
    review.confirm_receipt(request.account, request.shop_site, order.pk, state_version=payload.state_version)
    return detail(_order(request.shop_site, order_id))


@router.post("/sites/{site_id}/shop/orders/{order_id}/cancel/")
def order_cancel(request, site_id: str, order_id: str, payload: CancelIn):
    _limit(request, C.OWNER_WRITE_LIMIT)
    order = _order(request.shop_site, order_id)
    review.cancel(
        request.account, request.shop_site, order.pk, state_version=payload.state_version, note=payload.note
    )
    return detail(_order(request.shop_site, order_id))


@router.post("/sites/{site_id}/shop/orders/{order_id}/ship/")
def order_ship(request, site_id: str, order_id: str, payload: ShipIn):
    _limit(request, C.OWNER_WRITE_LIMIT)
    order = _order(request.shop_site, order_id)
    review.ship(
        request.account, request.shop_site, order.pk, state_version=payload.state_version,
        courier=payload.courier, tracking_no=payload.tracking_no, shipped_on=payload.shipped_on,
    )
    return detail(_order(request.shop_site, order_id))


@router.post("/sites/{site_id}/shop/orders/{order_id}/deliver/")
def order_deliver(request, site_id: str, order_id: str, payload: DeliverIn):
    _limit(request, C.OWNER_WRITE_LIMIT)
    order = _order(request.shop_site, order_id)
    review.deliver(
        request.account, request.shop_site, order.pk, state_version=payload.state_version,
        delivered_on=payload.delivered_on,
    )
    return detail(_order(request.shop_site, order_id))
