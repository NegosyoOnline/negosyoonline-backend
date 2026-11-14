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

    def test_the_verified_date_and_the_waybill_come_through(self):
        body = self.placed()
        stamp = timezone.now()
        ShopOrder.objects.filter(public_id=body["order_id"]).update(
            payment_status="verified", verified_at=stamp, courier="LBC", tracking_no="TRK-123", shipped_on=stamp.date())
        view = self.view(body).json()
        self.assertEqual(view["verified_at"], ShopOrder.objects.get().verified_at.isoformat())
        self.assertEqual((view["courier"], view["tracking_no"]), ("LBC", "TRK-123"))
        self.assertEqual(view["shipping_charged_centavos"], 0)

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
