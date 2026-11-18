"""The local preview's fake Xendit page: it plays Xendit's side through the real webhook, for the
Supervisor's visual test. It exists only with XENDIT_FAKE (DJANGO_DEBUG=true) and makes no network call."""
from django.http import Http404
from django.test import RequestFactory, override_settings

from apps.pack import constants as C
from apps.pack.models import PackOrder
from apps.pack.tests.base import HOST
from apps.pack.tests.xendit_base import XenditPackCase
from apps.payments import fake_views
from apps.payments.models import XenditEvent


class FakePreviewTests(XenditPackCase):
    def page(self, order, method="get", **data):
        request = getattr(RequestFactory(), method)(f"/fake-xendit/invoice/{order.xendit_invoice_id}/", data, HTTP_HOST="testserver")
        return fake_views.invoice(request, order.xendit_invoice_id)

    def test_the_page_shows_the_server_amount_and_the_three_buttons(self):
        _, _, order = self.placed_xendit()
        response = self.page(order)
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn("PHP 1,488.00", text)
        self.assertIn("no real payment", text)
        for action in ("pay", "pay_wrong", "expire"):
            self.assertIn(f'value="{action}"', text)

    def test_pay_marks_the_order_paid_through_the_real_webhook_and_returns_to_the_store(self):
        _, _, order = self.placed_xendit()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.page(order, "post", action="pay")
        self.assertEqual((response.status_code, response["Location"]), (302, f"https://{HOST}{C.RETURN_PATH}"))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertEqual(XenditEvent.objects.get().source, "webhook")

    def test_a_different_amount_is_a_mismatch_and_expire_closes_it(self):
        _, _, order = self.placed_xendit()
        self.page(order, "post", action="pay_wrong")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_MISMATCH)

    def test_expire_closes_the_order_and_a_closed_invoice_cannot_be_paid(self):
        _, _, order = self.placed_xendit()
        self.page(order, "post", action="expire")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)
        self.page(order, "post", action="pay")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)
        self.assertIn("closed", self.page(order).content.decode())

    @override_settings(XENDIT_FAKE=False)
    def test_without_the_fake_switch_the_page_is_404(self):
        with self.assertRaises(Http404):
            fake_views.invoice(RequestFactory().get("/x"), "any")
