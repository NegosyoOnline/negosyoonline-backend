"""`/readyz/` field `xendit_queue`: false when a Membership Pack order stays `awaiting_xendit` past the
window in which the reconcile and the sweep should have settled it."""
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.health import tasks, views
from apps.pack import constants as C
from apps.pack.models import PackOrder
from apps.pack.tests.xendit_base import XenditPackCase


class XenditQueueTests(XenditPackCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        cache.set(tasks.HEARTBEAT_KEY, timezone.now().timestamp(), 600)
        _, _, self.order = self.placed_xendit()

    def get(self):
        return self.client.get("/readyz/", secure=True)

    def update(self, **fields):
        PackOrder.objects.filter(pk=self.order.pk).update(**fields)

    def test_payload_has_the_key(self):
        self.assertIn("xendit_queue", self.get().json())

    @override_settings(XENDIT_ENABLED=False)
    def test_off_is_true_even_with_a_stuck_order(self):
        self.update(xendit_expires_at=timezone.now() - timedelta(days=2))
        self.assertTrue(self.get().json()["xendit_queue"])

    def test_no_orders_is_true(self):
        self.update(payment_status=C.VERIFIED)  # no order awaits payment
        self.assertTrue(views.check_xendit_queue())

    def test_a_fresh_awaiting_order_with_an_invoice_is_true(self):
        self.assertEqual(self.order.payment_status, C.AWAITING_XENDIT)
        self.assertTrue(self.order.xendit_expires_at)
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["xendit_queue"])

    def test_expired_invoice_inside_the_window_is_true(self):
        self.update(xendit_expires_at=timezone.now() - timedelta(minutes=80))
        self.assertTrue(views.check_xendit_queue())

    def test_an_order_past_expiry_plus_sweep_plus_reconcile_plus_margin_is_503(self):
        self.update(xendit_expires_at=timezone.now() - timedelta(minutes=100))
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["xendit_queue"])

    def test_no_invoice_younger_than_the_window_is_true(self):
        self.update(xendit_expires_at=None, xendit_invoice_id="", created_at=timezone.now() - timedelta(hours=3))
        self.assertTrue(views.check_xendit_queue())

    def test_no_invoice_older_than_the_window_is_false(self):
        self.update(xendit_expires_at=None, xendit_invoice_id="", created_at=timezone.now() - timedelta(hours=4))
        self.assertFalse(views.check_xendit_queue())

    def test_verified_or_expired_old_orders_are_true(self):
        old = timezone.now() - timedelta(days=3)
        for status in (C.VERIFIED, C.XENDIT_EXPIRED):
            self.update(payment_status=status, xendit_expires_at=old, created_at=old)
            self.assertTrue(views.check_xendit_queue(), status)
