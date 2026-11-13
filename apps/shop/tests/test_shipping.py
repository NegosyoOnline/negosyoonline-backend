"""The shipping engine, table-driven from TWCinc's parity cases (test_shipping.py ParityTests,
fixture captured 2026-10-29), in centavos."""
from decimal import Decimal

from django.test import SimpleTestCase, override_settings

from apps.shop import constants as C, shipping, zones


def item(weight, slot, sqty, qty):
    return shipping.ShippingItem(Decimal(weight), Decimal(slot), sqty, qty)


class ParityTests(SimpleTestCase):
    CASES = [  # (items (weight, slot, shipping qty, quantity), (city, province) or None, fee in pesos)
        ([("0.25", "1", 1, 1)], None, 65),
        ([("0.25", "1", 1, 3)], ("Batangas City", "Batangas"), 125),
        ([("0.50", "2", 1, 3)], ("Cebu City", "Cebu"), 155),
        ([("0.50", "2", 1, 3)], ("Davao City", "Davao del Sur"), 165),
        ([("1.00", "5", 1, 4)], ("Puerto Princesa", "Palawan"), 375),
        ([("1.20", "10", 1, 4)], ("Makati", "Metro Manila"), 265),
        ([("2.00", "20", 1, 4)], ("San Fernando", "Pampanga"), 550),
        ([("1.00", "100", 1, 3)], ("Quezon City", "Metro Manila"), 995),
        ([("0.30", "1", 2, 2)], ("Iloilo City", "Iloilo"), 155),
        ([("0.25", "1", 1, 2), ("0.80", "6", 1, 1)], ("Tagum", "Davao del Norte"), 195),
    ]

    def test_fixture_parity(self):
        for lines, dest, pesos in self.CASES:
            items = [item(*line) for line in lines]
            address = None if dest is None else {"city": dest[0], "province": dest[1]}
            zone = zones.dest_zone(address)
            self.assertEqual(shipping.fee_centavos(items, zone), pesos * 100, (lines, dest))

    def test_the_pouch_boundaries_pick_the_smallest_pouch_that_fits(self):
        manila = lambda slot, qty=1: shipping.fee_centavos([item("0.1", slot, 1, qty)], "MANILA")  # noqa: E731
        self.assertEqual(manila("2.4"), 500 + 6000)  # PS
        self.assertEqual(manila("2.5"), 500 + 8000)  # PM
        self.assertEqual(manila("15"), 500 + 10000)  # PL

    def test_a_box_is_charged_the_minimum_weight_when_lighter(self):
        # BXS holds up to 24 slots, minimum 3 kg: the 3.00 kg Manila rate (155) plus the box fee (30).
        self.assertEqual(shipping.fee_centavos([item("0.1", "16", 1, 1)], "MANILA"), 500 + 15500 + 3000)

    def test_a_heavy_box_uses_its_real_weight(self):
        # 6 kg in a BXS: the 6.00 kg Luzon rate (375) plus the box fee (30) and valuation (5).
        self.assertEqual(shipping.fee_centavos([item("6.00", "20", 1, 1)], "LUZON"), 500 + 37500 + 3000)

    def test_above_the_largest_box_splits_into_boxes(self):
        # 300 slots: two BL boxes (250 slots max each); 40 kg split in two 20 kg boxes: 2 x (815 + 80) + 5.
        fee = shipping.fee_centavos([item("0.4", "3", 1, 100)], "MANILA")
        self.assertEqual(fee, 500 + 2 * (81500 + 8000))

    def test_over_the_rate_table_cannot_be_priced(self):
        with self.assertRaises(shipping.ShippingUnavailable):
            shipping.fee_centavos([item("30.00", "200", 1, 1)], "MANILA")

    def test_shipping_qty_multiplies_the_units(self):
        one = shipping.fee_centavos([item("0.30", "1", 1, 4)], "MANILA")
        two = shipping.fee_centavos([item("0.30", "1", 2, 2)], "MANILA")
        self.assertEqual(one, two)

    def test_zones(self):
        self.assertEqual(zones.dest_zone(None), "MANILA")
        self.assertEqual(zones.dest_zone({"city": "", "province": ""}), "MANILA")
        self.assertEqual(zones.dest_zone({"city": "Naga", "province": "Camarines Sur"}), "LUZON")
        self.assertEqual(zones.dest_zone({"city": "Cebu City", "province": "Cebu"}), "VISAYAS")
        self.assertEqual(zones.dest_zone({"city": "Davao City", "province": "Davao del Sur"}), "MINDANAO")
        self.assertEqual(zones.dest_zone({"city": "Coron", "province": "Palawan"}), "ISLAND")
        self.assertEqual(zones.dest_zone({"city": "Makati", "province": "Metro Manila"}), "MANILA")


class PolicyTests(SimpleTestCase):
    def items(self, slot="1", sqty=1, weight="0.25"):
        return [shipping.ShippingItem(Decimal(weight), None if slot is None else Decimal(slot), sqty, 1)]

    @override_settings(SHOP_SHIPPING_POLICY="charge")
    def test_charge_prices_the_order(self):
        q = shipping.quote(self.items(), None)
        self.assertEqual((q.charged_centavos, q.computed_centavos, q.policy, q.estimated), (6500, 6500, "charge", True))

    @override_settings(SHOP_SHIPPING_POLICY="charge")
    def test_charge_refuses_every_missing_or_unusable_input(self):
        bad = [self.items(slot=None), self.items(sqty=None), self.items(slot="0"), self.items(weight="0")]
        for items in bad:
            with self.subTest(items=items), self.assertRaises(shipping.ShippingUnavailable) as caught:
                shipping.quote(items, None)
            self.assertEqual(str(caught.exception), C.MSG_SHIPPING_MISSING)

    @override_settings(SHOP_SHIPPING_POLICY="zero")
    def test_zero_charges_nothing_and_keeps_the_computed_fee(self):
        q = shipping.quote(self.items(), {"city": "Batangas City", "province": "Batangas"})
        self.assertEqual((q.charged_centavos, q.computed_centavos, q.policy, q.estimated), (0, 8500, "zero", False))

    @override_settings(SHOP_SHIPPING_POLICY="zero")
    def test_zero_with_a_missing_input_never_refuses_and_computes_nothing(self):
        q = shipping.quote(self.items(slot=None), None)
        self.assertEqual((q.charged_centavos, q.computed_centavos), (0, None))

    @override_settings(SHOP_SHIPPING_POLICY="nonsense")
    def test_an_unknown_policy_reads_as_zero(self):
        self.assertEqual(shipping.policy(), "zero")

    def test_the_default_is_zero(self):
        from django.conf import settings
        self.assertEqual(settings.SHOP_SHIPPING_POLICY, "zero")
