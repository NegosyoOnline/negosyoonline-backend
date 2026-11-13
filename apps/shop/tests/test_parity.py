"""SHOP-1 parity with TWCinc (z8uq9mek5v): the member's own address in the quote, its fallback to the
Membership Package order's address, and the buyer view's extra fields (unit price, image, member
savings, receiver phone and email, verified date, waybill)."""
import json

from django.test import override_settings
from django.utils import timezone

from apps.catalog.models import CatalogProduct
from apps.pack.models import PackOrder
from apps.shop.models import ShopOrder
from apps.shop.tests.base import CEBU_ADDRESS, HOST, MANILA_ADDRESS, ShopTestCase

HOME = {"line": "9 Ayala Ave", "barangay": "Poblacion", "city": "Makati", "province": "Metro Manila",
        "zip": "1200", "landmark": "Near the park"}
PACK = {"line": "7 Rizal St", "barangay": "Lahug", "city": "Cebu City", "province": "Cebu", "zip": "6000",
        "landmark": "", "country": "Philippines", "receiver_name": "Maria Santos",
        "receiver_mobile": "+639171234567"}
SIX = {"line", "barangay", "city", "province", "zip", "landmark"}


def set_pack_address(member, address):
    PackOrder.objects.filter(member=member).update(delivery_address=address)


class HomeAddressInQuoteTests(ShopTestCase):
    def home_quote(self, token, **fields):
        return self.quote(self.cart(), member_token=token, **fields).json()

    def test_a_member_with_benefits_sees_their_saved_address_read_only(self):
        member = self.member(benefits=True, address=HOME)
        body = self.home_quote(self.token(member))
        self.assertEqual(body["home_address"], HOME)
        self.assertEqual(body["home_address_source"], "profile")

    def test_a_guest_never_gets_an_address(self):
        body = self.quote(self.cart()).json()
        self.assertIsNone(body["home_address"])
        self.assertEqual(body["home_address_source"], "")

    def test_a_member_without_benefits_gets_no_address(self):
        member = self.member(address=HOME)
        body = self.home_quote(self.token(member))
        self.assertIsNone(body["home_address"])
        self.assertNotIn("Ayala", json.dumps(body))

    def test_a_member_only_ever_gets_their_own_address(self):
        mine = self.member(benefits=True, address=HOME)
        theirs = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz",
                             benefits=True, address=CEBU_ADDRESS)
        body = self.home_quote(self.token(mine))
        self.assertEqual(body["home_address"]["city"], "Makati")
        self.assertNotIn("Osmena", json.dumps(body))
        # a token of another site's member is a dead session, not a leak
        other = self.member(email="x@buyer.test", mobile="09170000003", site=self.other_site, address=HOME)
        self.assertEqual(self.quote(self.cart(), member_token=self.token(other)).status_code, 401)
        self.assertEqual(self.home_quote(self.token(theirs))["home_address"]["city"], "Cebu City")

    def test_the_address_is_the_six_keys_only(self):
        member = self.member(benefits=True, address=HOME)
        type(member).objects.filter(pk=member.pk).update(
            delivery_address={**HOME, "receiver_name": "Zed", "secret": "x"})
        self.assertEqual(set(self.home_quote(self.token(member))["home_address"]), SIX)


class HomeAddressFallbackTests(ShopTestCase):
    def test_without_a_saved_address_the_pack_orders_is_used_for_showing(self):
        member = self.member(benefits=True)
        set_pack_address(member, PACK)
        body = self.quote(self.cart(), member_token=self.token(member)).json()
        self.assertEqual(body["home_address"]["line"], "7 Rizal St")
        self.assertEqual(body["home_address_source"], "pack_order")
        self.assertEqual(set(body["home_address"]), SIX)

    def test_the_saved_address_wins_over_the_pack_orders(self):
        member = self.member(benefits=True, address=HOME)
        set_pack_address(member, PACK)
        body = self.quote(self.cart(), member_token=self.token(member)).json()
        self.assertEqual((body["home_address"]["city"], body["home_address_source"]), ("Makati", "profile"))

    def test_an_unusable_saved_address_falls_back(self):
        member = self.member(benefits=True)
        type(member).objects.filter(pk=member.pk).update(delivery_address={"line": "only a line"})
        set_pack_address(member, PACK)
        body = self.quote(self.cart(), member_token=self.token(member)).json()
        self.assertEqual(body["home_address_source"], "pack_order")

    def test_nothing_anywhere_is_no_address(self):
        member = self.member(benefits=True)
        body = self.quote(self.cart(), member_token=self.token(member)).json()
        self.assertEqual((body["home_address"], body["home_address_source"]), (None, ""))

    def test_another_members_pack_address_is_never_used(self):
        member = self.member(benefits=True)
        other = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz", benefits=True)
        set_pack_address(other, PACK)
        body = self.quote(self.cart(), member_token=self.token(member)).json()
        self.assertIsNone(body["home_address"])

    def test_placing_a_home_order_uses_the_pack_orders_address(self):
        member = self.member(benefits=True)
        set_pack_address(member, PACK)
        self.placed(member_token=self.token(member), ship_mode="home", address=None,
                    receiver_name=None, receiver_mobile=None, contact_email=None)
        order = ShopOrder.objects.get()
        self.assertEqual((order.ship_mode, order.delivery_address["line"], order.delivery_address["city"]),
                         ("home", "7 Rizal St", "Cebu City"))
        self.assertEqual(set(order.delivery_address), SIX)

    @override_settings(SHOP_SHIPPING_POLICY="charge")
    def test_the_home_quote_prices_shipping_to_the_fallback_address(self):
        member = self.member(benefits=True)
        set_pack_address(member, PACK)  # Cebu
        body = self.quote(self.cart(), member_token=self.token(member), ship_mode="home").json()
        self.assertFalse(body["shipping"]["estimated"])
        self.assertEqual(body["shipping"]["charged_centavos"], 500 + 9000)

    def test_home_with_no_address_anywhere_is_still_refused(self):
        member = self.member(benefits=True)
        response = self.place(self.cart(), member_token=self.token(member), ship_mode="home", address=None)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "no_home_address"))


@override_settings(PUBLIC_MEDIA_BASE_URL="https://media.example.test/public")
class BuyerViewExtraTests(ShopTestCase):
    def view(self, body, host=HOST):
        return self.post(f"shop/orders/{body['order_id']}/view/", host=host, view_token=body["view_token"])

    def test_a_guest_order_carries_unit_price_image_receiver_and_no_savings(self):
        CatalogProduct.objects.filter(pk=self.a.pk).update(image_key="sante/products/a.png")
        body = self.placed(self.cart(2, 1))
        view = self.view(body).json()
        first, second = view["lines"]
        self.assertEqual((first["unit_centavos"], first["line_charged_centavos"], first["line_srp_total_centavos"]),
                         (10000, 20000, 20000))
        self.assertEqual(first["image_url"], "https://media.example.test/public/sante/products/a.png")
        self.assertIsNone(second["image_url"])  # no photo
        self.assertEqual(second["unit_centavos"], 25050)
        self.assertEqual((view["receiver_mobile"], view["receiver_email"]), ("+639171234567", "buyer@example.test"))
        self.assertEqual(view["member_savings_centavos"], 0)
        self.assertEqual(view["srp_subtotal_centavos"], view["subtotal_centavos"])
        self.assertIsNone(view["verified_at"])

    def test_a_member_home_order_shows_savings_and_the_srp_subtotal(self):
        member = self.member(benefits=True, address=HOME)
        body = self.placed(self.cart(1, 1), member_token=self.token(member), ship_mode="home", address=None,
                           receiver_name=None, receiver_mobile=None, contact_email=None)
        view = self.view(body).json()
        self.assertEqual(view["subtotal_centavos"], 6500 + 22545)
        self.assertEqual(view["member_savings_centavos"], 3500 + 2505)
        self.assertEqual(view["srp_subtotal_centavos"], 10000 + 25050)
        self.assertEqual([l["unit_centavos"] for l in view["lines"]], [10000, 25050])
        self.assertEqual(view["receiver_mobile"], "+639171234567")
        self.assertEqual(view["receiver_email"], "")  # none typed

    def test_a_member_different_address_order_has_no_savings(self):
        member = self.member(benefits=True)
        body = self.placed(self.cart(1), member_token=self.token(member), ship_mode="different",
                           address=CEBU_ADDRESS)
        view = self.view(body).json()
        self.assertEqual((view["member_savings_centavos"], view["srp_subtotal_centavos"]), (0, 10000))

    def test_the_verified_date_and_the_waybill_come_through(self):
        body = self.placed()
        stamp = timezone.now()
        ShopOrder.objects.filter(public_id=body["order_id"]).update(
            payment_status="verified", verified_at=stamp, courier="LBC", tracking_no="TRK-123", shipped_on=stamp.date())
        view = self.view(body).json()
        self.assertEqual(view["verified_at"], ShopOrder.objects.get().verified_at.isoformat())
        self.assertEqual((view["courier"], view["tracking_no"]), ("LBC", "TRK-123"))
        self.assertEqual(view["shipping_charged_centavos"], 0)

    def test_my_orders_cards_carry_the_same_fields_for_the_owner_of_the_order_only(self):
        CatalogProduct.objects.filter(pk=self.a.pk).update(image_key="sante/products/a.png")
        mine = self.member(benefits=True, address=HOME)
        theirs = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz", benefits=True)
        self.placed(self.cart(1), member_token=self.token(mine), ship_mode="home", address=None,
                    receiver_name=None, receiver_mobile=None, contact_email=None)
        self.placed(self.cart(1), member_token=self.token(theirs), ship_mode="different", address=MANILA_ADDRESS,
                    receiver_mobile="09179999999", contact_email="juan-private@example.test")
        cards = self.post("member/shop-orders/", token=self.token(mine)).json()["cards"]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["lines"][0]["image_url"], "https://media.example.test/public/sante/products/a.png")
        self.assertEqual(card["member_savings_centavos"], 3500)
        self.assertEqual(card["receiver_mobile"], "+639171234567")
        text = json.dumps(cards)
        self.assertNotIn("juan-private", text)
        self.assertNotIn("9179999999", text)

    def test_a_guest_or_another_orders_token_never_gets_the_fields(self):
        mine = self.placed()
        other = self.placed()
        wrong = self.post(f"shop/orders/{mine['order_id']}/view/", view_token=other["view_token"])
        self.assertEqual(wrong.status_code, 404)
        self.assertNotIn("receiver_mobile", wrong.json())
        self.assertNotIn("buyer@example.test", wrong.content.decode())
        listing = self.post("member/shop-orders/", token="")
        self.assertEqual(listing.status_code, 401)
        self.assertNotIn("receiver_email", listing.content.decode())
        # another site's host cannot open it
        self.assertEqual(self.view(mine, host="bens-store.negosyotest.com").status_code, 404)

    def test_no_media_host_means_no_image_never_a_private_key(self):
        with override_settings(PUBLIC_MEDIA_BASE_URL=""):
            CatalogProduct.objects.filter(pk=self.a.pk).update(image_key="sante/products/a.png")
            view = self.view(self.placed()).json()
        self.assertIsNone(view["lines"][0]["image_url"])
        self.assertNotIn("sante/products", json.dumps(view))
