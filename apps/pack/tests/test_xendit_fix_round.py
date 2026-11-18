"""Fix round F1, F3, F5, F6, F8 and the on-commit key-failed email (z8uq9mgknz)."""
import urllib.request
from decimal import Decimal
from unittest import mock

from django.core import checks, mail
from django.core.cache import cache
from django.test import RequestFactory, override_settings
from django.utils import timezone

from apps.pack import constants as C, xendit
from apps.pack.tests.xendit_base import XenditPackCase, fake_xendit
from apps.payments import fake_views, services as payments, xendit_client
from apps.payments.models import SiteXenditCredential


class KeepTokenOnDeleteTests(XenditPackCase):
    def test_f1_clearing_the_key_keeps_the_token_and_a_paid_callback_still_verifies(self):
        _, _, order = self.placed_xendit()
        owner = self.site.account
        payments.clear_xendit(owner, self.site)
        row = SiteXenditCredential.objects.get(site=self.site)
        self.assertEqual(row.api_key_ciphertext, "")
        self.assertNotEqual(row.webhook_token_ciphertext, "")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.webhook(order)
        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)


class FrozenPriceTests(XenditPackCase):
    def test_f5_the_amount_and_the_paid_check_use_the_orders_frozen_price(self):
        _, _, order = self.placed_xendit()
        self.assertEqual(xendit.amount(order), Decimal("1488"))
        with mock.patch.object(C, "PRICE_CENTAVOS", 999900):  # the constant moves on; the order does not
            self.assertEqual(xendit.amount(order), Decimal("1488"))
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.webhook(order).status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)

    def test_f5_the_invoice_is_created_for_the_orders_frozen_price(self):
        _, _, order = self.placed_xendit()
        self.assertEqual(float(fake_xendit.get(order.xendit_invoice_id)["amount"]), 1488.0)
        with mock.patch.object(C, "PRICE_CENTAVOS", 999900):
            self.assertEqual(xendit.amount(order), Decimal("1488"))


class TimeoutTests(XenditPackCase):
    def test_f3_connect_5_read_20_and_the_claim_outlives_find_plus_create(self):
        self.assertEqual((xendit_client.CONNECT_TIMEOUT, xendit_client.READ_TIMEOUT), (5, 20))
        self.assertGreater(xendit.CLAIM_SECONDS, 2 * (5 + 20))
        seen = {}

        def fake_open(request, timeout=None):
            seen["timeout"] = timeout
            raise OSError("stop")

        with mock.patch.object(xendit_client._opener, "open", fake_open):
            with self.assertRaises(xendit_client.XenditError):
                with override_settings(XENDIT_FAKE=False):
                    xendit_client.check_key("xnd_development_k")
        self.assertEqual(seen["timeout"], 5)  # the connection opens within 5 s

    def test_f3_the_socket_gets_the_read_timeout_once_connected(self):
        conn = xendit_client._Connection("api.xendit.co", timeout=5)
        with mock.patch("http.client.HTTPSConnection.connect"):
            conn.sock = mock.Mock()
            conn.connect()
        conn.sock.settimeout.assert_called_once_with(20)

    def test_f3_the_claim_is_held_for_the_claim_seconds(self):
        _, _, order = self.placed_xendit()
        with mock.patch.object(cache, "add", return_value=True) as add:
            order.xendit_invoice_url = ""
            from apps.pack.models import PackOrder
            PackOrder.objects.filter(pk=order.pk).update(xendit_invoice_url="")
            xendit.ensure_invoice(order.pk)
        self.assertEqual(add.call_args.args[2], xendit.CLAIM_SECONDS)


class FakeModeTests(XenditPackCase):
    @override_settings(RAILWAY_ENVIRONMENT_NAME="staging")
    def test_f6_the_fake_page_is_404_on_a_railway_environment(self):
        from django.http import Http404
        with self.assertRaises(Http404):
            fake_views.invoice(RequestFactory().get("/x"), "any")

    def test_f6_the_deploy_check_refuses_the_fake_without_debug_or_on_railway(self):
        from apps.payments.checks import check_xendit_fake_is_local
        with mock.patch.dict("os.environ", {"XENDIT_FAKE": "1"}):
            with override_settings(DEBUG=False, RAILWAY_ENVIRONMENT_NAME=""):
                self.assertEqual([e.id for e in check_xendit_fake_is_local(None)], ["payments.E002"])
            with override_settings(DEBUG=True, RAILWAY_ENVIRONMENT_NAME="staging"):
                self.assertEqual([e.id for e in check_xendit_fake_is_local(None)], ["payments.E002"])
            with override_settings(DEBUG=True, RAILWAY_ENVIRONMENT_NAME=""):
                self.assertEqual(check_xendit_fake_is_local(None), [])
        with mock.patch.dict("os.environ", {"XENDIT_FAKE": ""}):
            with override_settings(DEBUG=False):
                self.assertEqual(check_xendit_fake_is_local(None), [])

    def page_redirect(self, order):
        request = RequestFactory().post(f"/fake-xendit/invoice/{order.xendit_invoice_id}/", {"action": "pay"}, HTTP_HOST="testserver")
        return fake_views.invoice(request, order.xendit_invoice_id)["Location"]

    def test_f8_the_return_origin_replaces_the_public_host_in_fake_mode(self):
        _, _, order = self.placed_xendit()
        with override_settings(XENDIT_FAKE_RETURN_ORIGIN="http://localhost:3002"):
            self.assertEqual(self.page_redirect(order), f"http://localhost:3002{C.RETURN_PATH}")

    def test_f8_without_the_origin_the_link_is_the_sites_public_host(self):
        _, _, order = self.placed_xendit()
        with override_settings(XENDIT_FAKE_RETURN_ORIGIN=""):
            self.assertTrue(self.page_redirect(order).startswith("https://"))

    def test_f8_the_origin_is_never_applied_to_a_real_invoice_request(self):
        # The real create call sends the site's public host; the origin only rewrites the fake page's redirect.
        with override_settings(XENDIT_FAKE_RETURN_ORIGIN="http://localhost:3002"):
            _, _, order = self.placed_xendit()
        doc = fake_xendit.get(order.xendit_invoice_id)
        self.assertTrue(doc["success_redirect_url"].startswith("https://"))

    def test_f8_the_setting_is_empty_unless_the_fake_is_on(self):
        import importlib, os
        from config import settings as cfg
        with mock.patch.dict(os.environ, {"XENDIT_FAKE_RETURN_ORIGIN": "http://localhost:3002", "XENDIT_FAKE": ""}):
            reloaded = importlib.reload(cfg)
            self.assertEqual(reloaded.XENDIT_FAKE_RETURN_ORIGIN, "")
        with mock.patch.dict(os.environ, {"XENDIT_FAKE_RETURN_ORIGIN": "http://localhost:3002/", "XENDIT_FAKE": "1", "DJANGO_DEBUG": "true", "RAILWAY_ENVIRONMENT_NAME": ""}):
            reloaded = importlib.reload(cfg)
            self.assertEqual(reloaded.XENDIT_FAKE_RETURN_ORIGIN, "http://localhost:3002")
        with mock.patch.dict(os.environ, {"XENDIT_FAKE_RETURN_ORIGIN": "http://localhost:3002", "XENDIT_FAKE": "1", "DJANGO_DEBUG": "true", "RAILWAY_ENVIRONMENT_NAME": "staging"}):
            reloaded = importlib.reload(cfg)
            self.assertEqual((reloaded.XENDIT_FAKE, reloaded.XENDIT_FAKE_RETURN_ORIGIN), (False, ""))
        importlib.reload(cfg)


class KeyFailedEmailTests(XenditPackCase):
    def test_the_key_failed_email_waits_for_the_commit(self):
        with mock.patch("apps.notifications.services.send_email") as send:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                payments.mark_key_failed(self.site)
            self.assertEqual(send.call_count, 0)
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            self.assertEqual(send.call_count, 1)
