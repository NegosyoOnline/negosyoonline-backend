"""Shipment by hand (PK-SHIP): record, correct, deliver, the derived status, the switch, the
emails, the member reads and the database constraints."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from apps.accounts.tests.base import ORIGIN
from apps.pack import constants as C, dashboard, fulfillment
from apps.pack.models import PackOrder, PackOrderEvent
from apps.pack.tests.owner_base import OwnerTestCase, make_order

SHIP = {"courier": "lbc", "tracking_number": "LBC123456"}


class ShipCase(OwnerTestCase):
    def ship_url(self, order, tail="shipment/"):
        return self.url(f"{order.public_id}/{tail}")

    def ship(self, order, **body):
        return self.post(self.ship_url(order), {"state_version": order.state_version, **SHIP, **body})

    def reload(self, order):
        order.refresh_from_db()
        return order


class RecordTests(ShipCase):
    def test_ship_a_verified_order_writes_fields_event_and_emails_once(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.ship(order, shipped_on=str(timezone.localdate())).json()
        self.assertEqual(body["shipment_status"], "shipped")
        self.assertEqual(body["state_version"], order.state_version + 1)
        self.assertEqual(body["shipment"]["courier_label"], "LBC")
        self.assertEqual(body["shipment"]["tracking_number"], "LBC123456")
        self.assertEqual(body["shipment"]["recorded_by"], self.account.email)
        event = PackOrderEvent.objects.get(kind=C.EVENT_SHIPMENT_RECORDED)
        self.assertEqual((event.actor_type, event.actor_ref), ("subscriber", self.account.pk))
        self.assertEqual(body["events"][-1]["kind"], C.EVENT_SHIPMENT_RECORDED)
        self.assertEqual([m.to for m in mail.outbox], [[order.member.email]])
        self.assertIn("Tracking number: LBC123456", mail.outbox[0].body)
        self.assertIn("Courier: LBC", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].subject, f"Your order {order.order_number} has shipped")

    def test_shipped_date_defaults_to_today_in_manila(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        body = self.ship(order).json()
        self.assertEqual(body["shipment"]["shipped_on"], fulfillment.manila_today().isoformat())

    def test_other_courier_needs_a_name_and_keeps_it(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        r = self.ship(order, courier="other")
        self.assertEqual(self.error(r, 422, "invalid_shipment")["fields"], {"courier_other_name": ["Enter the courier name."]})
        body = self.ship(order, courier="other", courier_other_name="Hand delivery").json()
        self.assertEqual(body["shipment"]["courier_label"], "Hand delivery")

    def test_a_courier_name_is_dropped_for_a_listed_courier(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        body = self.ship(order, courier_other_name="ignored").json()
        self.assertEqual(body["shipment"]["courier_other_name"], "")

    def test_only_a_verified_order_ships(self):
        for status in (C.PENDING_REVIEW, C.REJECTED):
            order, _ = make_order(self.site, status=status, lock=status == C.REJECTED)
            self.error(self.ship(order), 409, "wrong_status")
            self.assertIsNone(self.reload(order).shipment_recorded_at)

    def test_a_second_shipment_is_409(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        self.ship(order)
        self.reload(order)
        self.error(self.ship(order), 409, "already_shipped")

    def test_required_fields_and_dates(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        fields = self.error(self.ship(order, courier="", tracking_number=" "), 422, "invalid_shipment")["fields"]
        self.assertEqual(sorted(fields), ["courier", "tracking_number"])
        future = str(timezone.localdate() + timedelta(days=2))
        self.assertIn("shipped_on", self.error(self.ship(order, shipped_on=future), 422, "invalid_shipment")["fields"])
        self.assertIn("shipped_on", self.error(self.ship(order, shipped_on="soon"), 422, "invalid_shipment")["fields"])
        self.assertIn("tracking_number", self.error(self.ship(order, tracking_number="x" * 65), 422, "invalid_shipment")["fields"])
        self.assertIsNone(self.reload(order).shipment_recorded_at)

    def test_a_stale_version_changes_nothing(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        self.error(self.post(self.ship_url(order), {"state_version": 99, **SHIP}), 409, "stale")
        order = self.reload(order)
        self.assertIsNone(order.shipment_recorded_at)
        self.assertFalse(PackOrderEvent.objects.filter(kind=C.EVENT_SHIPMENT_RECORDED).exists())

    def test_an_unknown_body_key_is_refused(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        self.assertEqual(self.ship(order, payment_status="verified").status_code, 400)

    def test_a_mail_failure_does_not_undo_the_write(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        with mock.patch("apps.pack.notices.send_email", side_effect=RuntimeError("down")):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.ship(order).status_code, 200)
        self.assertIsNotNone(self.reload(order).shipment_recorded_at)

    def test_no_email_when_the_buyer_has_none(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        order.member.email = ""
        order.member.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.ship(order)
        self.assertEqual(mail.outbox, [])


class CorrectTests(ShipCase):
    def test_correct_updates_logs_old_and_new_and_sends_no_email(self):
        order, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.post(
                self.ship_url(order, "shipment/correct/"),
                {"state_version": order.state_version, "courier": "jnt", "tracking_number": "JT9"},
            ).json()
        self.assertEqual(body["shipment"]["courier_label"], "J&T Express")
        event = PackOrderEvent.objects.get(kind=C.EVENT_SHIPMENT_CORRECTED)
        self.assertEqual((event.old["courier"], event.new["courier"]), ("lbc", "jnt"))
        self.assertEqual(mail.outbox, [])
        self.assertEqual(body["state_version"], order.state_version + 1)

    def test_correct_before_ship_is_409(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        r = self.post(self.ship_url(order, "shipment/correct/"), {"state_version": order.state_version, **SHIP})
        self.error(r, 409, "not_shipped")

    def test_delivery_is_final(self):
        order, _ = make_order(self.site, status=C.VERIFIED, delivered=True)
        r = self.post(self.ship_url(order, "shipment/correct/"), {"state_version": order.state_version, **SHIP})
        self.error(r, 409, "delivered_final")


class DeliveryTests(ShipCase):
    def deliver(self, order, **body):
        return self.post(self.ship_url(order, "delivery/"), {"state_version": order.state_version, **body})

    def test_deliver_marks_logs_and_emails(self):
        order, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.deliver(order).json()
        self.assertEqual(body["shipment_status"], "delivered")
        self.assertEqual(body["shipment"]["delivery_confirmed_by"], self.account.email)
        event = PackOrderEvent.objects.get(kind=C.EVENT_DELIVERED)
        self.assertEqual(event.new["source"], "manual")
        self.assertEqual(mail.outbox[0].subject, f"Your order {order.order_number} was delivered")
        self.assertIn("reply to this email", mail.outbox[0].body)

    def test_deliver_before_ship_is_409(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        self.error(self.deliver(order), 409, "not_shipped")

    def test_delivered_twice_is_409(self):
        order, _ = make_order(self.site, status=C.VERIFIED, delivered=True)
        self.error(self.deliver(order), 409, "delivered_final")

    def test_the_date_is_between_shipped_and_today(self):
        order, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        before = str(timezone.localdate() - timedelta(days=1))
        after = str(timezone.localdate() + timedelta(days=1))
        for bad in (before, after, "nope"):
            fields = self.error(self.deliver(order, delivered_on=bad), 422, "invalid_delivery")["fields"]
            self.assertEqual(list(fields), ["delivered_on"])
        self.assertIsNone(self.reload(order).delivery_confirmed_at)


class SwitchAndAccessTests(ShipCase):
    def test_switch_off_answers_404_and_sends_nothing(self):
        order, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        with override_settings(PACK_SHIPPING_ENABLED=False):
            for tail, body in (
                ("shipment/", SHIP), ("shipment/correct/", SHIP), ("delivery/", {}),
            ):
                r = self.post(self.ship_url(order, tail), {"state_version": order.state_version, **body})
                self.error(r, 404, "not_found")
            self.assertFalse(self.get(self.url(f"{order.public_id}/")).json()["shipping_enabled"])
            with self.captureOnCommitCallbacks(execute=True):
                from apps.pack import notices
                self.assertIsNone(notices.order_shipped(order.pk))
        self.assertEqual(mail.outbox, [])

    def test_signed_out_is_401(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        self.client.cookies.clear()
        r = self.client.post(
            self.ship_url(order), {"state_version": 1, **SHIP}, content_type="application/json", secure=True,
            HTTP_ORIGIN=ORIGIN,
        )
        self.assertEqual(r.status_code, 401, r.content)

    def test_another_subscribers_order_and_site_are_404(self):
        ben, site, order, _ = self.other_site()
        order.payment_status = C.VERIFIED
        order.save()
        r = self.post(self.url(f"{order.public_id}/shipment/"), {"state_version": order.state_version, **SHIP})
        self.error(r, 404, "not_found")  # Ana's site, Ben's order
        r = self.post(self.url(f"{order.public_id}/shipment/", site_id=site.public_id), {"state_version": 1, **SHIP})
        self.error(r, 404, "not_found")
        self.assertIsNone(self.reload(order).shipment_recorded_at)


class ListAndDetailTests(ShipCase):
    def test_filters_and_row_fields(self):
        review, _ = make_order(self.site)
        ready, _ = make_order(self.site, status=C.VERIFIED)
        shipped, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        done, _ = make_order(self.site, status=C.VERIFIED, delivered=True)

        def ids(name):
            return {o["id"] for o in self.get(self.url(f"?filter={name}")).json()["orders"]}

        self.assertEqual(ids("ready_to_ship"), {ready.public_id})
        self.assertEqual(ids("shipped"), {shipped.public_id})
        self.assertEqual(ids("delivered"), {done.public_id})
        self.assertEqual(ids("to_review"), {review.public_id})
        rows = {o["id"]: o for o in self.get(self.url()).json()["orders"]}
        self.assertEqual(
            [rows[o.public_id]["shipment_status"] for o in (ready, shipped, done)],
            ["not_shipped", "shipped", "delivered"],
        )
        self.assertTrue(all(r["shipping_enabled"] for r in rows.values()))
        self.assertEqual(rows[shipped.public_id]["courier"], "LBC")

    def test_detail_has_no_shipment_before_shipping_and_no_buyer_contact(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        body = self.get(self.url(f"{order.public_id}/")).json()
        self.assertIsNone(body["shipment"])
        self.assertNotIn(order.member.email, str(body))


class ConstraintTests(OwnerTestCase):
    def test_the_database_refuses_a_shipped_unverified_row_and_a_delivered_unshipped_one(self):
        order, _ = make_order(self.site)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PackOrder.objects.filter(pk=order.pk).update(shipment_recorded_at=timezone.now())
        verified, _ = make_order(self.site, status=C.VERIFIED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PackOrder.objects.filter(pk=verified.pk).update(delivery_confirmed_at=timezone.now())


class MemberReadTests(OwnerTestCase):
    def cards(self, order):
        return dashboard.cards(order.member, dashboard.orders_for(order.member))

    def test_each_state_reads_through(self):
        ready, _ = make_order(self.site, status=C.VERIFIED)
        card = self.cards(ready)[0]
        self.assertEqual((card["chip_label"], card["courier"], card["tracking"]), ("Preparing your order", "", ""))
        self.assertEqual([s["state"] for s in card["steps"]], ["done", "done", "current", "todo"])
        summary = dashboard.order_monitor(dashboard.orders_for(ready.member))
        self.assertEqual((summary["for_booking"], summary["for_shipping"], summary["delivered"]), (1, 0, 0))

        shipped, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        card = self.cards(shipped)[0]
        self.assertEqual((card["chip_label"], card["courier"], card["tracking"]), ("Shipped", "LBC", "LBC123456"))
        self.assertEqual([s["state"] for s in card["steps"]], ["done", "done", "done", "current"])
        self.assertEqual(card["steps"][2]["date"], timezone.localdate().isoformat())
        orders = dashboard.orders_for(shipped.member)
        self.assertEqual(dashboard.order_monitor(orders)["for_shipping"], 1)
        self.assertEqual(dashboard.summary(orders)["delivered"], 0)

        done, _ = make_order(self.site, status=C.VERIFIED, delivered=True)
        card = self.cards(done)[0]
        self.assertEqual(card["chip_label"], "Delivered")
        self.assertEqual([s["state"] for s in card["steps"]], ["done"] * 4)
        orders = dashboard.orders_for(done.member)
        self.assertEqual(dashboard.summary(orders)["delivered"], 1)
        self.assertEqual(dashboard.order_monitor(orders)["delivered"], 1)

    def test_an_order_under_review_shows_no_shipment(self):
        order, _ = make_order(self.site)
        card = self.cards(order)[0]
        self.assertEqual((card["courier"], card["tracking"]), ("", ""))

    def test_a_courier_name_for_other_shows_the_typed_name(self):
        order, _ = make_order(self.site, status=C.VERIFIED, shipped=True)
        order.courier, order.courier_other_name = "other", "Hand delivery"
        order.save()
        self.assertEqual(self.cards(order)[0]["courier"], "Hand delivery")
