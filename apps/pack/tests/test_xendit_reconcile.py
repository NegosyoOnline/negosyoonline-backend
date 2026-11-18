"""The 15-minute Xendit reconcile and the window sweep: the site's own key, the same handler as the
webhook, a 401 marks the key failed, and a switch that is off reads nothing."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.pack import constants as C, xendit
from apps.pack.models import PackOrder
from apps.pack.tests.xendit_base import XenditPackCase, fake_xendit, give_key
from apps.payments import reconcile, tasks
from apps.payments.models import SitePaymentSettings, SiteXenditCredential, XenditEvent


def age(order, **delta):
    """Make the order older: placed `delta` ago."""
    PackOrder.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(**delta))
    order.refresh_from_db()
    return order


class ReconcileTests(XenditPackCase):
    def test_a_paid_invoice_is_applied_through_the_same_handler_as_the_webhook(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        self.pay_in_fake(order)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(reconcile.run(), 1)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertEqual(XenditEvent.objects.get().source, "reconcile")
        self.assertEqual(len([m for m in mail.outbox if "is verified" in m.subject]), 1)

    def test_it_runs_with_the_task_name_the_beat_schedule_uses(self):
        from django.conf import settings
        self.assertEqual(tasks.xendit_reconcile.name, "payments.xendit_reconcile")
        entry = settings.CELERY_BEAT_SCHEDULE["payments-xendit-reconcile"]
        self.assertEqual((entry["task"], entry["schedule"]), ("payments.xendit_reconcile", 900))
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        self.pay_in_fake(order)
        tasks.xendit_reconcile()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)

    def test_the_run_after_the_webhook_has_nothing_left_to_do(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        self.pay_in_fake(order)
        self.webhook(order)
        self.assertEqual(reconcile.run(), 0)
        self.assertEqual(order.events.filter(kind="xendit_paid").count(), 1)

    def test_an_order_younger_than_15_minutes_is_not_read(self):
        _, _, order = self.placed_xendit()
        self.pay_in_fake(order)
        calls = len(fake_xendit.calls)
        reconcile.run()
        self.assertEqual(len(fake_xendit.calls), calls)

    def test_an_expired_invoice_closes_the_order(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        fake_xendit.expire(order.xendit_invoice_id)
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)

    def test_a_pending_invoice_is_left_alone(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)
        self.assertEqual(XenditEvent.objects.count(), 0)

    def test_a_wrong_amount_becomes_a_mismatch(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        self.pay_in_fake(order, amount=500)
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_MISMATCH)

    def test_a_rejected_key_marks_it_failed_once_and_emails_the_owner(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        give_key(self.site, api_key="xnd_development_rejected")
        with mock.patch("apps.notifications.services.send_email") as send:
            reconcile.run()
            reconcile.run()
        self.assertIsNotNone(SiteXenditCredential.objects.get(site=self.site).key_failed_at)
        self.assertEqual(send.call_count, 1)  # told once, not every 15 minutes
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)

    def test_an_unreachable_xendit_changes_nothing_and_the_run_goes_on(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        give_key(self.site, api_key="xnd_development_down")
        self.assertEqual(reconcile.run(), 0)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)
        self.assertIsNone(SiteXenditCredential.objects.get(site=self.site).key_failed_at)

    def test_one_sites_bad_document_does_not_stop_another_order(self):
        _, _, first = self.placed_xendit()
        age(first, minutes=20)
        self.pay_in_fake(first)
        with mock.patch("apps.payments.xendit_client.get_invoice", side_effect=ValueError("bad body")):
            self.assertEqual(reconcile.run(), 0)
        self.assertEqual(reconcile.run(), 1)

    def test_each_order_is_read_with_its_own_sites_key(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        seen = []
        real = fake_xendit.call

        def watch(api_key, *args, **kwargs):
            seen.append(api_key)
            return real(api_key, *args, **kwargs)

        with mock.patch.object(fake_xendit, "call", watch):
            reconcile.run()
        self.assertEqual(set(seen), {"xnd_development_fakekeyfortests"})

    def test_a_site_without_a_key_is_skipped_and_its_order_expires_with_the_window(self):
        _, _, order = self.placed_xendit()
        age(order, hours=1, minutes=0)
        self.cred.api_key_ciphertext = ""
        self.cred.save()
        calls = len(fake_xendit.calls)
        reconcile.run()
        self.assertEqual(len(fake_xendit.calls), calls)  # nothing can be read without the key
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)  # still inside its window
        PackOrder.objects.filter(pk=order.pk).update(xendit_expires_at=timezone.now() - timedelta(hours=1, minutes=5))
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)
        self.assertEqual(order.events.get(kind="xendit_expired").new, {"reason": "window_passed"})

    def test_it_reads_a_site_whose_owner_switched_xendit_off_and_does_not_need_the_payment_methods_switch(self):
        _, _, order = self.placed_xendit()
        age(order, minutes=20)
        SitePaymentSettings.objects.filter(site=self.site).update(xendit_enabled=False)
        self.pay_in_fake(order)
        with override_settings(PAYMENT_METHODS_ENABLED=False):  # the worker has only XENDIT_ENABLED
            self.assertEqual(reconcile.run(), 1)

    @override_settings(XENDIT_ENABLED=False)
    def test_platform_switch_off_reads_nothing_and_touches_no_table(self):
        with self.assertNumQueries(0):
            self.assertEqual(reconcile.run(), 0)
            self.assertEqual(tasks.xendit_reconcile(), 0)
        self.assertEqual(fake_xendit.calls, [])


class InvoicelessAndWindowTests(XenditPackCase):
    def invoiceless(self, **placed_ago):
        give_key(self.site, api_key="xnd_development_down")
        _, _, order = self.placed_xendit()
        give_key(self.site)
        return age(order, **placed_ago)

    def test_an_order_with_no_invoice_after_two_hours_gets_one_last_lookup_and_adopts_what_is_found(self):
        order = self.invoiceless(hours=3)
        found = fake_xendit.call("k", "POST", "/v2/invoices", payload={
            "external_id": order.xendit_external_id, "amount": 1488.0, "currency": "PHP", "invoice_duration": 86400,
            "success_redirect_url": "x", "failure_redirect_url": "x",
        })
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.xendit_invoice_id, found["id"])
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)

    def test_with_nothing_found_after_two_hours_it_expires(self):
        order = self.invoiceless(hours=3)
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)
        self.assertEqual(order.events.get(kind="xendit_expired").new, {"reason": "no_invoice"})

    def test_before_two_hours_the_invoiceless_order_waits(self):
        order = self.invoiceless(hours=1)
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)

    def test_with_a_rejected_key_the_invoiceless_order_still_expires_after_two_hours(self):
        order = self.invoiceless(hours=3)
        give_key(self.site, api_key="xnd_development_rejected")
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)

    def test_the_window_sweep_expires_an_order_one_hour_after_its_invoice_expired(self):
        _, _, order = self.placed_xendit()
        age(order, days=2)
        PackOrder.objects.filter(pk=order.pk).update(xendit_expires_at=timezone.now() - timedelta(minutes=61))
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)

    def test_inside_the_grace_hour_it_is_still_read_not_expired(self):
        _, _, order = self.placed_xendit()
        age(order, days=2)
        PackOrder.objects.filter(pk=order.pk).update(xendit_expires_at=timezone.now() - timedelta(minutes=30))
        self.pay_in_fake(order)
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)

    def test_a_late_authenticated_payment_still_applies_to_a_window_expired_order(self):
        _, _, order = self.placed_xendit()
        age(order, days=2)
        PackOrder.objects.filter(pk=order.pk).update(xendit_expires_at=timezone.now() - timedelta(hours=3))
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)
        self.pay_in_fake(order)
        self.webhook(order, webhook_id="late")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)

    def test_only_awaiting_orders_are_swept(self):
        _, _, order = self.placed_xendit()
        self.webhook(order)
        age(order, days=3)
        PackOrder.objects.filter(pk=order.pk).update(xendit_expires_at=timezone.now() - timedelta(days=2))
        reconcile.run()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)

    def test_another_sites_order_is_expired_by_its_own_window_not_ours(self):
        _, _, order = self.placed_xendit()
        self.assertFalse(xendit.expire_overdue(self.other_site, order.xendit_external_id, timezone.now() + timedelta(days=30)) is False)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)  # (site, external_id) match only
