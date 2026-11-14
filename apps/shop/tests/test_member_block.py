"""M1 (z8uq9mezdg, ruling 1100430000523941): a signed-in Santé member never orders on the web.
The quote and the placement answer 409 `member_use_app`; guests order as before."""
from unittest import mock

from apps.shop import quote as shop_quote
from apps.shop.models import ShopOrder
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.shop.tests.base import CEBU_ADDRESS, HOST, PNG, ShopTestCase

MESSAGE = "Members order through the Santé Mobile App."


class MemberBlockTests(ShopTestCase):
    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 409, response.content)
        error = response.json()["error"]
        self.assertEqual((error["code"], error["message"]), ("member_use_app", MESSAGE))

    def test_a_member_quote_is_refused_with_or_without_benefits(self):
        for benefits in (False, True):
            with self.subTest(benefits=benefits):
                member = self.member(email=f"m{benefits}@buyer.test", mobile=f"0917000000{int(benefits)}",
                                     benefits=benefits)
                self.assert_blocked(self.quote(self.cart(), member_token=self.token(member)))

    def test_a_member_placement_is_refused_and_leaves_nothing_behind(self):
        for benefits in (False, True):
            member = self.member(email=f"m{benefits}@buyer.test", mobile=f"0917000000{int(benefits)}",
                                 benefits=benefits)
            self.assert_blocked(self.place(self.cart(), member_token=self.token(member)))
        self.assertEqual(ShopOrder.objects.count(), 0)
        self.assertEqual(self.proof_files(), [])

    def test_a_tampered_ship_mode_does_not_get_a_member_through(self):
        member = self.member(benefits=True, address=CEBU_ADDRESS)
        token = self.token(member)
        for mode in ("home", "different", "guest", "bogus"):
            with self.subTest(mode=mode):
                self.assert_blocked(self.quote(self.cart(), member_token=token, ship_mode=mode))
                fields = self.order_fields(self.cart(), member_token=token, ship_mode=None, fingerprint="x")
                fields["ship_mode"] = mode
                response = self.client.post(
                    "/storefront/v1/shop/orders/", {"host": HOST, "visitor_ip": "203.0.113.50", **fields},
                    secure=True, **self.headers())
                self.assert_blocked(response)
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_the_refusal_also_holds_inside_the_lock(self):
        """A member whose session resolves as a guest before the lock (a race) is still refused under it."""
        member = self.member(benefits=True)
        token = self.token(member)
        real = shop_quote.resolve_buyer
        calls = []

        def first_guest(site, member_token):
            calls.append(1)
            return shop_quote.GUEST_BUYER if len(calls) == 1 else real(site, member_token)

        fields = self.order_fields(self.cart(), member_token=None)  # a guest's valid fingerprint
        fields["member_token"] = token
        fields.update(host=HOST, visitor_ip="203.0.113.50", proof=SimpleUploadedFile("p.png", PNG, "image/png"))
        with mock.patch("apps.shop.quote.resolve_buyer", side_effect=first_guest):
            response = self.client.post("/storefront/v1/shop/orders/", fields, secure=True, **self.headers())
        self.assert_blocked(response)
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_a_guest_still_quotes_and_places(self):
        self.assertEqual(self.quote(self.cart()).status_code, 200)
        self.placed()
        order = ShopOrder.objects.get()
        self.assertEqual((order.buyer_kind, order.ship_mode, order.member_id), ("guest", "guest", None))

    def test_another_sites_member_is_still_a_dead_session_not_a_refusal(self):
        other = self.member(email="x@buyer.test", mobile="09170000003", site=self.other_site)
        self.assertEqual(self.quote(self.cart(), member_token=self.token(other)).status_code, 401)
        self.assertEqual(self.place(self.cart(), member_token=self.token(other)).status_code, 401)
