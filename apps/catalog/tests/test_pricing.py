"""Line money math in whole centavos. Restates TWCinc's test_pricing."""
from decimal import Decimal as D

from django.test import SimpleTestCase

from apps.catalog import pricing

# srp, rate, quantity, line SRP total, line earnings, line members total, unit members price.
# The last row fails under banker's rounding, so it proves half-up.
VECTORS = [
    (9999, "0.3500", 3, 29997, 10499, 19498, 6499),
    (9999, "0.3500", 1, 9999, 3500, 6499, 6499),
    (10000, "0.4000", 1, 10000, 4000, 6000, 6000),
    (5, "0.1000", 1, 5, 1, 4, 4),
]


class PricingTests(SimpleTestCase):
    def test_the_vectors(self):
        for srp, rate, quantity, total, earnings, members, unit in VECTORS:
            with self.subTest(srp=srp, rate=rate, quantity=quantity):
                totals = pricing.line(srp, D(rate), quantity)
                self.assertEqual(
                    (totals.line_srp_total_centavos, totals.line_earnings_centavos,
                     totals.line_members_total_centavos),
                    (total, earnings, members),
                )
                self.assertEqual(pricing.unit_display(srp, D(rate)), unit)

    def test_earnings_and_members_total_always_add_up_to_the_srp_total(self):
        for srp in (1, 5, 100, 9999, 12345, 999_999_999_999):
            for rate in ("0.1000", "0.1234", "0.3333", "0.3500", "0.5000"):
                for quantity in (1, 2, 3, 7, 100):
                    totals = pricing.line(srp, D(rate), quantity)
                    self.assertEqual(totals.line_srp_total_centavos, srp * quantity)
                    self.assertEqual(
                        totals.line_earnings_centavos + totals.line_members_total_centavos,
                        totals.line_srp_total_centavos,
                    )
                    self.assertIsInstance(totals.line_earnings_centavos, int)
                    self.assertIsInstance(totals.line_members_total_centavos, int)

    def test_bad_inputs_raise(self):
        for srp, rate in ((99.99, D("0.35")), (D("9999"), D("0.35")), (True, D("0.35")),
                          ("9999", D("0.35")), (9999, 0.35), (9999, "0.35")):
            with self.subTest(srp=srp, rate=rate), self.assertRaises(TypeError):
                pricing.line(srp, rate, 1)
        for quantity in (0, -1, True, 1.0, "3"):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                pricing.line(100, D("0.1000"), quantity)
