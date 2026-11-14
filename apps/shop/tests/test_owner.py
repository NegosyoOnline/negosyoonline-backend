"""The Subscriber's product order routes: list, detail, proof read, confirm, cancel, ship, deliver,
the state machine, stale 409, isolation, the switch and the template rule. The Billing-only rule
is listed in apps/sites/tests/test_billing_only.py."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import Client, override_settings
from django.utils import timezone

from apps.shop import constants as C
from apps.shop.models import ShopOrder, ShopOrderEvent
from apps.shop.tests.owner_base import OwnerTestCase, PNG, make_order
from apps.sites.models import Site


class ListAndDetailTests(OwnerTestCase):
    def test_list_filters_and_newest_first(self):
        review, _ = make_order(self.site)
        to_ship, _ = make_order(self.site, status=C.VERIFIED)
        shipped, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        delivered, _ = make_order(self.site, status=C.VERIFIED, shipped=True, delivered=True)
        cancelled, _ = make_order(self.site, status=C.CANCELLED)
        ids = lambda query: [o["id"] for o in self.get(self.url(query)).json()["orders"]]  # noqa: E731
        self.assertEqual(ids(""), [o.public_id for o in (cancelled, delivered, shipped, to_ship, review)])
        self.assertEqual(ids("?filter=to_review"), [review.public_id])
        self.assertEqual(ids("?filter=to_ship"), [to_ship.public_id])
        self.assertEqual(ids("?filter=shipped"), [delivered.public_id, shipped.public_id])
        self.assertEqual(ids("?filter=cancelled"), [cancelled.public_id])
        self.assertEqual(ids("?filter=all"), ids(""))

    def test_the_row_shape_and_masked_mobile(self):
        order, _ = make_order(self.site)
        row = self.get(self.url()).json()["orders"][0]
        self.assertEqual(
            sorted(row),
            ["buyer_kind", "created_at", "currency", "id", "mobile_masked", "order_number", "payment_status",
             "receiver_name", "ship_mode", "shipping_charged_centavos", "state_version", "status",
             "subtotal_centavos", "total_centavos"],
        )
        self.assertNotIn(order.receiver_mobile, str(row))
        self.assertEqual((row["status"], row["total_centavos"]), ("placed", 10000))

    def test_the_list_is_capped_at_200(self):
        for _ in range(3):
            make_order(self.site)
        with mock.patch("apps.shop.constants.OWNER_LIST_MAX", 2):
            self.assertEqual(len(self.get(self.url()).json()["orders"]), 2)

    def test_detail_has_lines_address_contact_proof_and_history(self):
        order, proof = make_order(self.site)
        body = self.get(self.url(f"{order.public_id}/")).json()
        self.assertEqual(body["state_version"], order.state_version)
        self.assertEqual(body["lines"][0]["code"], "FX-A")
        self.assertEqual(body["delivery_address"]["city"], "Makati")
        self.assertEqual(body["contact_email"], order.contact_email)
        self.assertEqual(body["receiver_mobile"], order.receiver_mobile)
        self.assertEqual(body["proof"]["id"], proof.public_id)
        self.assertEqual((body["proof"]["reference_number"], body["proof"]["amount_sent_centavos"]), ("REF123", 10000))
        self.assertEqual([e["kind"] for e in body["events"]], ["placed"])
        self.assertNotIn("file_key", str(body))
        self.assertNotIn("view_token", str(body))
        self.assertNotIn("quote_fingerprint", str(body))

    def test_an_unknown_order_is_404(self):
        self.error(self.get(self.url("sho_missing/")), 404, "not_found")


class ProofReadTests(OwnerTestCase):
    def test_the_proof_opens_with_private_headers(self):
        order, proof = make_order(self.site)
        response = self.get(self.url(f"{order.public_id}/proofs/{proof.public_id}/"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PNG)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("inline", response["Content-Disposition"])

    def test_a_proof_of_another_order_is_404(self):
        order, _ = make_order(self.site)
        _, other_proof = make_order(self.site)
        self.error(self.get(self.url(f"{order.public_id}/proofs/{other_proof.public_id}/")), 404, "not_found")

    def test_the_proof_download_is_rate_limited(self):
        order, proof = make_order(self.site)
        with mock.patch.object(C, "OWNER_LINK_LIMIT", ("shop-link-test", 2, 600)):
            statuses = [self.get(self.url(f"{order.public_id}/proofs/{proof.public_id}/")).status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])


class ConfirmTests(OwnerTestCase):
    def test_confirm_receipt_verifies_writes_an_event_and_mails_the_buyer(self):
        order, _ = make_order(self.site)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.act(order, "confirm-receipt").json()
        self.assertEqual((body["payment_status"], body["status"]), (C.VERIFIED, "confirmed"))
        self.assertEqual(body["state_version"], order.state_version + 1)
        self.assertIsNotNone(body["verified_at"])
        event = ShopOrderEvent.objects.filter(kind="verified").get()
        self.assertEqual((event.actor_type, event.actor_label), ("subscriber", self.account.email))
        self.assertEqual([m.to for m in mail.outbox], [[order.contact_email]])
        self.assertEqual(mail.outbox[0].subject, f"Payment confirmed: {order.order_number}")

    def test_a_second_confirm_is_wrong_status(self):
        order, _ = make_order(self.site)
        self.act(order, "confirm-receipt")
        order.refresh_from_db()
        self.error(self.act(order, "confirm-receipt"), 409, "wrong_status")

    def test_a_stale_version_is_409_and_changes_nothing(self):
        order, _ = make_order(self.site)
        r = self.post(self.url(f"{order.public_id}/confirm-receipt/"), {"state_version": order.state_version + 5})
        self.error(r, 409, "stale")
        self.assertEqual(ShopOrder.objects.get().payment_status, C.PENDING_REVIEW)

    def test_a_cancelled_order_cannot_be_confirmed(self):
        order, _ = make_order(self.site, status=C.CANCELLED)
        self.error(self.act(order, "confirm-receipt"), 409, "wrong_status")

    def test_an_unknown_body_key_is_refused(self):
        order, _ = make_order(self.site)
        r = self.post(self.url(f"{order.public_id}/confirm-receipt/"), {"state_version": 1, "site_id": "x"})
        self.assertEqual(r.status_code, 400)


class CancelTests(OwnerTestCase):
    def test_cancel_needs_a_note_of_1_to_500_characters(self):
        order, _ = make_order(self.site)
        for note in ("", "   ", "x" * 501):
            with self.subTest(note=note[:5]):
                self.error(self.act(order, "cancel", note=note), 422, "note_required")
        self.assertEqual(ShopOrder.objects.get().payment_status, C.PENDING_REVIEW)
        self.assertFalse(ShopOrderEvent.objects.filter(kind="cancelled").exists())

    def test_cancel_records_the_note_and_the_email_never_carries_it(self):
        order, _ = make_order(self.site)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.act(order, "cancel", note="Wrong amount received").json()
        self.assertEqual((body["payment_status"], body["status"], body["cancel_note"]),
                         (C.CANCELLED, "cancelled", "Wrong amount received"))
        self.assertIsNotNone(body["cancelled_at"])
        self.assertEqual(ShopOrderEvent.objects.get(kind="cancelled").note, "Wrong amount received")
        sent = mail.outbox[0]
        self.assertNotIn("Wrong amount", sent.subject + sent.body)
        self.assertIn("cancelled by the store", sent.body)

    def test_cancel_after_confirmation_or_shipment_is_refused(self):
        verified, _ = make_order(self.site, status=C.VERIFIED)
        shipped, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        for order in (verified, shipped):
            self.error(self.act(order, "cancel", note="no"), 409, "wrong_status")
        self.assertFalse(ShopOrder.objects.filter(payment_status=C.CANCELLED).exists())

    def test_a_cancelled_order_cannot_be_cancelled_again(self):
        order, _ = make_order(self.site, status=C.CANCELLED)
        self.error(self.act(order, "cancel", note="again"), 409, "wrong_status")


class ShipAndDeliverTests(OwnerTestCase):
    def test_ship_needs_a_confirmed_order_and_a_courier(self):
        pending, _ = make_order(self.site)
        self.error(self.act(pending, "ship", courier="J&T"), 409, "wrong_status")
        order, _ = make_order(self.site, status=C.VERIFIED)
        self.error(self.act(order, "ship", courier=""), 422, "courier_required")
        self.error(self.act(order, "ship", courier="   "), 422, "courier_required")
        self.assertIsNone(ShopOrder.objects.get(pk=order.pk).shipped_on)

    def test_ship_records_courier_tracking_and_date_and_mails_the_buyer(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.act(order, "ship", courier="J&T Express", tracking_no="JT123").json()
        today = timezone.localdate().isoformat()
        self.assertEqual((body["status"], body["courier"], body["tracking_no"], body["shipped_on"]),
                         ("shipped", "J&T Express", "JT123", today))
        self.assertIn("JT123", mail.outbox[0].body)
        self.assertEqual(ShopOrderEvent.objects.filter(kind="shipped").count(), 1)

    def test_the_shipping_date_may_be_earlier_but_never_in_the_future(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.error(self.act(order, "ship", courier="J&T", shipped_on=future), 422, "invalid_date")
        past = (timezone.localdate() - timedelta(days=2)).isoformat()
        self.assertEqual(self.act(order, "ship", courier="J&T", shipped_on=past).json()["shipped_on"], past)

    def test_an_order_cannot_be_shipped_twice(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        self.act(order, "ship", courier="J&T")
        order.refresh_from_db()
        self.error(self.act(order, "ship", courier="J&T"), 409, "wrong_status")

    def test_deliver_needs_a_shipped_order(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        self.error(self.act(order, "deliver"), 409, "wrong_status")
        shipped, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.act(shipped, "deliver").json()
        self.assertEqual((body["status"], body["delivered_on"]), ("delivered", timezone.localdate().isoformat()))
        self.assertEqual(mail.outbox[0].subject, f"Your order {shipped.order_number} was delivered")
        shipped.refresh_from_db()
        self.error(self.act(shipped, "deliver"), 409, "wrong_status")

    def test_delivery_cannot_be_before_shipment(self):
        order, _ = make_order(self.site, status=C.VERIFIED, shipped=True)  # shipped yesterday
        before = (timezone.localdate() - timedelta(days=3)).isoformat()
        self.error(self.act(order, "deliver", delivered_on=before), 422, "invalid_date")

    def test_the_full_path_writes_one_event_per_step(self):
        order, _ = make_order(self.site)
        for action, extra in (("confirm-receipt", {}), ("ship", {"courier": "LBC"}), ("deliver", {})):
            order.refresh_from_db()
            self.assertEqual(self.act(order, action, **extra).status_code, 200)
        self.assertEqual([e.kind for e in ShopOrderEvent.objects.all()], ["placed", "verified", "shipped", "delivered"])
        self.assertEqual(ShopOrder.objects.get().state_version, 4)

    def test_a_stale_ship_is_409(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        r = self.post(self.url(f"{order.public_id}/ship/"), {"state_version": 99, "courier": "J&T"})
        self.error(r, 409, "stale")

    def test_a_failing_email_never_undoes_the_action(self):
        order, _ = make_order(self.site)
        with mock.patch("apps.shop.notices.send_email", side_effect=RuntimeError("smtp down")):
            with self.captureOnCommitCallbacks(execute=True):
                body = self.act(order, "confirm-receipt")
        self.assertEqual(body.status_code, 200)
        self.assertEqual(ShopOrder.objects.get().payment_status, C.VERIFIED)


class MemberOrderTests(OwnerTestCase):
    def test_a_member_home_order_shows_on_my_dashboard_and_no_member_data_crosses_sites(self):
        from apps.members import services as members

        mine = members.create_member(
            self.site, full_name="Maria Santos", mobile="09171234567", email="maria@mine.test", email_state="pending",
        )
        order, _ = make_order(self.site, member=mine)
        ShopOrder.objects.filter(pk=order.pk).update(ship_mode="home", credited_member=None)
        _ben, ben_site, ben_order, _ = self.other_site()
        theirs = members.create_member(
            ben_site, full_name="Juan Cruz", mobile="09170000002", email="juan@theirs.test", email_state="pending",
        )
        ShopOrder.objects.filter(pk=ben_order.pk).update(member=theirs, buyer_kind="member", ship_mode="home")
        detail = self.get(self.url(f"{order.public_id}/")).json()
        self.assertEqual((detail["buyer_kind"], detail["ship_mode"]), ("member", "home"))
        everything = str(detail) + str(self.get(self.url()).json())
        self.assertNotIn("juan@theirs.test", everything)
        self.assertNotIn("Juan Cruz", everything)
        self.error(self.get(self.url(f"{ben_order.public_id}/")), 404, "not_found")


class IsolationTests(OwnerTestCase):
    def test_another_subscribers_site_and_order_are_404_and_untouched(self):
        ben, ben_site, order, proof = self.other_site()
        sid = ben_site.public_id
        calls = [
            ("get", self.url("", site_id=sid), None),
            ("get", self.url(f"{order.public_id}/", site_id=sid), None),
            ("get", self.url(f"{order.public_id}/proofs/{proof.public_id}/", site_id=sid), None),
            ("post", self.url(f"{order.public_id}/confirm-receipt/", site_id=sid), {"state_version": 1}),
            ("post", self.url(f"{order.public_id}/cancel/", site_id=sid), {"state_version": 1, "note": "x"}),
            # my site, his order id
            ("get", self.url(f"{order.public_id}/"), None),
            ("get", self.url(f"{order.public_id}/proofs/{proof.public_id}/"), None),
            ("post", self.url(f"{order.public_id}/confirm-receipt/"), {"state_version": 1}),
            ("post", self.url(f"{order.public_id}/ship/"), {"state_version": 1, "courier": "x"}),
        ]
        for method, path, body in calls:
            r = self.get(path) if method == "get" else self.send("post", path, body)
            self.assertEqual(r.status_code, 404, (path, r.content))
        self.assertEqual(ShopOrder.objects.get(pk=order.pk).payment_status, C.PENDING_REVIEW)

    def test_signed_out_is_401(self):
        self.assertEqual(self.get(self.url(), client=Client()).status_code, 401)

    def test_a_site_that_is_not_a_sante_site_is_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        self.error(self.get(self.url()), 404, "not_found")

    def test_a_removed_site_is_404(self):
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.error(self.get(self.url()), 404, "not_found")


class SwitchTests(OwnerTestCase):
    @override_settings(SHOP_ENABLED=False)
    def test_off_every_route_is_404_signed_in_or_not(self):
        order, proof = make_order(self.site)
        calls = [
            ("get", self.url()), ("get", self.url(f"{order.public_id}/")),
            ("get", self.url(f"{order.public_id}/proofs/{proof.public_id}/")),
        ]
        for method, path in calls:
            self.error(self.get(path), 404, "not_found")
            self.assertEqual(self.get(path, client=Client()).status_code, 404)
        for action in ("confirm-receipt", "cancel", "ship", "deliver"):
            r = self.post(self.url(f"{order.public_id}/{action}/"), {"state_version": 1})
            self.error(r, 404, "not_found")
        self.assertEqual(ShopOrder.objects.get().payment_status, C.PENDING_REVIEW)
