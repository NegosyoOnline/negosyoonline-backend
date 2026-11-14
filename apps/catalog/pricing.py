"""Line money math, in whole centavos. Pure: no database, no settings.

The server decides prices and totals; a browser only shows them. The unit members price is
for display only: a checkout charges the line total.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class LineTotals:
    line_srp_total_centavos: int
    line_earnings_centavos: int
    line_members_total_centavos: int


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check(srp_centavos, earnings_rate, quantity):
    if not _whole(srp_centavos) or not isinstance(earnings_rate, Decimal):
        raise TypeError("srp_centavos must be an int and earnings_rate a Decimal")
    if not _whole(quantity) or quantity < 1:
        raise ValueError("quantity must be an int of 1 or more")


def line(srp_centavos, earnings_rate, quantity):
    """line SRP total = SRP x quantity; line earnings = line SRP total x rate, rounded half-up
    to the centavo; line members total = line SRP total less line earnings."""
    _check(srp_centavos, earnings_rate, quantity)
    srp_total = srp_centavos * quantity
    earnings = int((srp_total * earnings_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return LineTotals(srp_total, earnings, srp_total - earnings)


def unit_display(srp_centavos, earnings_rate):
    """The members price of one unit, for display only."""
    return line(srp_centavos, earnings_rate, 1).line_members_total_centavos


def per_serving(centavos, servings):
    """`centavos` shared over `servings`, rounded half-up to a whole centavo; None when the
    product has no servings value."""
    if servings is None:
        return None
    if not _whole(centavos) or not _whole(servings) or servings < 1:
        raise ValueError("centavos and servings must be ints, servings 1 or more")
    return int((Decimal(centavos) / servings).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
