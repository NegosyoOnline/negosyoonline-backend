"""The member Dashboard figures, My Orders cards and the dashboard details form (TWCinc's
dashboard.py shapes over the member's pack order only)."""
from apps.pack import constants as C, review
from apps.pack.models import PackOrder, SanteRegistration
from apps.pack.tests.base import HOST, NOT_FOUND
from apps.pack.tests.test_member_pack import MemberPackCase
from apps.storefront.tests.base import SECRET


class DashboardCase(MemberPackCase):
    def route(self, path, **fields):
        return self.client.post(
            "/storefront/v1/member/" + path, {"host": HOST, "token": self.session, **fields},
            content_type="application/json", secure=True,
            HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def verify(self):
        from apps.pack import registration
        self.order.payment_status = C.VERIFIED
        self.order.save()
        registration.on_payment_verified(self.order)


class HomeTests(DashboardCase):
    def test_under_review_order_counts_as_awaiting_not_spent(self):
        body = self.route("home/").json()
        self.assertEqual(body["summary"], {
            "total_spent": 0, "total_spent_text": "0.00", "total_spent_centavos": 0, "total_orders": 1, "delivered": 0, "needs_attention": 0,
        })
        om = body["order_monitor"]
        self.assertEqual((om["awaiting_approval"], om["for_booking"], om["for_shipping"], om["delivered"]), (1, 0, 0, 0))
        self.assertTrue(om["has_orders"])
        self.assertEqual([d["label"] for d in om["series"]], ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
        self.assertEqual(sum(d["count"] for d in om["series"]), 1)
        self.assertEqual(om["delta"], 1)

    def test_a_verified_order_is_spent_and_preparing(self):
        self.verify()
        body = self.route("home/").json()
        self.assertEqual(body["summary"]["total_spent"], 1488)
        self.assertEqual(body["summary"]["total_spent_text"], "1,488.00")
        self.assertEqual(body["summary"]["total_spent_centavos"], 148800)
        self.assertEqual((body["order_monitor"]["awaiting_approval"], body["order_monitor"]["for_booking"]), (0, 1))

    def test_a_rejected_order_needs_attention(self):
        self.reject()
        self.assertEqual(self.route("home/").json()["summary"]["needs_attention"], 1)

    def test_one_order_is_not_a_full_bar(self):
        series = self.route("home/").json()["order_monitor"]["series"]
        self.assertEqual(max(d["h"] for d in series), 25)  # 1 of the axis floor of 4

    def test_no_orders_means_an_empty_monitor(self):
        from apps.pack import dashboard
        om = dashboard.order_monitor([])
        self.assertFalse(om["has_orders"])
        self.assertEqual(om["delta"], 0)

    def test_session_and_host_rules(self):
        self.assertEqual(self.route("home/", token="x" * 43).status_code, 401)
        self.assertEqual(self.client.post(
            "/storefront/v1/member/home/", {"host": "nope.negosyotest.com", "token": self.session},
            content_type="application/json", secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}").json(), NOT_FOUND)


class OrdersTests(DashboardCase):
    def card(self):
        cards = self.route("orders/").json()["cards"]
        self.assertEqual(len(cards), 1)
        return cards[0]

    def test_under_review_card(self):
        c = self.card()
        self.assertEqual(c["order_number"], self.order.order_number)
        self.assertEqual((c["total"], c["total_text"], c["total_centavos"]), (1488, "1,488.00", 148800))
        self.assertEqual(c["order"], {"order_number": self.order.order_number, "created_at": c["created_at"], "total": 1488})
        self.assertEqual((c["headline"], c["item_count"]), ("Santé Membership Package ×1", 1))
        self.assertEqual(c["item_lines"], [{"qty": 1, "name": "Santé Membership Package · 2 Boxes With Stevia, 20 Sachets"}])
        self.assertEqual((c["chip_label"], c["chip_class"]), ("Under review", ""))
        self.assertTrue(c["under_review"])
        self.assertFalse(c["problem"])
        self.assertEqual((c["shipping_label"], c["courier"], c["tracking"]), ("FREE", "", ""))
        self.assertEqual(c["payment_label"], "Bank or e-wallet transfer")
        self.assertEqual([s["state"] for s in c["steps"]], ["done", "current", "todo", "todo"])
        self.assertEqual([s["label"] for s in c["steps"]], ["Order Placed", "Payment Verified", "Shipped", "Delivered"])
        self.assertIsNone(c["membership"])  # only once the payment is verified

    def test_verified_card_shows_the_membership_panel(self):
        self.verify()
        self.order.refresh_from_db()
        c = self.card()
        self.assertEqual((c["chip_label"], c["chip_class"]), ("Preparing your order", ""))
        self.assertEqual([s["state"] for s in c["steps"]], ["done", "done", "current", "todo"])
        self.assertIsNotNone(c["steps"][0]["date"])
        m = c["membership"]
        self.assertEqual(m["label"], "Details needed")
        self.assertEqual(m["line"], "Add your details so the store can register your membership with Santé.")
        self.assertEqual((m["registered"], m["sante_member_id"], m["act"]), (False, "", True))

    def test_rejected_card_is_a_problem(self):
        self.reject()
        c = self.card()
        self.assertTrue(c["problem"])
        self.assertEqual(c["problem_line"], "We could not verify your payment.")
        self.assertEqual((c["chip_label"], c["chip_class"]), ("Needs attention", "sf-status-expired"))

    def test_address_line_comes_from_the_order(self):
        self.order.delivery_address = {"receiver_name": "Ana R", "receiver_mobile": "+639171234567", "line": "1 Rizal St",
                                       "barangay": "Poblacion", "city": "Makati", "province": "Metro Manila", "zip": "1200"}
        self.order.save()
        c = self.card()
        self.assertEqual(c["address"], "1 Rizal St, Poblacion, Makati, Metro Manila 1200")
        self.assertEqual((c["receiver_name"], c["receiver_mobile"]), ("Ana R", "+639171234567"))

    def test_another_members_order_is_never_listed(self):
        from apps.members import sessions
        other = self.members_other()
        _, token = sessions.create_session(self.site, other)
        self.assertEqual(self.route("orders/", token=token).json(), {"cards": []})

    def members_other(self):
        from apps.members import services
        return services.create_member(self.site, full_name="Other Buyer", mobile="09179998888", email="other@buyer.test")


class DetailsFormTests(DashboardCase):
    ADDRESS = {"line": "1 Rizal St", "barangay": "Poblacion", "city": "Makati City", "province": "Metro Manila", "zip": "1210"}

    def setUp(self):
        super().setUp()
        self.member.mobile_state = "verified"
        self.member.save()

    def details(self, **fields):
        base = {"date_of_birth": "1990-05-17", "desired_username": "mariasantos1", "alternate_username": "mariasantos2"}
        return self.route("registration/details/", **{**base, **fields})

    def test_the_registration_card_says_the_address_is_needed_and_has_no_member_id(self):
        card = self.route("registration/").json()["registration"]
        self.assertTrue(card["needs_address"])
        self.assertEqual(card["sante_member_id"], "")

    def test_no_address_is_refused_with_the_address_error(self):
        response = self.details()
        self.assertEqual(response.status_code, 400)  # the members address check's status
        self.assertEqual(response.json()["error"]["code"], "invalid_address")
        self.assertEqual(SanteRegistration.objects.get().status, "awaiting_buyer_details")

    def test_one_date_field_and_no_terms_tick_saves_with_the_address(self):
        response = self.details(delivery_address=self.ADDRESS)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(SanteRegistration.objects.get().status, "waiting_for_accept")
        self.member.refresh_from_db()
        self.assertEqual(self.member.delivery_address["line"], "1 Rizal St")
        self.assertTrue(self.member.delivery_address["zip"])
        self.assertFalse(self.route("registration/").json()["registration"]["needs_address"])

    def test_a_bad_date_is_a_field_error(self):
        response = self.details(date_of_birth="not-a-date", delivery_address=self.ADDRESS)
        self.assertEqual(response.status_code, 422)
        self.assertIn("date_of_birth", response.json()["error"]["fields"])

    def test_the_three_part_date_still_works(self):
        response = self.details(date_of_birth="", dob_year="1990", dob_month="5", dob_day="17", delivery_address=self.ADDRESS)
        self.assertEqual(response.status_code, 200, response.content)
