"""The cart quote and the pricing rules shared with placement. The server alone decides prices:
the browser sends product ids, quantities, a ship mode and an address, never a price.

A quote (`quote`) reads active products and the pricing math without locks and is for display.
Placement re-prices from the locked product rows through `assemble`, the one function that works
out the charged lines, the shipping, the total and the fingerprint, so the two cannot disagree.
The fingerprint covers the product ids, revisions, quantities, ship mode, charged shipping and
total; placement refuses a mismatch ("Prices changed. Review your order.")."""
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from apps.catalog import pricing, services as catalog
from apps.catalog.models import CatalogProduct
from apps.common.errors import ApiError
from apps.members import sessions
from apps.shop import constants as C, shipping


@dataclass(frozen=True)
class Buyer:
    member: object  # members.Member or None
    benefits: bool  # a member with active benefits (apps.pack.interfaces.benefits_active)


GUEST_BUYER = Buyer(None, False)


@dataclass(frozen=True)
class Line:
    """One priced line. The shipping inputs ride along; they are never shown or stored."""

    product_id: str
    code: str
    name: str
    revision: int
    srp_centavos: int
    earnings_rate: Decimal
    quantity: int
    line_srp_total_centavos: int
    line_earnings_centavos: int
    line_members_total_centavos: int
    weight_kg: Decimal | None
    volume_slot: Decimal | None
    shipping_qty: int | None


@dataclass(frozen=True)
class Assembled:
    lines: list  # of (Line, charged centavos)
    ship_mode: str
    subtotal_centavos: int
    shipping: object  # shipping.ShippingQuote
    total_centavos: int
    fingerprint: str


# -- the buyer ------------------------------------------------------------------------------------

def resolve_buyer(site, member_token):
    """The buyer of this request. No token: a guest. A token: the member of a live session of this
    site, else 401 `member_session_invalid`. A member without active benefits buys as a guest
    (but the order is still theirs). Benefits are read only while the pack is on."""
    if not (member_token or "").strip():
        return GUEST_BUYER
    session = sessions.find_session(site, member_token) if settings.MEMBERS_ENABLED else None
    if session is None:
        raise ApiError("member_session_invalid", C.MSG_SESSION, status=401)
    member = session.member
    benefits = False
    if settings.PACK_ENABLED:
        from apps.pack import interfaces  # read-only hand-off: the one member-price test

        benefits = interfaces.benefits_active(member)
    return Buyer(member, benefits)


def resolve_mode(buyer, ship_mode, *, strict):
    """The ship mode of this order. A buyer without benefits is `guest` and may not ask for the
    member modes. A member with benefits ships to their own saved address only (`home`): a
    request for `different` or `guest` is refused (`member_own_address_only`); left out, a quote
    shows `home` and a placement refuses (`strict`)."""
    ship_mode = (ship_mode or "").strip()
    if not buyer.benefits:
        if ship_mode in ("", C.GUEST):
            return C.GUEST
        raise ApiError(
            "ship_mode_not_allowed", C.MSG_SHIP_MODE_NOT_ALLOWED, status=422,
            fields={"ship_mode": [C.MSG_SHIP_MODE_NOT_ALLOWED]},
        )
    if ship_mode == C.HOME:
        return ship_mode
    if ship_mode in (C.DIFFERENT, C.GUEST):
        raise ApiError(
            "member_own_address_only", C.MSG_MEMBER_OWN_ADDRESS_ONLY, status=422,
            fields={"ship_mode": [C.MSG_MEMBER_OWN_ADDRESS_ONLY]},
        )
    if ship_mode == "" and not strict:
        return C.HOME
    raise ApiError(
        "ship_mode_required", C.MSG_SHIP_MODE, status=422, fields={"ship_mode": [C.MSG_SHIP_MODE]}
    )


# -- the cart's lines -----------------------------------------------------------------------------

def clean_lines(raw):
    """[(product id, quantity)] with a repeated product merged, in the order first seen. 422 for
    an empty cart, more than 20 products, or a quantity outside 1 to 99 (also after merging)."""
    merged = {}
    for item in raw or []:
        product_id = str((item or {}).get("id") or "").strip()
        qty = (item or {}).get("qty")
        if not product_id or len(product_id) > 40 or isinstance(qty, bool) or not isinstance(qty, int):
            raise ApiError("invalid_line", C.MSG_BAD_LINE, status=422)
        merged[product_id] = merged.get(product_id, 0) + qty
    if not merged:
        raise ApiError("empty_cart", C.MSG_EMPTY_CART, status=422)
    if len(merged) > C.MAX_LINES:
        raise ApiError("too_many_lines", C.MSG_TOO_MANY_LINES, status=422)
    if any(not C.QTY_MIN <= qty <= C.QTY_MAX for qty in merged.values()):
        raise ApiError("invalid_quantity", C.MSG_BAD_QTY, status=422)
    return list(merged.items())


def line_of_product(product, quantity):
    totals = pricing.line(product.srp_centavos, product.earnings_rate, quantity)
    return Line(
        product.public_id, product.code, product.name, product.revision, product.srp_centavos,
        product.earnings_rate, quantity, totals.line_srp_total_centavos,
        totals.line_earnings_centavos, totals.line_members_total_centavos, product.weight_kg,
        product.volume_slot, product.shipping_qty,
    )


def line_of_snapshot(snap, product):
    """A placement line: the locked snapshot's money, and the product's shipping inputs (the
    product row is locked by the snapshot in the same transaction)."""
    return Line(
        snap.product_id, snap.code, snap.name, snap.revision, snap.srp_centavos, snap.earnings_rate,
        snap.quantity, snap.line_srp_total_centavos, snap.line_earnings_centavos,
        snap.line_members_total_centavos, product.weight_kg, product.volume_slot,
        product.shipping_qty,
    )


def charged_centavos(line, buyer, ship_mode):
    """What the buyer pays for the line: the members total for a member with benefits shipping to
    their own address, else the SRP total. """
    if buyer.benefits and ship_mode == C.HOME:
        return line.line_members_total_centavos
    return line.line_srp_total_centavos


def fingerprint_of(lines, ship_mode, shipping_charged, total):
    payload = [
        [[line.product_id, line.revision, line.quantity] for line, _ in lines],
        ship_mode, shipping_charged, total,
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def assemble(lines, buyer, ship_mode, address):
    """The charged lines, shipping, total and fingerprint. Raises shipping.ShippingUnavailable
    under the `charge` policy when a product lacks a shipping input."""
    charged = [(line, charged_centavos(line, buyer, ship_mode)) for line in lines]
    subtotal = sum(amount for _, amount in charged)
    items = [
        shipping.ShippingItem(l.weight_kg, l.volume_slot, l.shipping_qty, l.quantity) for l in lines
    ]
    ship = shipping.quote(items, address)
    total = subtotal + ship.charged_centavos
    return Assembled(
        charged, ship_mode, subtotal, ship, total,
        fingerprint_of(charged, ship_mode, ship.charged_centavos, total),
    )


# -- the quote ------------------------------------------------------------------------------------

ADDRESS_KEYS = ("line", "barangay", "city", "province", "zip", "landmark")
HOME_PROFILE, HOME_PACK_ORDER = "profile", "pack_order"


def _usable(raw):
    """The six address keys as trimmed text, or None when line, city or province is missing."""
    if not isinstance(raw, dict):
        return None
    out = {k: str(raw.get(k) or "").strip() for k in ADDRESS_KEYS}
    return out if out["line"] and out["city"] and out["province"] else None


def home_address(member):
    """(address, source) of the member's own delivery address, or (None, "").

    The member's saved `delivery_address` first. When it is missing or unusable, the delivery
    address of the member's latest Membership Package order on this site, as TWCinc does
    (`negosyoonline_sante_storefront/providers.py: pack_address`, registered as the home-address
    fallback and read by `negosyoonline_storefront/addresses.py: home_address`). Used both to show
    the address and to place a `home` order, so the two cannot disagree."""
    saved = _usable(member.delivery_address)
    if saved is not None:
        return saved, HOME_PROFILE
    from apps.pack.models import PackOrder  # read-only hand-off

    latest = PackOrder.objects.filter(member=member, site_id=member.site_id).order_by("-pk").first()
    fallback = _usable(latest.delivery_address) if latest is not None else None
    return (fallback, HOME_PACK_ORDER) if fallback is not None else (None, "")


def _address_locked(buyer):
    """True while the signed-in member with benefits cannot change their own address (an active
    recovery hold). False for a guest or a member without benefits, who see no Edit anyway."""
    if not buyer.benefits:
        return False
    from apps.members import holds  # read-only hand-off

    return holds.active_hold(buyer.member, "address_change") is not None


def _quote_address(buyer, ship_mode, posted):
    """Where the quote ships: the member's own address for `home`, else the posted one, else
    None (the Metro Manila estimate). Only the city and province are read; a quote validates
    nothing else."""
    source = home_address(buyer.member)[0] if ship_mode == C.HOME else posted
    if not isinstance(source, dict):
        return None
    address = {k: str(source.get(k) or "").strip() for k in ("city", "province")}
    return address if any(address.values()) else None


def quote(site, raw_lines, *, member_token="", ship_mode="", address=None):
    """The priced cart as a plain dict for the storefronts server. Per-line problems and a
    shipping problem are answered in the dict (`ok` False, no fingerprint), not raised."""
    wanted = clean_lines(raw_lines)
    buyer = resolve_buyer(site, member_token)
    mode = resolve_mode(buyer, ship_mode, strict=False)
    key = catalog.catalog_key_for(site)
    found = {
        p.public_id: p
        for p in CatalogProduct.objects.filter(
            public_id__in=[pid for pid, _ in wanted], catalog__template_key=key, is_active=True
        )
    } if key else {}
    priced, rows = [], []
    for product_id, qty in wanted:
        product = found.get(product_id)
        if product is None:
            rows.append({
                "id": product_id, "code": "", "name": "", "quantity": qty, "available": False,
                "error": C.MSG_UNAVAILABLE, "unit_srp_centavos": 0, "unit_members_price_centavos": None,
                "line_total_centavos": 0,
            })
            continue
        line = line_of_product(product, qty)
        priced.append(line)
        rows.append({
            "id": line.product_id, "code": line.code, "name": line.name, "quantity": qty,
            "available": True, "error": "", "unit_srp_centavos": line.srp_centavos,
            "unit_members_price_centavos": (
                pricing.unit_display(line.srp_centavos, line.earnings_rate) if buyer.benefits else None
            ),
            "line_total_centavos": charged_centavos(line, buyer, mode),
        })
    errors = []
    ship_error = ""
    assembled = None
    try:
        assembled = assemble(priced, buyer, mode, _quote_address(buyer, mode, address))
    except shipping.ShippingUnavailable as exc:
        ship_error = str(exc)
        errors.append(ship_error)
    if len(priced) < len(wanted):
        errors.append(C.MSG_UNAVAILABLE)
    subtotal = sum(row["line_total_centavos"] for row in rows)
    ship = assembled.shipping if assembled else None
    ok = assembled is not None and len(priced) == len(wanted)
    shipping_charged = ship.charged_centavos if ship else 0
    # The member's own address, read-only, for the "Ship to my address" step: only a signed-in
    # member with active benefits (the buyers who may choose `home`). Never a guest's or another
    # member's: it comes from this request's own verified session.
    home, home_source = home_address(buyer.member) if buyer.benefits else (None, "")
    return {
        "ok": ok,
        "home_address": home,
        "home_address_source": home_source,
        "home_address_locked": _address_locked(buyer),
        "member_price": buyer.benefits,
        "ship_mode": mode,
        "lines": rows,
        "subtotal_centavos": subtotal,
        "shipping": {
            "charged_centavos": shipping_charged,
            "policy": shipping.policy(),
            "estimated": True if ship is None else ship.estimated,
            "available": not ship_error,
            "error": ship_error,
            "note": C.MSG_STORE_PAYS_SHIPPING if shipping.policy() == C.ZERO else "",
        },
        "total_centavos": subtotal + shipping_charged,
        "fingerprint": assembled.fingerprint if ok else "",
        "errors": errors,
        "currency": C.CURRENCY,
    }
