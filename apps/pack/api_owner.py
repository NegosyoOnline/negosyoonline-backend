"""The Subscriber's Membership Pack order review (port of the pack-orders part of TWCinc's
negosyoonline_sante_pack/api.py), under /v1/sites/{site_id}/pack/orders/:

    GET  .../pack/orders/?filter=to_review|rejected|all   -> {orders: [row]} newest 200
    GET  .../pack/orders/{order_id}/                      -> detail with submissions, decisions
    GET  .../pack/orders/{order_id}/proofs/{proof_id}/    -> the proof file itself
    POST .../pack/orders/{order_id}/approve/  {state_version}
    POST .../pack/orders/{order_id}/reject/   {state_version, reason_code, other_text?}

Order ids are public ids; money is integer centavos. Every route is signed-in only and reaches its
site through site_for(account, site_id, template=SANTE), so a missing, removed, foreign or non-Santé
site answers 404; then the Billing-only rule (403 `billing_only`). While PACK_ENABLED is off every
route answers 404, signed in or not. A write with an old `state_version` answers 409 `stale`.
"""
from django.conf import settings
from django.http import HttpResponse
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.common import private_files
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.members.contacts import mask
from apps.pack import constants as C, review
from apps.pack.models import PackOrder
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for

WRITE_LIMIT = ("pack-write-acct", 60, 600)  # TWCinc's numbers
LINK_LIMIT = ("pack-link-acct", 120, 600)
LIST_MAX = 200
FILTERS = {"to_review": {"payment_status": C.PENDING_REVIEW}, "rejected": {"payment_status": C.REJECTED}}
PROOF_FILE_NAMES = {
    "image/jpeg": "proof.jpg", "image/png": "proof.png", "image/webp": "proof.webp",
    "application/pdf": "proof.pdf",
}


def pack_site(request):
    """The router's sign-in step. It finds the site before any body is read."""
    if not settings.PACK_ENABLED:
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site_for(account, site_id, template=templates.SANTE)
        request.pack_site = _owner_site(request, site_id)
    return account


router = Router(tags=["pack-orders"], auth=pack_site)


class VersionIn(Schema):
    model_config = ConfigDict(extra="forbid")
    state_version: int


class RejectIn(VersionIn):
    reason_code: str = ""
    other_text: str = ""


def _limit(request, scope):
    hit(scope[0], request.account.pk, limit=scope[1], window=scope[2])


def _order(site, order_id):
    order = PackOrder.objects.select_related("member").filter(site=site, public_id=order_id).first()
    if order is None:
        raise ApiError("not_found", "Not found.", status=404)
    return order


def row(order):
    return {
        "id": order.public_id, "order_number": order.order_number, "buyer_name": order.buyer_name,
        "mobile_masked": mask("mobile", order.member.mobile), "method": order.payment_method,
        "payment_status": order.payment_status, "created_at": order.created_at.isoformat(),
        "state_version": order.state_version, "price_centavos": order.price_centavos,
    }


def _submission(sub):
    return {
        "id": sub.public_id, "seq": sub.seq, "account_kind": sub.account_kind,
        "account_provider_name": sub.account_provider_name, "account_name": sub.account_name,
        "account_number_last4": sub.account_number_last4, "reference_number": sub.reference_number,
        "amount_sent_centavos": sub.amount_sent_centavos, "sender_bank": sub.sender_bank,
        "sender_name": sub.sender_name, "sender_account_number": sub.sender_account_number,
        "transferred_at": sub.transferred_at.isoformat(), "submitted_at": sub.submitted_at.isoformat(),
        "content_type": sub.content_type,
        "decisions": [
            {
                "decision": d.decision, "reason_code": d.reason_code,
                "reason_label": C.REASON_LABELS.get(d.reason_code, ""), "other_text": d.other_text,
                "actor_label": d.actor_label, "decided_at": d.decided_at.isoformat(),
            }
            for d in sub.decisions.all()
        ],
    }


def detail(order):
    return {
        **row(order), "price_centavos": order.price_centavos, "currency": order.currency,
        "delivery_address": order.delivery_address, "rejection_lock_active": order.rejection_lock_active,
        "submissions": [_submission(s) for s in order.submissions.prefetch_related("decisions")],
        "events": [
            {
                "kind": e.kind, "old": e.old, "new": e.new, "actor_label": e.actor_label,
                "created_at": e.created_at.isoformat(),
            }
            for e in order.events.all()
        ],
    }


@router.get("/sites/{site_id}/pack/orders/")
def orders_list(request, site_id: str, filter: str = "all"):
    orders = (
        PackOrder.objects.select_related("member").filter(site=request.pack_site, **FILTERS.get(filter, {}))
        .order_by("-created_at", "-pk")[:LIST_MAX]
    )
    return {"orders": [row(o) for o in orders]}


@router.get("/sites/{site_id}/pack/orders/{order_id}/")
def order_detail(request, site_id: str, order_id: str):
    return detail(_order(request.pack_site, order_id))


@router.get("/sites/{site_id}/pack/orders/{order_id}/proofs/{proof_id}/")
def order_proof(request, site_id: str, order_id: str, proof_id: str):
    """The file itself, to the Subscriber it belongs to only. The submission is found first, under
    this site and this order, so no file is read for another id."""
    order = _order(request.pack_site, order_id)
    sub = order.submissions.filter(public_id=proof_id).first()
    if sub is None:
        raise ApiError("not_found", "Not found.", status=404)
    _limit(request, LINK_LIMIT)
    response = HttpResponse(private_files.read(sub.file_key), content_type=sub.content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="{PROOF_FILE_NAMES.get(sub.content_type, "proof")}"'
    return response


@router.post("/sites/{site_id}/pack/orders/{order_id}/approve/")
def order_approve(request, site_id: str, order_id: str, payload: VersionIn):
    _limit(request, WRITE_LIMIT)
    order = _order(request.pack_site, order_id)
    review.approve(request.account, request.pack_site, order.pk, state_version=payload.state_version)
    return detail(_order(request.pack_site, order_id))


@router.post("/sites/{site_id}/pack/orders/{order_id}/reject/")
def order_reject(request, site_id: str, order_id: str, payload: RejectIn):
    _limit(request, WRITE_LIMIT)
    order = _order(request.pack_site, order_id)
    review.reject(
        request.account, request.pack_site, order.pk, state_version=payload.state_version,
        reason_code=payload.reason_code, other_text=payload.other_text,
    )
    return detail(_order(request.pack_site, order_id))
