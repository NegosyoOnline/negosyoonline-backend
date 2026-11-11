"""The Membership Pack checkout routes of the storefront surface (registered by one line in
apps/storefront/api.py):

    POST /storefront/v1/checkout/contact/  {host, visitor_ip, token?, full_name, mobile, email,
        delivery_address?, agreement_version_id, agreement_accept, enrollment_authorized}
        -> {token, expires_at}: `token` is the raw checkout token, answered once on the first
        call and "" afterwards; `expires_at` is the sliding expiry for the cookie.
    POST /storefront/v1/checkout/check/    {host, visitor_ip, mobile, email} -> {errors}
    POST /storefront/v1/checkout/place/    multipart: host, visitor_ip, token, submit_key,
        account_id, reference_number, amount_sent, sender_bank, sender_first_name,
        sender_last_name, sender_account_number, transferred_at, proof (the file)
        -> {order_number, replay}

Every call carries the storefronts server's credential (the surface checks it). The token travels
as data: Django sets no cookie. While PACK_ENABLED is off, and for an unknown, foreign or non-Santé
host, every route answers the same 404 before any pack table is read.
"""
import ipaddress

from django.conf import settings
from ninja import File, Form, Router, Schema
from ninja.files import UploadedFile

from apps.common.errors import ApiError
from apps.pack import checkout, constants as C, placement, records
from apps.storefront.services import served_sante_site

router = Router()


def _site(host):
    site = served_sante_site(host) if settings.PACK_ENABLED else None
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site


def _ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return ""


def _expired():
    return ApiError("checkout_expired", C.MSG_CHECKOUT_EXPIRED, status=409)


class ContactIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    token: str = ""
    full_name: str = ""
    mobile: str = ""
    email: str = ""
    delivery_address: dict | None = None
    agreement_version_id: str = ""
    agreement_accept: str = ""
    enrollment_authorized: str = ""


class ContactOut(Schema):
    token: str
    expires_at: str


@router.post("/checkout/contact/", response=ContactOut)
def checkout_contact(request, body: ContactIn):
    site, ip = _site(body.host), _ip(body.visitor_ip)

    def extras(token):
        return {
            records.AGREEMENT_KEY: records.agreement_from_post(
                token, version_id=body.agreement_version_id, accept=body.agreement_accept
            ),
            records.ENROLLMENT_KEY: records.enrollment_from_post(body.enrollment_authorized),
        }

    data = {
        "full_name": body.full_name, "mobile": body.mobile, "email": body.email,
        "delivery_address": body.delivery_address,
    }
    token = checkout.current(site, body.token) if body.token else None
    if token is None:
        token, raw = checkout.start_checkout(site, data, extras=extras, ip=ip)
    else:
        token, raw = checkout.save_contact(site, token, data, extras=extras, ip=ip), ""
    token.refresh_from_db()
    return {"token": raw, "expires_at": token.expires_at.isoformat()}


class CheckIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    mobile: str = ""
    email: str = ""


class CheckOut(Schema):
    errors: dict[str, list[str]]


@router.post("/checkout/check/", response=CheckOut)
def checkout_check(request, body: CheckIn):
    site = _site(body.host)
    return {"errors": checkout.check_contact(site, body.mobile, body.email, ip=_ip(body.visitor_ip))}


class PlaceIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    token: str = ""
    submit_key: str = ""
    account_id: str = ""
    reference_number: str = ""
    amount_sent: str = ""
    sender_bank: str = ""
    sender_name: str = ""
    sender_first_name: str = ""
    sender_last_name: str = ""
    sender_account_number: str = ""
    transferred_at: str = ""


class PlaceOut(Schema):
    order_number: str
    replay: bool


@router.post("/checkout/place/", response=PlaceOut)
def checkout_place(request, body: Form[PlaceIn], proof: File[UploadedFile] = None):
    site = _site(body.host)
    token = checkout.current(site, body.token)
    if token is None:
        raise _expired()
    data = body.dict()
    for name in ("sender_name", "sender_first_name", "sender_last_name"):
        if name not in request.POST:  # an absent field is not an empty one (the name rule reads presence)
            data.pop(name)
    placed = placement.place_pack_order(
        site, token, body.token, submit_key=body.submit_key, data=data, upload=proof,
        ip=_ip(body.visitor_ip),
    )
    return {"order_number": placed.order.order_number, "replay": placed.replay}
