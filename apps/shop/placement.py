"""Shop placement (TWCinc `place_order`, bank transfer only, no billing meter).

One transaction under the Site row lock, keyed by UniqueConstraint(site, idempotency_key): a
replay returns the first order and writes nothing. Each product is locked and copied by
`catalog.snapshot.take` inside the transaction, and the order is re-priced from those locked
rows; the posted fingerprint must equal the recomputed one ("Prices changed. Review your
order."). Nothing posted but ids, quantities, ship mode, address, receiver and proof fields is
read: the browser never sends a price. The proof file is stored BEFORE the transaction and
discarded on any rollback. The order is numbered SO00001 per site."""
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from apps.billing import services as billing
from apps.catalog import services as catalog, snapshot
from apps.catalog.models import CatalogProduct
from apps.common import private_files
from apps.common.errors import ApiError
from apps.common.phones import normalise_ph_mobile
from apps.payments import services as payments
from apps.shop import constants as C, notices, proofs, quote, shipping
from apps.shop.models import ShopOrder, ShopOrderLine
from apps.sites.models import Site

logger = logging.getLogger(__name__)

ADDRESS_KEYS = ("line", "barangay", "city", "province", "zip", "landmark")
ADDRESS_MAX = 120


@dataclass(frozen=True)
class Placed:
    order: ShopOrder
    view_token: str  # the raw token, answered once; "" on a replay
    replay: bool


def idempotency_key(submit_key):
    submit_key = (submit_key or "").strip()
    if not 1 <= len(submit_key) <= 64:
        raise ApiError("no_submit_key", C.MSG_NO_SUBMIT_KEY, status=409)
    return hashlib.sha256(f"shop-submit:{submit_key}".encode()).hexdigest()


def token_hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _replay(site, key):
    return ShopOrder.objects.filter(site=site, idempotency_key=key).first()


def _next_number(site):
    return C.ORDER_NUMBER % (ShopOrder.objects.filter(site=site).count() + 1)


# -- the receiver and the address -----------------------------------------------------------------

def _invalid(fields, code="invalid_order"):
    return ApiError(
        code, C.MSG_CHECK_FIELDS, status=422, fields={k: [v] for k, v in fields.items()}
    )


def clean_receiver(buyer, data):
    """(name, mobile, email) of the receiver. A signed-in member may leave name and mobile blank:
    their own are used. The email is optional."""
    errors = {}
    member = buyer.member
    name = " ".join(str(data.get("receiver_name") or "").split())
    if not name and member is not None:
        name = member.full_name
    if not 1 <= len(name) <= C.RECEIVER_NAME_MAX:
        errors["receiver_name"] = "Enter the receiver's name."
    raw_mobile = str(data.get("receiver_mobile") or "").strip()
    mobile = ""
    if not raw_mobile and member is not None:
        mobile = member.mobile
    else:
        try:
            mobile = normalise_ph_mobile(raw_mobile)
        except ValueError:
            errors["receiver_mobile"] = "Enter a valid Philippine mobile number."
    email = str(data.get("contact_email") or "").strip().lower()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            errors["contact_email"] = "Enter a valid email address."
    if errors:
        raise _invalid(errors, "invalid_receiver")
    return name, mobile, email


def clean_address(buyer, mode, posted):
    """The delivery address stored on the order. `home` is the member's own saved address (the
    server's, never the posted one); any other mode needs the typed address, with a province the
    ZIP dataset knows."""
    if mode == C.HOME:
        saved = buyer.member.delivery_address or {}
        out = {k: str(saved.get(k) or "").strip() for k in ADDRESS_KEYS}
        if not (out["line"] and out["city"] and out["province"]):
            raise ApiError("no_home_address", C.MSG_NO_HOME_ADDRESS, status=422)
        return out
    if not isinstance(posted, dict) or set(posted) - set(ADDRESS_KEYS):
        raise _invalid({"delivery_address": "Enter the delivery address."}, "invalid_address")
    out = {k: str(posted.get(k) or "").strip() for k in ADDRESS_KEYS}
    errors = {}
    for key, message in (
        ("line", "Enter the street address."), ("barangay", "Enter the barangay."),
        ("city", "Enter the city or municipality."), ("province", "Enter the province."),
    ):
        if not out[key]:
            errors[key] = message
    if any(len(value) > ADDRESS_MAX for value in out.values()):
        errors["delivery_address"] = "Enter the delivery address."
    if errors:
        raise _invalid(errors, "invalid_address")
    from apps.pack import address_zip  # the ZIP dataset (read-only)

    derived = address_zip.derive_address_fields(out["province"], out["city"], out["barangay"], out["zip"])
    if not derived.ok:
        raise _invalid({derived.error_field or "zip": derived.error_message}, "invalid_address")
    out["zip"] = derived.zip_code
    return out


# -- placement ------------------------------------------------------------------------------------

def _unavailable():
    return ApiError("product_unavailable", C.MSG_UNAVAILABLE, status=409)


def _priced(site, wanted, buyer, mode, address):
    """Lock and copy every product, then assemble. Inside the placing transaction only."""
    snaps = []
    for product_id, qty in wanted:
        try:
            snaps.append(snapshot.take(site, product_id, qty))
        except (snapshot.ProductNotFound, snapshot.ProductUnavailable):
            raise _unavailable() from None
    rows = {p.public_id: p for p in CatalogProduct.objects.filter(public_id__in=[s.product_id for s in snaps])}
    lines = [quote.line_of_snapshot(snap, rows[snap.product_id]) for snap in snaps]
    try:
        return quote.assemble(lines, buyer, mode, address)
    except shipping.ShippingUnavailable as exc:
        raise ApiError("shipping_unavailable", str(exc), status=422) from None


def place(site, *, lines, member_token="", ship_mode="", address=None, receiver=None, fingerprint="",
          submit_key="", data=None, upload=None, ip=None):
    """Place one order. Returns Placed. Raises ApiError: 401 member session, 409 prices changed or
    product unavailable, 422 for an invalid field, line or proof."""
    key = idempotency_key(submit_key)
    first = _replay(site, key)
    if first is not None:
        return Placed(first, "", True)
    if catalog.catalog_key_for(site) == "":
        raise _unavailable()
    wanted = quote.clean_lines(lines)
    buyer = quote.resolve_buyer(site, member_token)
    mode = quote.resolve_mode(buyer, ship_mode, strict=True)
    name, mobile, email = clean_receiver(buyer, receiver or {})
    delivery = clean_address(buyer, mode, address)
    proof = proofs.clean(site, data or {}, upload)  # before any file is stored
    file_key = _store(site, proof)
    try:
        with transaction.atomic():
            locked = Site.objects.select_for_update().get(pk=site.pk)
            again = _replay(locked, key)
            if again is not None:
                transaction.on_commit(lambda: private_files.discard(file_key))
                return Placed(again, "", True)
            if locked.deleted_at or locked.operator_hold or not billing.position(locked).storefront_served:
                # The storefront's own refusal, asked again under the lock: a request that passed it
                # and then lost service (sweep, cancellation, due date, operator hold) creates nothing.
                # The stored proof file is discarded by the handler below.
                raise ApiError("not_found", "Not found.", status=404)
            if not payments.any_method(locked) or not payments.active_accounts(locked).filter(
                pk=proof.account.pk
            ).exists():
                raise ApiError("method_unavailable", C.MSG_PAYMENT_UNAVAILABLE, status=409)
            buyer = quote.resolve_buyer(locked, member_token)  # read again under the lock
            mode = quote.resolve_mode(buyer, ship_mode, strict=True)
            priced = _priced(locked, wanted, buyer, mode, delivery)
            if not hmac.compare_digest(str(fingerprint or ""), priced.fingerprint):
                raise ApiError("prices_changed", C.MSG_PRICES_CHANGED, status=409)
            raw = secrets.token_urlsafe(C.VIEW_TOKEN_BYTES)
            ship = priced.shipping
            order = ShopOrder.objects.create(
                site=locked, order_number=_next_number(locked),
                buyer_kind=C.MEMBER if buyer.member is not None else C.GUEST, member=buyer.member,
                ship_mode=mode, credited_member=buyer.member if mode == C.DIFFERENT else None,
                receiver_name=name, receiver_mobile=mobile, contact_email=email,
                delivery_address=delivery, payment_status=C.PENDING_REVIEW,
                subtotal_centavos=priced.subtotal_centavos,
                shipping_charged_centavos=ship.charged_centavos,
                shipping_computed_centavos=ship.computed_centavos, shipping_policy=ship.policy,
                total_centavos=priced.total_centavos, quote_fingerprint=priced.fingerprint,
                idempotency_key=key, view_token_hash=token_hash(raw),
            )
            for position, (line, charged) in enumerate(priced.lines, start=1):
                ShopOrderLine.objects.create(
                    order=order, position=position, product_id=line.product_id, code=line.code,
                    name=line.name, revision=line.revision, srp_centavos=line.srp_centavos,
                    earnings_rate=line.earnings_rate, quantity=line.quantity,
                    line_srp_total_centavos=line.line_srp_total_centavos,
                    line_earnings_centavos=line.line_earnings_centavos,
                    line_members_total_centavos=line.line_members_total_centavos,
                    line_charged_centavos=charged,
                )
            order.add_event(
                C.EVENT_PLACED, actor_type="buyer",
                actor_ref=buyer.member.pk if buyer.member is not None else None,
            )
            proofs.write(order, proof, file_key, ip=ip)
            notices.after_commit(notices.order_placed, order.pk)
            notices.after_commit(notices.new_order, order.pk)
            notices.after_commit(notices.review_needed, order.pk)
    except BaseException:
        private_files.discard(file_key)
        raise
    return Placed(order, raw, False)


def _store(site, proof):
    """The proof file, stored before the transaction. A store that cannot take it refuses the order."""
    try:
        return private_files.save(C.PROOF_FOLDER.format(site.pk), proof.content, proof.content_type)
    except private_files.StoreError:
        raise ApiError("store_unavailable", C.MSG_STORE_UNAVAILABLE, status=503) from None
