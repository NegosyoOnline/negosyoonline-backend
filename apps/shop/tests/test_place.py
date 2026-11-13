"""Placement: guest and member orders, member price only with benefits, fingerprint, replay,
unavailable products, proof validation, atomicity, the buyer's done page and My Orders, emails."""
import json
from unittest import mock

from django.core import mail
from django.test import override_settings

from apps.catalog.models import CatalogProduct
from apps.shop import constants as C
from apps.shop.models import ShopOrder, ShopOrderEvent, ShopOrderLine, ShopProofSubmission
from apps.shop.tests.base import (
    CEBU_ADDRESS, HOST, MANILA_ADDRESS, NOT_FOUND, PDF, PNG, ShopTestCase,
)

HOME = {"line": "9 Ayala Ave", "barangay": "Poblacion", "city": "Makati", "province": "Metro Manila",
        "zip": "1200", "landmark": ""}


class GuestPlacementTests(ShopTestCase):
    def test_a_guest_order_is_priced_stored_and_answered_once_with_a_view_token(self):
        body = self.placed(self.cart(2, 1))
        self.assertEqual(body["order_number"], "SO00001")
        self.assertFalse(body["replay"])
        self.assertTrue(body["view_token"])
        order = ShopOrder.objects.get()
        self.assertEqual(
            (order.buyer_kind, order.ship_mode, order.member_id, order.credited_member_id),
            ("guest", "guest", None, None),
        )
        self.assertEqual(
            (order.subtotal_centavos, order.shipping_charged_centavos, order.total_centavos,
             order.payment_status, order.shipping_policy),
            (45050, 0, 45050, "pending_review", "zero"),
        )
        self.assertEqual(order.delivery_address["zip"], "1100")  # the dataset's, not the buyer's
        self.assertEqual(order.receiver_mobile, "+639171234567")
        self.assertNotEqual(order.view_token_hash, body["view_token"])
        lines = list(ShopOrderLine.objects.filter(order=order))
        self.assertEqual([(l.code, l.quantity, l.line_charged_centavos) for l in lines],
                         [("FX-A", 2, 20000), ("FX-B", 1, 25050)])
        self.assertEqual(lines[0].line_earnings_centavos, 7000)
        self.assertEqual(ShopOrderEvent.objects.get().kind, "placed")
        proof = ShopProofSubmission.objects.get()
        self.assertEqual((proof.reference_number, proof.amount_sent_centavos, proof.account_number_last4),
                         ("REF123", 10000, self.account.account_number[-4:]))
        self.assertEqual(len(self.proof_files()), 1)

    def test_order_numbers_count_per_site(self):
        self.placed()
        self.assertEqual(self.placed()["order_number"], "SO00002")

    def test_the_server_prices_the_order_whatever_the_browser_sends(self):
        body = self.placed(price_centavos="1", total_centavos="1", subtotal_centavos="1",
                           shipping_charged_centavos="1")
        self.assertEqual(ShopOrder.objects.get().total_centavos, 10000)
        self.assertTrue(body["order_id"])

    def test_a_stale_fingerprint_is_refused_and_nothing_is_kept(self):
        response = self.place(self.cart(), fingerprint="0" * 64)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["message"], "Prices changed. Review your order.")
        self.assertEqual(response.json()["error"]["code"], "prices_changed")
        self.assertEqual(ShopOrder.objects.count(), 0)
        self.assertEqual(self.proof_files(), [])

    def test_a_missing_fingerprint_is_refused(self):
        self.assertEqual(self.place(self.cart(), fingerprint="").status_code, 409)

    def test_a_price_change_between_the_quote_and_the_order_is_refused(self):
        fields = self.order_fields(self.cart())
        CatalogProduct.objects.filter(pk=self.a.pk).update(srp_centavos=12000, revision=2)
        from django.core.files.uploadedfile import SimpleUploadedFile
        fields.update(host=HOST, visitor_ip="203.0.113.50", proof=SimpleUploadedFile("p.png", PNG, "image/png"))
        response = self.client.post("/storefront/v1/shop/orders/", fields, secure=True, **self.headers())
        self.assertEqual(response.json()["error"]["code"], "prices_changed")
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_a_replay_returns_the_first_order_and_writes_nothing(self):
        first = self.placed(submit_key="same")
        again = self.place(self.cart(), submit_key="same")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["order_id"], first["order_id"])
        self.assertTrue(again.json()["replay"])
        self.assertEqual(again.json()["view_token"], "")
        self.assertEqual(ShopOrder.objects.count(), 1)
        self.assertEqual(len(self.proof_files()), 1)

    def test_a_missing_submit_key_is_refused(self):
        self.assertEqual(self.place(self.cart(), submit_key="").status_code, 409)

    def test_an_inactive_product_blocks_the_order(self):
        fields = self.cart(1, 1)
        quoted = self.order_fields(fields)
        CatalogProduct.objects.filter(pk=self.b.pk).update(is_active=False)
        response = self.place(fields)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "product_unavailable")
        self.assertEqual(ShopOrder.objects.count(), 0)
        self.assertEqual(self.proof_files(), [])
        self.assertTrue(quoted["fingerprint"])

    def test_an_unknown_product_blocks_the_order(self):
        response = self.place([{"id": "prod_missing", "qty": 1}])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["message"],
                         "This product is no longer available. Remove it to continue.")

    def test_quantity_and_line_limits_hold_at_placement(self):
        for lines in ([{"id": self.a.public_id, "qty": 100}], [{"id": self.a.public_id, "qty": 0}], [],
                      [{"id": f"p{i}", "qty": 1} for i in range(21)]):
            with self.subTest(lines=lines):
                self.assertEqual(self.place(lines).status_code, 422)
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_a_guest_cannot_pick_a_member_ship_mode(self):
        for mode in ("home", "different"):
            with self.subTest(mode=mode):
                self.assertEqual(self.place(self.cart(), ship_mode=mode).status_code, 422)
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_the_address_needs_a_province_the_zip_dataset_knows(self):
        bad = {**MANILA_ADDRESS, "province": "Atlantis"}
        response = self.place(self.cart(), address=bad)
        self.assertEqual(response.status_code, 422)
        self.assertIn("province", response.json()["error"]["fields"])
        missing = {**MANILA_ADDRESS, "barangay": ""}
        self.assertIn("barangay", self.place(self.cart(), address=missing).json()["error"]["fields"])
        self.assertEqual(self.place(self.cart(), address=None).status_code, 422)
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_the_receiver_is_required_for_a_guest(self):
        response = self.place(self.cart(), receiver_name="", receiver_mobile="12")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(sorted(response.json()["error"]["fields"]), ["receiver_mobile", "receiver_name"])
        self.assertIn("contact_email", self.place(self.cart(), contact_email="nope").json()["error"]["fields"])

    @override_settings(SHOP_SHIPPING_POLICY="charge")
    def test_charge_policy_orders_carry_the_shipping(self):
        self.placed(address=CEBU_ADDRESS)
        order = ShopOrder.objects.get()
        self.assertEqual((order.shipping_charged_centavos, order.shipping_computed_centavos, order.total_centavos),
                         (9500, 9500, 19500))

    @override_settings(SHOP_SHIPPING_POLICY="charge")
    def test_charge_policy_refuses_a_product_that_cannot_be_priced(self):
        missing = self.product("FX-C", 5000, "0.2000", slot=None)
        response = self.place([{"id": missing.public_id, "qty": 1}], fingerprint="x")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["message"], "Shipping is not available for this product yet.")

    def test_the_account_must_be_an_active_account_of_the_site(self):
        from apps.payments.tests.base import make_payment_account
        foreign = make_payment_account(self.other_site)
        response = self.place(self.cart(), account_id=foreign.public_id)
        self.assertEqual(response.status_code, 422)
        self.assertIn("account_id", response.json()["error"]["fields"])

    def test_no_payment_method_refuses_the_order(self):
        from apps.payments.models import SitePaymentSettings
        SitePaymentSettings.objects.create(site=self.site, direct_enabled=False)
        response = self.place(self.cart())
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "method_unavailable"))
        self.assertEqual(self.proof_files(), [])
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_the_host_must_be_the_sites_own(self):
        self.assertEqual(self.place(self.cart(), host="nowhere.negosyotest.com").status_code, 404)
        self.assertEqual(self.proof_files(), [])


class ProofTests(ShopTestCase):
    def test_png_and_pdf_are_accepted(self):
        self.assertEqual(self.place(self.cart()).status_code, 200)
        self.assertEqual(self.place(self.cart(), content=PDF, name="p.pdf", declared="application/pdf").status_code, 200)
        self.assertEqual(sorted(p.content_type for p in ShopProofSubmission.objects.all()),
                         ["application/pdf", "image/png"])

    def test_the_file_is_judged_by_its_bytes_not_its_name_or_declared_type(self):
        response = self.place(self.cart(), content=b"<html>not an image</html>", name="proof.png", declared="image/png")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["fields"]["proof"], ["Upload a JPEG, PNG, WebP or PDF file."])
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_the_file_is_required(self):
        response = self.place(self.cart(), content=None)
        self.assertEqual(response.json()["error"]["fields"]["proof"], ["Attach the file."])

    def test_the_cap_is_4_mb(self):
        big = PNG + b"0" * (4 * 1024 * 1024)
        response = self.place(self.cart(), content=big)
        self.assertEqual(response.status_code, 422)
        self.assertIn("4 MB", response.json()["error"]["fields"]["proof"][0])
        self.assertEqual(self.proof_files(), [])

    def test_reference_amount_and_account_are_validated(self):
        for field, value in (("reference_number", ""), ("reference_number", "x" * 65), ("amount_sent", "0"),
                             ("amount_sent", "abc"), ("amount_sent", "1.234"), ("amount_sent", "100000"),
                             ("account_id", "pay_missing")):
            with self.subTest(field=field, value=value):
                response = self.place(self.cart(), **{field: value})
                self.assertEqual(response.status_code, 422)
                self.assertIn(field, response.json()["error"]["fields"])
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_the_amount_is_advisory_it_need_not_equal_the_total(self):
        self.placed(amount_sent="1.00")
        self.assertEqual(ShopProofSubmission.objects.get().amount_sent_centavos, 100)
        self.assertEqual(ShopOrder.objects.get().total_centavos, 10000)


class AtomicTests(ShopTestCase):
    def test_a_failure_after_the_file_is_stored_discards_the_file_and_keeps_no_row(self):
        with mock.patch("apps.shop.placement.proofs.write", side_effect=RuntimeError("boom")):
            response = self.place(self.cart())
        self.assertEqual(response.status_code, 500)
        self.assertEqual(ShopOrder.objects.count(), 0)
        self.assertEqual(ShopOrderLine.objects.count(), 0)
        self.assertEqual(ShopOrderEvent.objects.count(), 0)
        self.assertEqual(self.proof_files(), [])

    def test_a_refused_order_leaves_no_file_behind(self):
        self.place(self.cart(), fingerprint="bad")
        self.assertEqual(self.proof_files(), [])


class MemberPlacementTests(ShopTestCase):
    def test_a_member_with_benefits_ships_home_at_the_members_price(self):
        member = self.member(benefits=True, address=HOME)
        token = self.token(member)
        self.placed(self.cart(1, 1), member_token=token, ship_mode="home", address=None,
                    receiver_name=None, receiver_mobile=None, contact_email=None)
        order = ShopOrder.objects.get()
        self.assertEqual((order.buyer_kind, order.ship_mode, order.member_id, order.credited_member_id),
                         ("member", "home", member.pk, None))
        self.assertEqual((order.subtotal_centavos, order.total_centavos), (6500 + 22545, 6500 + 22545))
        self.assertEqual(order.delivery_address["line"], "9 Ayala Ave")  # the member's saved address
        self.assertEqual((order.receiver_name, order.receiver_mobile), ("Maria Santos", "+639171234567"))
        line = ShopOrderLine.objects.first()
        self.assertEqual((line.line_srp_total_centavos, line.line_members_total_centavos, line.line_charged_centavos),
                         (10000, 6500, 6500))

    def test_home_uses_the_servers_address_not_the_posted_one(self):
        member = self.member(benefits=True, address=HOME)
        self.placed(member_token=self.token(member), ship_mode="home", address=CEBU_ADDRESS)
        self.assertEqual(ShopOrder.objects.get().delivery_address["city"], "Makati")

    def test_home_without_a_saved_address_is_refused(self):
        member = self.member(benefits=True)
        response = self.place(self.cart(), member_token=self.token(member), ship_mode="home", address=None)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "no_home_address")

    def test_a_different_address_pays_srp_and_credits_the_member(self):
        member = self.member(benefits=True)
        self.placed(self.cart(2), member_token=self.token(member), ship_mode="different", address=CEBU_ADDRESS)
        order = ShopOrder.objects.get()
        self.assertEqual((order.ship_mode, order.credited_member_id, order.total_centavos),
                         ("different", member.pk, 20000))
        line = ShopOrderLine.objects.get()
        self.assertEqual((line.line_earnings_centavos, line.line_charged_centavos), (7000, 20000))  # recorded, not paid
        self.assertEqual(order.delivery_address["city"], "Cebu City")

    def test_a_member_with_benefits_must_choose_a_mode(self):
        member = self.member(benefits=True, address=HOME)
        response = self.place(self.cart(), member_token=self.token(member), ship_mode=None)
        self.assertEqual(response.json()["error"]["code"], "ship_mode_required")

    def test_a_rejected_pack_payment_still_gets_the_members_price(self):
        member = self.member(address=HOME)
        self.give_benefits(member, status="rejected")
        self.placed(member_token=self.token(member), ship_mode="home", address=None)
        self.assertEqual(ShopOrder.objects.get().total_centavos, 6500)

    def test_a_member_without_benefits_buys_as_a_guest_at_srp_but_the_order_is_theirs(self):
        member = self.member()
        self.placed(member_token=self.token(member))
        order = ShopOrder.objects.get()
        self.assertEqual((order.buyer_kind, order.ship_mode, order.member_id, order.total_centavos),
                         ("member", "guest", member.pk, 10000))

    def test_home_is_refused_without_benefits_at_placement(self):
        member = self.member(address=HOME)
        response = self.place(self.cart(), member_token=self.token(member), ship_mode="home", address=None)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_benefits_lost_between_quote_and_order_change_the_price(self):
        member = self.member(benefits=True, address=HOME)
        token = self.token(member)
        fields = self.order_fields(self.cart(), member_token=token, ship_mode="home", address=None)
        from apps.pack.models import PackOrder
        PackOrder.objects.filter(member=member).delete()
        from django.core.files.uploadedfile import SimpleUploadedFile
        fields.update(host=HOST, visitor_ip="203.0.113.50", proof=SimpleUploadedFile("p.png", PNG, "image/png"))
        response = self.client.post("/storefront/v1/shop/orders/", fields, secure=True, **self.headers())
        self.assertEqual(response.status_code, 422)  # no longer allowed to ship home
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_a_bad_token_places_nothing(self):
        response = self.place(self.cart(), member_token="nope")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.proof_files(), [])


class BuyerViewTests(ShopTestCase):
    def view(self, order_id, token, host=HOST):
        return self.post(f"shop/orders/{order_id}/view/", host=host, view_token=token)

    def test_the_placing_buyer_opens_the_done_page_with_the_token(self):
        body = self.placed()
        response = self.view(body["order_id"], body["view_token"])
        self.assertEqual(response.status_code, 200)
        view = response.json()
        self.assertEqual((view["order_number"], view["status"], view["status_label"]),
                         ("SO00001", "placed", "Payment under review"))
        self.assertEqual(view["total_centavos"], 10000)
        text = json.dumps(view)
        for hidden in ("fingerprint", "view_token", "REF123", "cancel_note", "file_key", "contact_email"):
            self.assertNotIn(hidden, text)

    def test_a_wrong_missing_or_other_orders_token_is_the_same_404(self):
        one, two = self.placed(), self.placed()
        for order_id, token in ((one["order_id"], "nope"), (one["order_id"], ""), (one["order_id"], two["view_token"]),
                                ("sho_missing", one["view_token"])):
            with self.subTest(order_id=order_id, token=token):
                response = self.view(order_id, token)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), NOT_FOUND)

    def test_another_sites_host_cannot_open_the_order(self):
        body = self.placed()
        self.assertEqual(self.view(body["order_id"], body["view_token"], host="bens-store.negosyotest.com").status_code, 404)

    def test_a_member_sees_only_their_own_orders(self):
        mine = self.member()
        theirs = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz")
        self.placed(member_token=self.token(mine))
        self.placed(member_token=self.token(theirs))
        self.placed()  # a guest's
        cards = self.post("member/shop-orders/", token=self.token(mine)).json()["cards"]
        self.assertEqual([c["order_number"] for c in cards], ["SO00001"])
        self.assertEqual(cards[0]["lines"][0]["code"], "FX-A")
        self.assertEqual(self.post("member/shop-orders/", token="nope").status_code, 401)
        self.assertEqual(self.post("member/shop-orders/", token="").status_code, 401)

    def test_payment_accounts_are_listed_for_the_checkout(self):
        response = self.client.get("/storefront/v1/shop/payment-accounts/", {"host": HOST}, secure=True,
                                   **self.headers())
        body = response.json()
        self.assertTrue(body["available"])
        self.assertEqual([a["id"] for a in body["accounts"]], [self.account.public_id])
        self.assertEqual(sorted(body["accounts"][0]), sorted(
            ["id", "kind", "account_type", "provider_name", "account_name", "account_number",
             "account_number_last4", "has_qr"]))


class EmailTests(ShopTestCase):
    def test_placing_emails_the_buyer_and_the_owner_after_commit(self):
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.place(self.cart())
        self.assertEqual(mail.outbox, [])  # nothing is sent before the commit
        self.assertEqual(len(callbacks), 3)
        for callback in callbacks:
            callback()
        by_to = {}
        for m in mail.outbox:
            by_to.setdefault(m.to[0], []).append(m.subject)
        self.assertEqual(by_to["buyer@example.test"], ["Your Ana's Store order SO00001"])
        self.assertEqual(sorted(by_to[self.site.account.email]),
                         ["New order SO00001 for Ana's Store", "Payment proof to review: SO00001"])

    def test_a_guest_without_an_email_gets_no_buyer_email(self):
        mail.outbox.clear()
        self.placed(contact_email=None)
        self.assertNotIn("buyer@example.test", [m.to[0] for m in mail.outbox])

    def test_a_member_without_a_typed_email_is_emailed_at_their_own(self):
        member = self.member()
        mail.outbox.clear()
        self.placed(member_token=self.token(member), contact_email=None)
        self.assertIn("maria@buyer.test", [m.to[0] for m in mail.outbox])

    def test_a_failing_email_never_undoes_the_order(self):
        with mock.patch("apps.shop.notices.send_email", side_effect=RuntimeError("smtp down")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.place(self.cart())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ShopOrder.objects.count(), 1)
