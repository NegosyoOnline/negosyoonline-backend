"""The cart quote: prices decided by the server, who gets the member price, limits, the switch."""
from django.test import override_settings

from apps.shop import constants as C
from apps.shop.tests.base import CEBU_ADDRESS, HOST, NOT_FOUND, SECRET, ShopTestCase


class QuoteTests(ShopTestCase):
    def test_a_guest_pays_srp_and_sees_no_member_price(self):
        body = self.quote(self.cart(2, 1)).json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["member_price"])
        self.assertEqual(body["ship_mode"], "guest")
        self.assertEqual([l["line_total_centavos"] for l in body["lines"]], [20000, 25050])
        self.assertEqual(body["subtotal_centavos"], 45050)
        self.assertEqual(body["total_centavos"], 45050)
        self.assertTrue(all(l["unit_members_price_centavos"] is None for l in body["lines"]))
        self.assertTrue(body["fingerprint"])
        self.assertNotIn("earnings", str(body))

    def test_zero_policy_charges_no_shipping_and_says_so(self):
        body = self.quote(self.cart()).json()
        self.assertEqual(body["shipping"]["charged_centavos"], 0)
        self.assertEqual(body["shipping"]["note"], "This store pays your shipping.")
        self.assertEqual(body["shipping"]["policy"], "zero")
        self.assertTrue(body["shipping"]["estimated"])

    @override_settings(SHOP_SHIPPING_POLICY="charge")
    def test_charge_policy_prices_shipping_by_zone(self):
        manila = self.quote(self.cart(), ship_mode="", address={"city": "Quezon City", "province": "Metro Manila"}).json()
        cebu = self.quote(self.cart(), address=CEBU_ADDRESS).json()
        self.assertEqual(manila["shipping"]["charged_centavos"], 6500)
        self.assertFalse(manila["shipping"]["estimated"])
        self.assertEqual(cebu["shipping"]["charged_centavos"], 500 + 9000)
        self.assertEqual(cebu["total_centavos"], 10000 + 9500)
        self.assertNotEqual(manila["fingerprint"], cebu["fingerprint"])
        self.assertEqual(manila["shipping"]["note"], "")

    @override_settings(SHOP_SHIPPING_POLICY="charge")
    def test_charge_policy_with_a_missing_input_refuses_in_the_quote(self):
        missing = self.product("FX-C", 5000, "0.2000", slot=None)
        body = self.quote([{"id": missing.public_id, "qty": 1}]).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["fingerprint"], "")
        self.assertEqual(body["shipping"]["error"], "Shipping is not available for this product yet.")
        self.assertIn("Shipping is not available for this product yet.", body["errors"])

    def test_a_signed_in_member_without_benefits_is_a_guest(self):
        member = self.member()
        body = self.quote(self.cart(), member_token=self.token(member)).json()
        self.assertFalse(body["member_price"])
        self.assertEqual(body["lines"][0]["line_total_centavos"], 10000)

    def test_a_member_with_benefits_gets_the_members_total_at_home(self):
        member = self.member(benefits=True, address={"line": "1 St", "city": "Makati", "province": "Metro Manila"})
        token = self.token(member)
        body = self.quote(self.cart(1, 1), member_token=token, ship_mode="home").json()
        self.assertTrue(body["member_price"])
        self.assertEqual([l["line_total_centavos"] for l in body["lines"]], [6500, 22545])
        self.assertEqual(body["subtotal_centavos"], 29045)
        self.assertEqual([l["unit_members_price_centavos"] for l in body["lines"]], [6500, 22545])

    def test_a_member_with_benefits_pays_srp_when_shipping_to_another_address(self):
        member = self.member(benefits=True)
        body = self.quote(self.cart(), member_token=self.token(member), ship_mode="different").json()
        self.assertEqual(body["lines"][0]["line_total_centavos"], 10000)

    def test_a_rejected_pack_payment_keeps_the_member_price(self):
        """Q1 A: the member price stays while the pack proof is rejected or not yet reviewed."""
        member = self.member()
        self.give_benefits(member, status="rejected")
        body = self.quote(self.cart(), member_token=self.token(member), ship_mode="home").json()
        self.assertEqual(body["lines"][0]["line_total_centavos"], 6500)

    def test_the_member_price_needs_the_pack_to_be_on(self):
        member = self.member(benefits=True)
        token = self.token(member)
        with override_settings(PACK_ENABLED=False):
            body = self.quote(self.cart(), member_token=token).json()
        self.assertFalse(body["member_price"])
        self.assertEqual(body["lines"][0]["line_total_centavos"], 10000)

    def test_home_or_different_is_refused_for_a_buyer_without_benefits(self):
        member = self.member()
        for mode in ("home", "different"):
            with self.subTest(mode=mode):
                response = self.quote(self.cart(), member_token=self.token(member), ship_mode=mode)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "ship_mode_not_allowed")
        self.assertEqual(self.quote(self.cart(), ship_mode="home").status_code, 422)

    def test_a_member_with_benefits_must_not_pick_guest(self):
        member = self.member(benefits=True)
        response = self.quote(self.cart(), member_token=self.token(member), ship_mode="guest")
        self.assertEqual(response.json()["error"]["code"], "ship_mode_required")

    def test_a_bad_member_token_is_401(self):
        response = self.quote(self.cart(), member_token="nope")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "member_session_invalid")

    def test_another_sites_member_token_is_401(self):
        other = self.member(site=self.other_site)
        self.assertEqual(self.quote(self.cart(), member_token=self.token(other)).status_code, 401)

    def test_an_unknown_or_inactive_product_is_flagged_on_its_line(self):
        dead = self.product("FX-D", 5000, "0.2000", active=False)
        lines = self.cart() + [{"id": dead.public_id, "qty": 1}, {"id": "prod_missing", "qty": 2}]
        body = self.quote(lines).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["fingerprint"], "")
        self.assertEqual([l["available"] for l in body["lines"]], [True, False, False])
        self.assertEqual(body["lines"][1]["error"], "This product is no longer available. Remove it to continue.")
        self.assertEqual(body["subtotal_centavos"], 10000)

    def test_quantity_and_line_limits(self):
        self.assertEqual(self.quote(self.cart(99)).status_code, 200)
        for qty in (0, 100, -1):
            with self.subTest(qty=qty):
                response = self.quote([{"id": self.a.public_id, "qty": qty}])
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "invalid_quantity")
        merged = self.quote([{"id": self.a.public_id, "qty": 60}, {"id": self.a.public_id, "qty": 60}])
        self.assertEqual(merged.json()["error"]["code"], "invalid_quantity")
        many = [{"id": f"prod_{i}", "qty": 1} for i in range(21)]
        self.assertEqual(self.quote(many).json()["error"]["code"], "too_many_lines")
        twenty = self.quote([{"id": f"prod_{i}", "qty": 1} for i in range(20)])
        self.assertEqual(twenty.status_code, 200)

    def test_an_empty_cart_is_refused(self):
        response = self.quote([])
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["message"], "Your cart is empty.")

    def test_a_repeated_product_is_merged(self):
        body = self.quote([{"id": self.a.public_id, "qty": 1}, {"id": self.a.public_id, "qty": 2}]).json()
        self.assertEqual(len(body["lines"]), 1)
        self.assertEqual(body["lines"][0]["quantity"], 3)

    def test_a_price_sent_by_the_browser_changes_nothing(self):
        body = self.post("shop/quote/", lines=self.cart(), price_centavos=1, total_centavos=1,
                         unit_price_centavos=1).json()
        self.assertEqual(body["total_centavos"], 10000)

    def test_the_fingerprint_follows_the_price(self):
        before = self.quote(self.cart()).json()["fingerprint"]
        self.assertEqual(before, self.quote(self.cart()).json()["fingerprint"])
        from apps.catalog.models import CatalogProduct
        CatalogProduct.objects.filter(pk=self.a.pk).update(srp_centavos=12000, revision=2)
        self.assertNotEqual(before, self.quote(self.cart()).json()["fingerprint"])

    def test_another_sites_host_and_non_sante_hosts_are_404(self):
        self.assertEqual(self.quote(self.cart(), host="nowhere.negosyotest.com").status_code, 404)

    def test_the_credential_is_required(self):
        response = self.client.post(
            "/storefront/v1/shop/quote/", {"host": HOST, "lines": self.cart()},
            content_type="application/json", secure=True,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.post("shop/quote/", secret="wrong", lines=self.cart()).status_code, 401)
        self.assertEqual(self.post("shop/quote/", secret=SECRET, lines=self.cart()).status_code, 200)


class SwitchOffTests(ShopTestCase):
    @override_settings(SHOP_ENABLED=False)
    def test_every_storefront_shop_route_is_404_with_the_switch_off(self):
        for path in ("shop/quote/", "shop/orders/", "shop/orders/sho_x/view/", "member/shop-orders/"):
            with self.subTest(path=path):
                response = self.post(path, lines=self.cart(), view_token="x", token="x")
                self.assertEqual(response.status_code, 404, response.content)
                self.assertEqual(response.json(), NOT_FOUND)
        for path in ("shop/payment-accounts/", f"shop/payment-accounts/{self.account.public_id}/qr/"):
            with self.subTest(path=path):
                response = self.client.get(f"/storefront/v1/{path}", {"host": HOST}, secure=True,
                                           **self.headers())
                self.assertEqual(response.status_code, 404)

    @override_settings(SHOP_ENABLED=False)
    def test_no_shop_or_catalog_table_is_read_with_the_switch_off(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.quote(self.cart())
        self.assertFalse([q for q in ctx.captured_queries if "shop_" in q["sql"] or "catalog_" in q["sql"]])

    def test_the_constants_the_page_mirrors(self):
        self.assertEqual((C.QTY_MIN, C.QTY_MAX, C.MAX_LINES), (1, 99, 20))
