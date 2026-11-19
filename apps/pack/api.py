"""The Membership Pack checkout routes of the storefront surface (registered by one line in
apps/storefront/api.py):

    POST /storefront/v1/checkout/contact/  {host, visitor_ip, token?, full_name, mobile, email,
        delivery_address?, enrollment_authorized, and optionally agreement_version_id +
        agreement_accept from an older storefront; the terms are accepted at onboarding/details/}
        -> {token, expires_at}: `token` is the raw checkout token, answered once on the first
        call and "" afterwards; `expires_at` is the sliding expiry for the cookie.
    POST /storefront/v1/checkout/check/    {host, visitor_ip, mobile, email} -> {errors}
    POST /storefront/v1/checkout/place/    multipart: host, visitor_ip, token, submit_key,
        account_id, reference_number, amount_sent, sender_bank, sender_first_name,
        sender_last_name, sender_account_number, transferred_at, proof (the file)
        -> {order_number, replay, pay_url}
        `pay_method` is "direct" (the default; the proof fields and file are required) or "xendit"
        (no proof; `pay_url` is the Xendit hosted invoice, null when it could not be created yet).
    POST /storefront/v1/checkout/payment-status/  {host, visitor_ip, token, retry?}
        -> {payment_status, order_number, pay_url}: what the return page shows; the browser never
        marks an order paid.

Every call carries the storefronts server's credential (the surface checks it). The token travels
as data: Django sets no cookie. While PACK_ENABLED is off, and for an unknown, foreign or non-Santé
host, every route answers the same 404 before any pack table is read.
"""
import ipaddress

from django.utils import timezone

from django.conf import settings
from django.http import HttpResponse
from ninja import File, Form, Router, Schema
from ninja.files import UploadedFile

from apps.common import private_files, throttle
from apps.common.errors import ApiError
from apps.pack import (
    access, address_zip, checkout, visits, constants as C, dashboard, interfaces, notices, onboarding, panels, placement, policies, portal, records,
    registration, registration_rules, review, xendit,
)
from apps.members import sessions
from apps.pack.models import PackOrder, PackProofSubmission, SanteRegistration
from apps.storefront.services import served_sante_site

router = Router()


def _site(host):
    site = served_sante_site(host) if settings.PACK_ENABLED else None
    if site is None:
        raise ApiError("not_found", "Not found.", status=404, event="site_not_found", reason="missing")
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
    portal.require_sale_open()  # L45: no Pack sale without the buyer portal

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
    meta_consent: str = ""  # "1" when the visitor accepted tracking; Meta identifiers below are read only then
    fbp: str = ""
    fbc: str = ""
    user_agent: str = ""
    source_url: str = ""
    visitor_ref: str = ""  # the visit this buyer came from, when the storefront has one
    pay_method: str = "direct"  # "direct" (proof) or "xendit" (hosted invoice, no proof)


class PlaceOut(Schema):
    order_number: str
    replay: bool
    pay_url: str | None = None


@router.post("/checkout/place/", response=PlaceOut)
def checkout_place(request, body: Form[PlaceIn], proof: File[UploadedFile] = None):
    site = _site(body.host)
    token = checkout.current(site, body.token)
    if token is None:
        raise _expired()
    data = body.dict()
    meta = {
        "consent": body.meta_consent == "1", "host": body.host, "url": body.source_url, "fbp": body.fbp,
        "fbc": body.fbc, "user_agent": body.user_agent, "visitor_ref": body.visitor_ref,
    }
    for name in ("meta_consent", "fbp", "fbc", "user_agent", "source_url", "visitor_ref", "pay_method"):  # not proof fields
        data.pop(name)
    for name in ("sender_name", "sender_first_name", "sender_last_name"):
        if name not in request.POST:  # an absent field is not an empty one (the name rule reads presence)
            data.pop(name)
    placed = placement.place_pack_order(
        site, token, body.token, submit_key=body.submit_key, data=data, upload=proof,
        ip=_ip(body.visitor_ip), meta=meta, pay_method=body.pay_method or C.PAYMENT_METHOD,
    )
    visits.link_order(site, body.visitor_ref, placed.order)
    return {
        "order_number": placed.order.order_number, "replay": placed.replay,
        "pay_url": xendit.continue_url(placed.order),
    }


class PaymentStatusIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    token: str = ""
    retry: bool = False


class PaymentStatusOut(Schema):
    payment_status: str
    order_number: str
    pay_url: str | None = None


STATUS_NAMES = {
    C.AWAITING_XENDIT: "waiting", C.VERIFIED: "paid", C.XENDIT_EXPIRED: "expired",
    C.XENDIT_MISMATCH: "mismatch", C.PENDING_REVIEW: "under_review", C.REJECTED: "needs_attention",
}


def status_name(order):
    """The return page's word for an order. A waiting order with no invoice yet is `no_invoice`."""
    if order.payment_status == C.AWAITING_XENDIT and not order.xendit_invoice_url:
        return "no_invoice"
    return STATUS_NAMES[order.payment_status]


@router.post("/checkout/payment-status/", response=PaymentStatusOut)
def checkout_payment_status(request, body: PaymentStatusIn):
    """The return page asks this. Reads the order the checkout token placed; a waiting Xendit order
    may get ONE server-side read of its invoice at Xendit (at most one per ~5 s), applied through the
    same verified handler as the webhook. `retry` tries to create a missing invoice (5 per hour per
    order). Nothing in the request can mark an order paid."""
    site = _site(body.host)
    token = checkout.current(site, body.token)
    if token is None:
        raise _expired()
    order = PackOrder.objects.select_related("site", "member").filter(site=site, pk=token.order_ref).first() if token.order_ref else None  # member is None until a Xendit order is paid
    if order is None:
        return {"payment_status": "none", "order_number": "", "pay_url": None}
    if body.retry and order.payment_status == C.AWAITING_XENDIT and not order.xendit_invoice_url:
        try:
            order = xendit.retry_invoice(order)
        except ApiError as exc:
            if exc.code not in ("xendit_unavailable", "invoice_busy"):
                raise  # 429 (too many tries) and the like
            order.refresh_from_db()  # still no invoice: the page shows Try again again
    else:
        order = xendit.read_once(order)
    return {
        "payment_status": status_name(order), "order_number": order.order_number,
        "pay_url": xendit.continue_url(order),
    }


# -- the Membership Pack page visitor (ClickUp z8uq9mfw14) -------------------------------------------
#
#    POST /storefront/v1/visit/          {host, visitor_ip, ref?}               -> {ref}
#    POST /storefront/v1/visit/event/    {host, visitor_ip, ref, event, percent?} -> {ok}
#        event: start | percent (25, 50, 75, 90, 100) | checkout
#    POST /storefront/v1/visit/contact/  {host, visitor_ip, ref, full_name, mobile, email} -> {ok}
#        saved when the buyer leaves a checkout field; kept only in the visit.
# Same gate as the checkout routes (PACK_ENABLED, a served Santé host); an unknown ref answers 404.


class VisitIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    ref: str = ""


class VisitOut(Schema):
    ref: str


class VisitEventIn(VisitIn):
    event: str = ""
    percent: int | None = None


class VisitContactIn(VisitIn):
    full_name: str = ""
    mobile: str = ""
    email: str = ""


class OkOut(Schema):
    ok: bool


@router.post("/visit/", response=VisitOut)
def visit_begin(request, body: VisitIn):
    site = _site(body.host)
    return {"ref": visits.begin(site, body.ref, ip=_ip(body.visitor_ip))}


@router.post("/visit/event/", response=OkOut)
def visit_event(request, body: VisitEventIn):
    site = _site(body.host)
    return {"ok": visits.note_event(site, body.ref, body.event, body.percent, ip=_ip(body.visitor_ip))}


@router.post("/visit/contact/", response=OkOut)
def visit_contact(request, body: VisitContactIn):
    site = _site(body.host)
    visits.save_contact(site, body.ref, body.full_name, body.mobile, body.email, ip=_ip(body.visitor_ip))
    return {"ok": True}


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
    if order.member is None or (order.payment_method == C.XENDIT and order.payment_status in C.XENDIT_UNPAID):
        # Onboarding opens when Xendit confirms; a Xendit order has no member until then.
        raise ApiError("payment_pending", C.MSG_PAYMENT_PENDING, status=409)
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
    payment_method_label: str = ""
    payment_verified: bool = False
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
        "payment_method_label": panels.method_label(order),
        "payment_verified": order.payment_status == C.VERIFIED, "expires_at": token.expires_at.isoformat(),
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
    agreement_version_id: str = ""  # the Membership Terms version the popup showed; needed while the order has no acceptance


@router.post("/onboarding/details/", response=StateOut)
def onboarding_details(request, body: DetailsIn):
    # ASCII digits only and short, so int() never sees "²" or a 5000-digit string
    parts = (body.dob_year, body.dob_month, body.dob_day)
    ok = all(p.isascii() and p.isdigit() and len(p) <= 4 for p in parts)
    dob = f"{body.dob_year}-{int(body.dob_month):02d}-{int(body.dob_day):02d}" if ok else ""
    return _after(body, lambda site, token, order, member: registration.submit_details(
        site, member, date_of_birth=dob, desired_username=body.desired_username,
        alternate_username=body.alternate_username, terms_accepted=body.terms,
        agreement_version_id=body.agreement_version_id))


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


# -- the member's own payment card, proof and new proof (the member session token) -------------------

class MemberIn(Schema):
    host: str = ""
    token: str = ""


def _member(host, token):
    """(site, member) for a live member session of this site, or 401 `member_session_invalid`."""
    site = _site(host)
    session = sessions.find_session(site, token)
    if session is None:
        raise ApiError("member_session_invalid", "Not signed in.", status=401)
    return site, session.member


class MemberPackOut(Schema):
    pack: dict | None
    registration: dict | None


@router.post("/member/pack/", response=MemberPackOut)
def member_pack(request, body: MemberIn):
    """The payment card, the rejection reason (only here, after sign-in) and the member's own
    proofs. The registration card is shown while benefits are on (TWCinc AC-68) and for a bank-transfer buyer awaiting the verify."""
    _, member = _member(body.host, body.token)
    return {
        "pack": panels.pack_panel(member),
        "registration": panels.registration_panel(member) if interfaces.benefits_active(member) or interfaces.bank_order_placed(member) else None,
    }


class HomeOut(Schema):
    summary: dict
    order_monitor: dict


class OrdersOut(Schema):
    cards: list[dict]


@router.post("/member/home/", response=HomeOut)
def member_home(request, body: MemberIn):
    """The Dashboard tiles (`summary`) and Order Monitoring card over the member's pack orders."""
    _, member = _member(body.host, body.token)
    return dashboard.home(member)


@router.post("/member/orders/", response=OrdersOut)
def member_orders(request, body: MemberIn):
    """My Orders: one card per pack order, newest first."""
    _, member = _member(body.host, body.token)
    return dashboard.my_orders(member)


class MemberProofIn(MemberIn):
    proof_id: str = ""


@router.post("/member/pack/proof/")
def member_pack_proof(request, body: MemberProofIn):
    """The member's own proof file. Another member's id answers the same 404."""
    site, member = _member(body.host, body.token)
    sub = PackProofSubmission.objects.filter(
        site=site, public_id=body.proof_id, order__member=member
    ).first()
    if sub is None:
        raise ApiError("not_found", "Not found.", status=404)
    response = HttpResponse(private_files.read(sub.file_key), content_type=sub.content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = 'inline; filename="proof"'
    return response


class ResubmitIn(PlaceIn):
    pass


@router.post("/member/pack/resubmit/", response=MemberPackOut)
def member_pack_resubmit(request, body: Form[ResubmitIn], proof: File[UploadedFile] = None):
    """Multipart, like the checkout proof, plus `token` (the member session). Only from `rejected`:
    409 `not_rejected` otherwise. The lock stays until the Subscriber approves."""
    site, member = _member(body.host, body.token)
    data = body.dict()
    for name in ("sender_name", "sender_first_name", "sender_last_name"):
        if name not in request.POST:
            data.pop(name)
    throttle.hit("pack-resubmit", member.pk, limit=10, window=3600)
    review.resubmit(site, member, data=data, upload=proof, ip=_ip(body.visitor_ip))
    return {"pack": panels.pack_panel(member), "registration": None}


# -- the store policy pages' data -------------------------------------------------------------------

@router.get("/policies/{slug}/")
def policy_page(request, slug: str, host: str = ""):
    """GET /storefront/v1/policies/{slug}/?host=... -> {slug, title, version, effective_date,
    business, shipping, privacy, links}. 404 (the one shape) until the store has filled every value
    the page prints, for an unknown slug, an unknown, foreign or non-Santé host, and while PACK_ENABLED
    is off. The storefronts server renders the page from this data."""
    data = policies.page(_site(host), slug)
    if data is None:
        raise ApiError("not_found", "Not found.", status=404)
    return data


# -- the member dashboard's Santé registration card (TWCinc dashboard/registration/*), by member session

class MemberRegIn(MemberIn):
    visitor_ip: str = ""
    mobile: str = ""
    code: str = ""
    dob_year: str = ""
    dob_month: str = ""
    dob_day: str = ""
    desired_username: str = ""
    alternate_username: str = ""
    terms: bool | None = None  # the card's tick; needed only while panels.registration_panel says terms_required
    agreement_version_id: str = ""  # the version the card showed; ignored when the order already has an acceptance
    date_of_birth: str = ""  # the dashboard form's one date field, YYYY-MM-DD
    delivery_address: dict | None = None  # line, barangay, city, province, zip


class MemberRegOut(Schema):
    registration: dict | None
    mobile: dict


def _reg_out(member):
    member.refresh_from_db()
    return {"registration": panels.registration_panel(member), "mobile": panels.mobile_check(member)}


@router.post("/member/registration/", response=MemberRegOut)
def member_registration(request, body: MemberRegIn):
    _, member = _member(body.host, body.token)
    return _reg_out(member)


@router.post("/member/registration/mobile/send/", response=MemberRegOut)
def member_mobile_send(request, body: MemberRegIn):
    site, member = _member(body.host, body.token)
    onboarding.send_member_code(site, member, body.mobile or member.mobile, ip=_ip(body.visitor_ip))
    return _reg_out(member)


@router.post("/member/registration/mobile/verify/", response=MemberRegOut)
def member_mobile_verify(request, body: MemberRegIn):
    site, member = _member(body.host, body.token)
    onboarding.verify_member_code(site, member, body.code, ip=_ip(body.visitor_ip))
    return _reg_out(member)


@router.post("/member/registration/details/", response=MemberRegOut)
def member_details(request, body: MemberRegIn):
    """The dashboard details form, as TWCinc's: one `date_of_birth` field, a terms tick plus
    `agreement_version_id` only while `registration.terms_required` (the order has no acceptance;
    the note names the version on record or just accepted), and a delivery address block asked for while the
    member has none. The three dob_* parts still work when `date_of_birth` is empty."""
    site, member = _member(body.host, body.token)
    dob = body.date_of_birth
    if not dob:
        parts = (body.dob_year, body.dob_month, body.dob_day)
        ok = all(p.isascii() and p.isdigit() and len(p) <= 4 for p in parts)
        dob = f"{body.dob_year}-{int(body.dob_month):02d}-{int(body.dob_day):02d}" if ok else ""
    registration.submit_details(
        site, member, date_of_birth=dob, desired_username=body.desired_username,
        alternate_username=body.alternate_username, delivery_address=body.delivery_address,
        terms_accepted=body.terms, agreement_version_id=body.agreement_version_id,
    )
    return _reg_out(member)
