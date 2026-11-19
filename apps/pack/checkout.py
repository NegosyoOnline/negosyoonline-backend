"""Checkout C2: the guest checkout token and the contact step (port of TWCinc's
negosyoonline_orders/checkout.py and negosyoonline_sante_pack/onboarding.py, steps 1-2).

The token is created only by the first valid contact call, after the per-IP limit, validation and
the duplicate check; never by a read. Only its SHA-256 is stored. The raw token is answered once,
to the storefronts server, which keeps it in the buyer's cookie. Every use pushes the expiry two
hours on (TWCinc's sliding cookie). The IP is advisory and comes from the server credential's call.

The mobile code send/verify of TWCinc's checkout is not here (PK2b/PK7); a returning buyer
still has to hold a used onboarding challenge for the number (see placement)."""
import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.common import throttle
from apps.common.errors import ApiError
from apps.members import contacts, services as members
from apps.members.models import Member
from apps.pack import constants as C
from apps.pack.models import CheckoutToken, PackOrder

THROTTLE_MESSAGE_WINDOW = 3600


def token_hash(raw):
    return hashlib.sha256((raw or "").encode()).hexdigest()


def begin(site, *, now=None):
    now = now or timezone.now()
    raw = secrets.token_urlsafe(32)
    row = CheckoutToken.objects.create(
        site=site, token_hash=token_hash(raw), created_at=now,
        expires_at=now + timedelta(seconds=C.TOKEN_TTL_SECONDS),
    )
    return row, raw


def current(site, raw, *, lock=False, now=None, slide=True):
    """The live token this raw value names on this site, or None. A use slides its expiry."""
    if not raw or not isinstance(raw, str):
        return None
    now = now or timezone.now()
    rows = CheckoutToken.objects.filter(site=site, token_hash=token_hash(raw), expires_at__gt=now)
    row = (rows.select_for_update() if lock else rows).first()
    if row is not None and slide:
        row.expires_at = now + timedelta(seconds=C.TOKEN_TTL_SECONDS)
        CheckoutToken.objects.filter(pk=row.pk).update(expires_at=row.expires_at)
    return row


def purge_expired(site, now):
    """Expired tokens of this site: never needed after they expire."""
    return CheckoutToken.objects.filter(site=site, expires_at__lte=now).delete()[0]


def clean_contact(data):
    """The typed contact, normalised, or ApiError 422-shaped with every field's message."""
    data, errors = data or {}, {}
    name = (data.get("full_name") or "").strip()
    if not 1 <= len(name) <= 120:
        errors["full_name"] = [C.MSG_NAME]
    out = {"full_name": name}
    for field, fn in (("mobile", contacts.clean_mobile), ("email", contacts.clean_email)):
        try:
            out[field] = fn(data.get(field))
        except ApiError as exc:
            errors.update(exc.fields or {})
    address = data.get("delivery_address")
    out["delivery_address"] = {}
    if address and any(str(v or "").strip() for v in address.values()):  # collected after payment; checked if sent
        try:
            out["delivery_address"] = members.clean_address(address)
        except ApiError as exc:
            errors.update(exc.fields or {})
    if errors:
        raise ApiError("invalid_contact", C.MSG_CHECK_FIELDS, status=422, fields=errors)
    return out


def abandoned(member):
    """A checkout that ended without a paid order holds no contact ("Unpaid orders are never
    consulted"): an onboarding member whose mobile is still pending and who never ordered, or (a member
    the Xendit checkout made at placement before the member came only with the payment, hotfix
    z8uq9mgpt5) whose every order is an unpaid Xendit order with no benefits. A real paid member, or
    one with a bank-transfer order, is never abandoned."""
    if member.source != "onboarding":
        return False
    orders = PackOrder.objects.filter(member=member)
    if not orders.exists():
        return member.mobile_state == "pending"
    return not orders.exclude(
        payment_method=C.XENDIT, payment_status__in=C.XENDIT_UNPAID, benefits_active_at__isnull=True
    ).exists()


def live_order(member):
    return PackOrder.objects.filter(member=member, payment_status__in=C.LIVE_STATUSES).first()


def benefits_active(member):
    order = live_order(member)
    return bool(order is not None and order.benefits_active_at)


def held_by_pending_order(site, mobile, email, exclude=None):
    """"mobile" or "email" when a bank order that has no member yet (its proof is under review or
    was rejected) holds that contact, else None. The buyer has sent money, so the contact stays
    taken as it was when the member existed; an unpaid Xendit order never holds one."""
    orders = PackOrder.objects.filter(
        site=site, member__isnull=True, payment_method=C.PAYMENT_METHOD,
        payment_status__in=(C.PENDING_REVIEW, C.REJECTED),
    )
    if exclude is not None:
        orders = orders.exclude(pk=exclude.pk)
    for field, value in (("mobile", mobile), ("email", email)):
        if value and orders.filter(**{f"buyer_{field}": value}).exists():
            return field
    return None


def order_on_file(field):
    """409 for a contact a memberless bank order holds. No account exists, so it is not contact_in_use."""
    message = C.ORDER_ON_FILE[field]
    return ApiError("order_on_file", message, status=409, fields={field: [message]})


def match_existing(site, mobile, email, *, exclude_order=None):
    """None for a new buyer; the returning member (same mobile AND same email, no benefits, no
    live order); or ApiError contact_in_use, for this site or another live store, or order_on_file
    when only a memberless bank order holds the contact. `exclude_order`
    is the order that is itself making its member (attach_member)."""
    by_mobile = Member.objects.filter(site=site, mobile=mobile).first()
    by_email = Member.objects.filter(site=site, email=email).first()
    by_mobile = None if by_mobile is not None and abandoned(by_mobile) else by_mobile
    by_email = None if by_email is not None and abandoned(by_email) else by_email
    if (by_mobile is not None or by_email is not None) and not (
        by_mobile is not None and by_email is not None and by_mobile.pk == by_email.pk
        and not benefits_active(by_mobile) and live_order(by_mobile) is None
    ):
        raise members.contact_in_use("mobile" if by_mobile is not None else "email")
    held = held_by_pending_order(site, mobile, email, exclude_order)
    if held:
        raise order_on_file(held)
    members.require_free_elsewhere(site, "mobile", mobile)
    members.require_free_elsewhere(site, "email", email)
    return by_mobile


def release_abandoned(site, mobile, email):
    """Under the caller's site row lock, just before a new member takes these contacts."""
    for member in Member.objects.filter(Q(mobile=mobile) | Q(email=email), site=site):
        if abandoned(member):
            members.release_contacts(site, member)


def throttle_contact(token, ip):
    """Answered BEFORE validation and the duplicate check, so a 429 reveals no membership."""
    if ip:
        scope, limit, window = C.CONTACT_IP_LIMIT
        throttle.hit(scope, ip, limit=limit, window=window)
    if token is not None:
        scope, limit, window = C.CONTACT_TOKEN_LIMIT
        throttle.hit(scope, token.pk, limit=limit, window=window)


def check_contact(site, mobile, email, *, ip=None):
    """The checkout marks a contact as the buyer finishes typing it. Saves nothing and creates no
    token; returns {field: [message]}. Each contact is judged on its own (unique per member); one
    held by a member with benefits or a live order is refused alone, and any other is told apart
    from a returning buyer only by the pair. A malformed email gets the refusal Continue would
    give; a partial mobile is not judged. Placement still decides."""
    if ip:
        scope, limit, window = C.CHECK_IP_LIMIT
        throttle.hit(scope, ip, limit=limit, window=window)
    values, errors = {}, {}
    for field, raw, clean in (
        ("mobile", mobile, contacts.clean_mobile), ("email", email, contacts.clean_email),
    ):
        try:
            values[field] = clean(raw) if raw else None
        except ApiError as exc:
            values[field] = None
            if field == "email":
                errors.update(exc.fields or {})
    paired = bool(values["mobile"] and values["email"])
    if paired:
        try:
            match_existing(site, values["mobile"], values["email"])
        except ApiError as exc:
            errors.update(exc.fields or {})
    for field, value in values.items():
        if not value or field in errors:
            continue
        if held_by_pending_order(site, value if field == "mobile" else None, value if field == "email" else None):
            errors.update(order_on_file(field).fields)
            continue
        member = Member.objects.filter(site=site, **{field: value}).first()
        if member is not None and not abandoned(member):
            # Each contact is unique, so each is judged on its own. One held by a member who has
            # benefits or a live order can never be taken again; any other may be a returning
            # buyer, told apart only by the pair, so it is marked only once the pair is judged.
            if paired or benefits_active(member) or live_order(member) is not None:
                errors.update(members.contact_in_use(field).fields)
                continue
        try:
            members.require_free_elsewhere(site, field, value)
        except ApiError as exc:
            errors.update(exc.fields or {})
    return errors


def start_checkout(site, data, *, extras=None, ip=None):
    """Returns (token, raw). `extras(token)` returns the agreement and enrollment acceptance the
    call keeps (it runs after the limit, before the contact is judged); a token without them
    cannot place."""
    throttle_contact(None, ip)
    kept = extras(None) if extras else None
    c = clean_contact(data)
    match_existing(site, c["mobile"], c["email"])
    token, raw = begin(site)
    return _store_contact(site, token, c, kept), raw


def save_contact(site, token, data, *, extras=None, ip=None):
    throttle_contact(token, ip)
    kept = extras(token) if extras else None
    c = clean_contact(data)
    match_existing(site, c["mobile"], c["email"])
    return _store_contact(site, token, c, kept)


def _store_contact(site, token, c, extras=None):
    with transaction.atomic():
        t = CheckoutToken.objects.select_for_update().get(pk=token.pk, site=site)
        if t.order_ref:
            raise ApiError("checkout_used", C.MSG_CHECKOUT_EXPIRED, status=409)
        if t.requested_mobile != c["mobile"]:
            t.onboarding_challenge_id, t.verified_mobile = None, ""
        t.pending_contact = {
            "full_name": c["full_name"], "mobile": c["mobile"], "email": c["email"],
            "delivery_address": c["delivery_address"], **(extras or {}),
        }
        t.save(update_fields=["pending_contact", "onboarding_challenge_id", "verified_mobile"])
    return t
