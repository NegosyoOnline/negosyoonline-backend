"""The shop's customer shipping fee (port of TWCinc's negosyoonline_storefront/shipping.py, the
J&T NCR body), in integer centavos. Pure: no database.

Inputs per product: weight_kg, volume_slot and shipping_qty (packed dimensions are not inputs:
TWCinc requires them but never uses them in the fee). A product without them cannot be priced.
Policy (settings.SHOP_SHIPPING_POLICY): `charge` refuses an order with such a product; `zero`
charges nothing and keeps the computed fee when there is one."""
from dataclasses import dataclass
from decimal import Decimal
from math import ceil

from django.conf import settings

from apps.shop import constants as C, zones


class ShippingUnavailable(Exception):
    """A product lacks an input, or the load is above the rate table."""


@dataclass(frozen=True)
class ShippingItem:
    weight_kg: Decimal | None
    volume_slot: Decimal | None
    shipping_qty: int | None
    quantity: int


@dataclass(frozen=True)
class ShippingQuote:
    charged_centavos: int
    computed_centavos: int | None
    policy: str
    estimated: bool  # no address yet: the Metro Manila estimate
    zone: str


def policy():
    value = settings.SHOP_SHIPPING_POLICY
    return value if value in (C.CHARGE, C.ZERO) else C.ZERO


def _inputs(item):
    """(weight, slot, shipping qty) per unit, or ShippingUnavailable. Never a guess: a missing
    value, or a weight or slot of 0, refuses."""
    if item.weight_kg is None or item.volume_slot is None or item.shipping_qty is None:
        raise ShippingUnavailable(C.MSG_SHIPPING_MISSING)
    weight, slot, sqty = Decimal(item.weight_kg), Decimal(item.volume_slot), Decimal(item.shipping_qty)
    if weight <= 0 or slot <= 0 or sqty < 1:
        raise ShippingUnavailable(C.MSG_SHIPPING_MISSING)
    return weight, slot, sqty


def _box_rate(weight, zone):
    index = zones.ZONES.index(zone) + 1
    for row in zones.BOX_RATES:
        if weight <= row[0]:
            return row[index]
    raise ShippingUnavailable(C.MSG_SHIPPING_FAILED)  # above the rate table


def fee_centavos(items, zone):
    """The J&T fee in centavos for these items to this zone. Raises ShippingUnavailable."""
    total_weight, total_slots = Decimal(0), Decimal(0)
    for item in items:
        weight, slot, sqty = _inputs(item)
        units = Decimal(item.quantity) * sqty
        total_weight += weight * units
        total_slots += slot * units
    packages = []
    for pkg in zones.PACKAGING_ORDER:
        if total_slots <= zones.SLOT_CAPACITY[pkg]:
            packages.append((pkg, total_weight))
            break
    if not packages:
        boxes = int(ceil(total_slots / zones.SLOT_CAPACITY["BL"])) or 1
        packages = [("BL", total_weight / Decimal(boxes))] * boxes
    fee = zones.VALUATION
    for pkg, weight in packages:
        if pkg in zones.POUCHES:
            fee += zones.POUCH_RATES[pkg][zones.ZONES.index(zone)]
        else:
            chargeable = max(weight, zones.BOX_MIN_KG[pkg])
            fee += _box_rate(chargeable, zone) + zones.BOX_FEE[pkg]
    return fee


def quote(items, address):
    """The fee for this order under the policy. `address` is the destination (or None for the
    estimate). Raises ShippingUnavailable only under `charge`."""
    zone = zones.dest_zone(address)
    pol = policy()
    try:
        computed = fee_centavos(items, zone)
    except ShippingUnavailable:
        if pol == C.CHARGE:
            raise
        computed = None
    charged = computed if pol == C.CHARGE else 0
    return ShippingQuote(charged, computed, pol, address is None, zone)
