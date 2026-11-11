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

from django.utils import timezone

from django.conf import settings
from ninja import File, Form, Router, Schema
from ninja.files import UploadedFile

from apps.common import throttle
from apps.common.errors import ApiError
from apps.pack import (
    access, address_zip, checkout, constants as C, notices, onboarding, panels, placement, records,
    registration, registration_rules,
)
from apps.pack.models import PackOrder, SanteRegistration
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


# -- the funnel after payment (checkout token -> its own placed order) ------------------------------

STEP_ORDER = ("verify", "address", "details", "done")


def _closed():
    return ApiError("checkout_closed", "Not signed in.", status=409)


def _funnel(host, raw):
    """(site, token, order, member) for a checkout token that has placed an order. A token that
    began before a recovery or a contact change of its member reaches nothing."""
    site = _site(host)
    token = checkout.current(site, raw)
    order = PackOrder.objects.select_related("member").filter(site=site, pk=token.order_ref).first() if token and token.order_ref else None
    if token is None or order is None:
        raise _closed()
    if access.contacts_replaced_since(order.member, token.created_at):
        raise ApiError("checkout_stale", C.MSG_CHECKOUT_EXPIRED, status=409)
    return site, token, order, order.member


def _step(member, order):
    """The first onboarding step still open: 1 SMS and email codes, 2 delivery, 3 Santé form."""
    if member.mobile_state == "pending" or member.email_state != "verified":
        return "verify"
    if not (order.delivery_address or {}).get("receiver_name"):
        return "address"
    reg = SanteRegistration.objects.filter(member=member).first()
    if reg is None or (reg.details_confirmed_at is None and reg.status in panels.DETAILS_OPEN):
        return "details"
    return "done"


class FunnelIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    token: str = ""


class StateOut(Schema):
    step: str
    order_number: str
    buyer_first_name: str
    mobile_masked: str
    mobile: dict
    email: dict
    address: dict
    details_erased: bool
    registration_held: dict | None = None
    expires_at: str


def _state(site, token, order, member, *, with_held=False):
    from apps.members import contacts, otp
    now = timezone.now()
    open_email = otp.latest_recent(site, purpose="onboarding", binding_raw=onboarding.email_binding(member))
    open_sms = onboarding.usable_challenge(member, otp.latest_recent(
        site, purpose="onboarding", binding_raw=onboarding.member_binding(member)
    ))

    def resend_in(ch):
        return max(0, otp.RESEND_SECONDS - int((now - ch.created_at).total_seconds())) if ch is not None else 0

    def attempts_left(ch):
        return max(0, otp.MAX_GUESSES - ch.attempts) if ch is not None else otp.MAX_GUESSES

    reg = SanteRegistration.objects.filter(member=member).first()
    held = None
    if with_held and reg is not None and (panels.erased_hold(reg) or reg.status == "cancelled"):
        held = {"label": panels.MEMBER_REG_LABELS[reg.status], "line": panels.member_reg_line(reg)}
    token.refresh_from_db()
    return {
        "step": _step(member, order), "order_number": order.order_number,
        "buyer_first_name": member.full_name.split(" ")[0],
        "mobile_masked": contacts.mask("mobile", member.mobile),
        "mobile": {**panels.mobile_check(member), "resend_in": resend_in(open_sms), "attempts_left": attempts_left(open_sms)},
        "email": {
            "verified": member.email_state == "verified", "email": member.email,
            "masked": contacts.mask("email", member.email), "code_sent": open_email is not None,
            "resend_in": resend_in(open_email), "attempts_left": attempts_left(open_email),
        },
        "address": {"country": "Philippines", "receiver_name": member.full_name, "receiver_mobile": member.mobile,
                    **(order.delivery_address or {})},
        "details_erased": panels.details_erased(reg), "registration_held": held,
        "expires_at": token.expires_at.isoformat(),
    }


@router.post("/onboarding/state/", response=StateOut)
def onboarding_state(request, body: FunnelIn):
    """Every call slides the checkout token two hours (checkout.current does)."""
    site, token, order, member = _funnel(body.host, body.token)
    return _state(site, token, order, member, with_held=True)


class CodeIn(FunnelIn):
    code: str = ""
    mobile: str = ""


def _after(body, act):
    site, token, order, member = _funnel(body.host, body.token)
    act(site, token, order, member)
    member.refresh_from_db()
    order.refresh_from_db()
    return _state(site, token, order, member)


@router.post("/onboarding/mobile/send/", response=StateOut)
def onboarding_mobile_send(request, body: CodeIn):
    return _after(body, lambda site, token, order, member: onboarding.send_member_code(
        site, member, body.mobile or member.mobile, ip=_ip(body.visitor_ip)))


@router.post("/onboarding/mobile/verify/", response=StateOut)
def onboarding_mobile_verify(request, body: CodeIn):
    return _after(body, lambda site, token, order, member: onboarding.verify_member_code(
        site, member, body.code, ip=_ip(body.visitor_ip), token=token))


@router.post("/onboarding/email/send/", response=StateOut)
def onboarding_email_send(request, body: CodeIn):
    return _after(body, lambda site, token, order, member: onboarding.send_member_email_code(
        site, member, ip=_ip(body.visitor_ip)))


@router.post("/onboarding/email/verify/", response=StateOut)
def onboarding_email_verify(request, body: CodeIn):
    return _after(body, lambda site, token, order, member: onboarding.verify_member_email_code(
        site, member, body.code, ip=_ip(body.visitor_ip)))


class AddressIn(FunnelIn):
    receiver_name: str = ""
    receiver_mobile: str = ""
    line: str = ""
    province: str = ""
    city: str = ""
    barangay: str = ""
    landmark: str = ""
    postal_code: str = ""
    zip: str = ""


@router.post("/onboarding/address/", response=StateOut)
def onboarding_address(request, body: AddressIn):
    keys = ("receiver_name", "receiver_mobile", "line", "province", "city", "barangay", "landmark", "postal_code", "zip")
    return _after(body, lambda site, token, order, member: registration.save_delivery(
        site, member, order, {k: getattr(body, k) for k in keys}))


class DetailsIn(FunnelIn):
    dob_year: str = ""
    dob_month: str = ""
    dob_day: str = ""
    desired_username: str = ""
    alternate_username: str = ""
    terms: bool = False


@router.post("/onboarding/details/", response=StateOut)
def onboarding_details(request, body: DetailsIn):
    # ASCII digits only and short, so int() never sees "²" or a 5000-digit string
    parts = (body.dob_year, body.dob_month, body.dob_day)
    ok = all(p.isascii() and p.isdigit() and len(p) <= 4 for p in parts)
    dob = f"{body.dob_year}-{int(body.dob_month):02d}-{int(body.dob_day):02d}" if ok else ""
    return _after(body, lambda site, token, order, member: registration.submit_details(
        site, member, date_of_birth=dob, desired_username=body.desired_username,
        alternate_username=body.alternate_username, terms_accepted=body.terms))


class CompletedOut(Schema):
    result: str  # issued | open | stale | spent
    session_token: str


@router.post("/onboarding/completed/", response=CompletedOut)
def onboarding_completed(request, body: FunnelIn):
    """Only this POST issues the member session, once per checkout token; a reload, a prefetch or a
    mail scanner never does."""
    site, token, order, member = _funnel(body.host, body.token)
    if _step(member, order) != "done":
        return {"result": "open", "session_token": ""}
    result, raw = onboarding.issue_completed_session(site, member, token, ip=_ip(body.visitor_ip))
    return {"result": result, "session_token": raw}


# -- a returning buyer's code step in the checkout --------------------------------------------------

class StatusOut(Schema):
    status: str


class CheckoutStateOut(Schema):
    needs_code: bool
    mobile: str
    verified: bool
    expires_at: str


@router.post("/checkout/state/", response=CheckoutStateOut)
def checkout_state(request, body: FunnelIn):
    site = _site(body.host)
    token = checkout.current(site, body.token)
    if token is None:
        raise _expired()
    token.refresh_from_db()
    return {
        "needs_code": onboarding.needs_code(site, token) if token.pending_contact else False,
        "mobile": token.requested_mobile, "verified": bool(token.verified_mobile),
        "expires_at": token.expires_at.isoformat(),
    }


@router.post("/checkout/code/send/", response=StatusOut)
def checkout_code_send(request, body: FunnelIn):
    site = _site(body.host)
    token = checkout.current(site, body.token)
    if token is None:
        raise _expired()
    return {"status": onboarding.send_code(site, token, body.token, ip=_ip(body.visitor_ip))}


@router.post("/checkout/code/verify/", response=StatusOut)
def checkout_code_verify(request, body: CodeIn):
    site = _site(body.host)
    token = checkout.current(site, body.token)
    if token is None:
        raise _expired()
    onboarding.verify_code(site, token, body.token, body.code, ip=_ip(body.visitor_ip))
    return {"status": "verified"}


# -- the secure order-access link -------------------------------------------------------------------

class AccessIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    link: str = ""


class AccessOut(Schema):
    link_open: bool
    renewable: bool


def _link(host, link):
    site = _site(host)
    row = access.read(site, link)
    if row is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site, row


@router.post("/access/read/", response=AccessOut)
def access_read(request, body: AccessIn):
    """A GET-like read: changes nothing, so a mail scanner's visit spends nothing."""
    _, row = _link(body.host, body.link)
    return {"link_open": access.is_open(row), "renewable": access.applies(row.member)}


class AccessRenewOut(Schema):
    sent: bool
    link_open: bool
    renewable: bool


def _access_throttle(ip):
    if ip:
        scope, limit, window = C.ACCESS_IP_LIMIT
        throttle.hit(scope, ip, limit=limit, window=window)


@router.post("/access/renew/", response=AccessRenewOut)
def access_renew(request, body: AccessIn):
    _, row = _link(body.host, body.link)
    _access_throttle(_ip(body.visitor_ip))
    sent = False
    if access.applies(row.member):
        notices.access_link(row.member)
        sent = True
    row.refresh_from_db()
    return {"sent": sent, "link_open": access.is_open(row), "renewable": access.applies(row.member)}


class RedeemOut(Schema):
    redeemed: bool
    token: str
    expires_at: str


@router.post("/access/redeem/", response=RedeemOut)
def access_redeem(request, body: AccessIn):
    """Spends the link once. Marks the email verified (only the inbox owner opens the link) and
    answers a fresh checkout token for the member's latest order: the link opens the onboarding
    step, never a session. The session comes only from the Completed step."""
    site, row = _link(body.host, body.link)
    _access_throttle(_ip(body.visitor_ip))
    if access.redeem(row) is None:
        return {"redeemed": False, "token": "", "expires_at": ""}
    onboarding.confirm_member_email(
        site, row.member, actor_label="Order access link", target=row.target
    )
    order = PackOrder.objects.filter(site=site, member=row.member).order_by("-created_at", "-pk").first()
    token, raw = checkout.begin(site)
    token.order_ref = order.pk if order is not None else None
    token.save(update_fields=["order_ref"])
    return {"redeemed": True, "token": raw, "expires_at": token.expires_at.isoformat()}


# -- the delivery address's ZIP, from the vendored dataset ------------------------------------------

class ZipIn(Schema):
    host: str = ""
    province: str = ""
    city: str = ""
    barangay: str = ""
    postal_code: str = ""


class ZipOut(Schema):
    ok: bool
    zip_code: str
    zip_precision: str
    error_field: str
    error_message: str


@router.post("/address/zip/", response=ZipOut)
def address_zip_lookup(request, body: ZipIn):
    _site(body.host)
    d = address_zip.derive_address_fields(body.province, body.city, body.barangay, body.postal_code)
    return {
        "ok": d.ok, "zip_code": d.zip_code or "", "zip_precision": d.zip_precision or "",
        "error_field": d.error_field or "", "error_message": d.error_message,
    }
