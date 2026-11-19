"""Placing a Membership Pack order by Xendit (Coach Ed, 2026-11-18): the order awaits payment with
benefits off, the invoice is made with the site's own key, and nothing the buyer's browser sends can
mark it paid."""
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings

from apps.common.errors import ApiError
from apps.pack import constants as C, xendit
from apps.pack.models import PackOrder, SanteRegistration
from apps.pack.tests.base import HOST, OTHER_HOST
from apps.pack.tests.xendit_base import XenditPackCase, API_KEY, fake_xendit, give_key
from apps.payments.models import SitePaymentSettings, SiteXenditCredential


class PlaceTests(XenditPackCase):
    def test_a_xendit_order_awaits_payment_with_benefits_off_and_a_hosted_invoice(self):
        token, body, order = self.placed_xendit()
        self.assertEqual(order.payment_method, "xendit")
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)
        self.assertIsNone(order.benefits_active_at)
        self.assertIsNone(order.paid_at)
        self.assertEqual(order.price_centavos, 148800)
        self.assertRegex(order.xendit_external_id, rf"^np-{self.site.pk}-[0-9a-f]{{24}}$")
        self.assertEqual(body["pay_url"], order.xendit_invoice_url)
        self.assertTrue(body["pay_url"].startswith("http"))
        self.assertEqual(body["replay"], False)
        invoice = self.invoice_of(order)
        self.assertEqual(invoice["amount"], 1488.0)  # the server's constant, in pesos
        self.assertEqual(invoice["currency"], "PHP")
        self.assertEqual(invoice["external_id"], order.xendit_external_id)
        self.assertEqual(invoice["success_redirect_url"], f"https://{HOST}{C.RETURN_PATH}")
        self.assertEqual(invoice["failure_redirect_url"], f"https://{HOST}{C.RETURN_PATH}")

    def test_no_registration_no_proof_and_no_order_received_email_until_it_is_paid(self):
        _, _, order = self.placed_xendit()
        self.assertFalse(SanteRegistration.objects.exists())
        self.assertEqual(order.submissions.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual([e.kind for e in order.events.all()], ["placed"])

    def test_the_amount_is_the_servers_whatever_the_buyer_posts(self):
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place_xendit(token, amount_sent="1.00", amount="1")
        self.assertEqual(response.status_code, 200, response.content)
        order = PackOrder.objects.get()
        self.assertEqual(order.price_centavos, 148800)
        self.assertEqual(self.invoice_of(order)["amount"], 1488.0)

    def test_a_replay_returns_the_same_order_and_makes_no_second_invoice(self):
        token, first, order = self.placed_xendit()
        calls_before = len(fake_xendit.calls)
        again = self.place_xendit(token)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json(), {**first, "replay": True})
        self.assertEqual(PackOrder.objects.count(), 1)
        self.assertEqual(len(fake_xendit.calls), calls_before)

    def test_an_existing_unexpired_invoice_of_ours_is_adopted_not_duplicated(self):
        token = self.start()
        original = xendit.new_external_id
        fixed = original(self.site)
        pre = fake_xendit.call(API_KEY, "POST", "/v2/invoices", payload={
            "external_id": fixed, "amount": 1488.0, "currency": "PHP", "invoice_duration": 86400,
            "success_redirect_url": "x", "failure_redirect_url": "x",
        })
        with mock.patch.object(xendit, "new_external_id", return_value=fixed):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.place_xendit(token)
        self.assertEqual(response.status_code, 200, response.content)
        order = PackOrder.objects.get()
        self.assertEqual(order.xendit_invoice_id, pre["id"])
        self.assertEqual(len([c for c in fake_xendit.calls if c == ("POST", "/v2/invoices")]), 1)

    def test_when_only_expired_invoices_exist_a_new_one_is_made(self):
        token = self.start()
        fixed = xendit.new_external_id(self.site)
        old = fake_xendit.call(API_KEY, "POST", "/v2/invoices", payload={
            "external_id": fixed, "amount": 1488.0, "currency": "PHP", "invoice_duration": 86400,
            "success_redirect_url": "x", "failure_redirect_url": "x",
        })
        fake_xendit.expire(old["id"])
        with mock.patch.object(xendit, "new_external_id", return_value=fixed):
            with self.captureOnCommitCallbacks(execute=True):
                self.place_xendit(token)
        order = PackOrder.objects.get()
        self.assertNotEqual(order.xendit_invoice_id, old["id"])

    def test_an_unreachable_xendit_leaves_the_order_awaiting_with_no_invoice_and_try_again_works(self):
        give_key(self.site, api_key="xnd_development_down")
        token, body, order = self.placed_xendit()
        self.assertIsNone(body["pay_url"])
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)
        self.assertEqual(order.xendit_invoice_url, "")
        answer = self.status(token).json()
        self.assertEqual((answer["payment_status"], answer["order_number"], answer["pay_url"]), ("no_invoice", order.order_number, None))
        give_key(self.site)  # the key works again
        retry = self.status(token, retry=True)
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(retry.json()["payment_status"], "waiting")
        self.assertTrue(retry.json()["pay_url"])
        self.assertEqual(PackOrder.objects.count(), 1)  # the same order, never a new one

    def test_a_rejected_key_marks_it_failed_and_xendit_is_no_longer_offered(self):
        give_key(self.site, api_key="xnd_development_rejected")
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place_xendit(token)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["pay_url"])
        self.assertIsNotNone(SiteXenditCredential.objects.get(site=self.site).key_failed_at)
        self.assertFalse(self.get_pack()["methods"]["xendit"])

    def test_retry_is_limited_to_five_an_hour(self):
        give_key(self.site, api_key="xnd_development_down")
        token, _, order = self.placed_xendit()
        for _ in range(5):
            self.assertEqual(self.status(token, retry=True).status_code, 200)
        self.assertEqual(self.status(token, retry=True).status_code, 429)

    def test_retry_with_an_invoice_already_stored_changes_nothing(self):
        token, _, order = self.placed_xendit()
        calls_before = len(fake_xendit.calls)
        response = self.status(token, retry=True)
        self.assertEqual(response.json()["payment_status"], "waiting")
        self.assertEqual(len([c for c in fake_xendit.calls[calls_before:] if c[0] == "POST"]), 0)


class MethodAvailabilityTests(XenditPackCase):
    def refused(self, token):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place_xendit(token)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "method_unavailable")
        self.assertEqual(PackOrder.objects.count(), 0)

    def test_refused_when_the_owner_has_it_switched_off(self):
        SitePaymentSettings.objects.filter(site=self.site).update(xendit_enabled=False)
        self.refused(self.start())

    def test_refused_without_a_saved_key(self):
        SiteXenditCredential.objects.filter(site=self.site).update(api_key_ciphertext="")
        self.refused(self.start())

    def test_refused_after_the_key_failed(self):
        from django.utils import timezone
        SiteXenditCredential.objects.filter(site=self.site).update(key_failed_at=timezone.now())
        self.refused(self.start())

    @override_settings(XENDIT_ENABLED=False)
    def test_refused_while_the_platform_switch_is_off(self):
        self.refused(self.start())

    def test_refused_on_a_site_that_is_not_a_sante_site(self):
        from apps.sante.tests.base import retemplate
        token = self.start()
        retemplate(self.site)  # the storefront refuses a non-Santé host outright
        response = self.place_xendit(token)
        self.assertEqual(response.status_code, 404)

    def test_an_unknown_pay_method_is_refused(self):
        token = self.start()
        response = self.place_xendit(token, pay_method="cod")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "method_unavailable")

    def test_direct_is_refused_when_the_owner_switched_it_off_but_xendit_still_works(self):
        SitePaymentSettings.objects.filter(site=self.site).update(direct_enabled=False)
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            direct = self.place(token)
        self.assertEqual(direct.status_code, 409, direct.content)
        self.assertEqual(direct.json()["error"]["code"], "method_unavailable")
        with self.captureOnCommitCallbacks(execute=True):
            ok = self.place_xendit(token, submit_key="key-2")
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_the_pack_read_says_which_methods_are_offered(self):
        body = self.get_pack()
        self.assertEqual(body["methods"], {"direct": True, "xendit": True})
        self.assertTrue(body["available"])
        SitePaymentSettings.objects.filter(site=self.site).update(direct_enabled=False)
        body = self.get_pack()
        self.assertEqual(body["methods"], {"direct": False, "xendit": True})
        self.assertEqual(body["payment_accounts"], [])
        self.assertTrue(body["available"])  # Xendit alone is enough
        SitePaymentSettings.objects.filter(site=self.site).update(xendit_enabled=False)
        self.assertFalse(self.get_pack()["available"])

    @override_settings(XENDIT_ENABLED=False)
    def test_with_the_platform_switch_off_the_pack_read_offers_direct_only(self):
        self.assertEqual(self.get_pack()["methods"], {"direct": True, "xendit": False})


class InvoiceRaceTests(XenditPackCase):
    def test_a_second_call_waits_for_the_first_invoice_and_never_creates_another(self):
        _, _, order = self.placed_xendit()
        PackOrder.objects.filter(pk=order.pk).update(xendit_invoice_url="", xendit_invoice_id="")
        cache.set(f"pack-invoice-claim:{order.pk}", 1, 60)  # another request holds the invoice step
        calls_before = len(fake_xendit.calls)

        def other_request_stores_it(_seconds):
            PackOrder.objects.filter(pk=order.pk).update(xendit_invoice_url="https://pay.example/inv", xendit_invoice_id="inv-1")

        with mock.patch.object(xendit, "_sleep", other_request_stores_it):
            got = xendit.ensure_invoice(order.pk)
        self.assertEqual(got.xendit_invoice_url, "https://pay.example/inv")
        self.assertEqual(len(fake_xendit.calls), calls_before)

    def test_a_claim_that_is_never_released_answers_busy_after_the_wait(self):
        _, _, order = self.placed_xendit()
        PackOrder.objects.filter(pk=order.pk).update(xendit_invoice_url="", xendit_invoice_id="")
        cache.set(f"pack-invoice-claim:{order.pk}", 1, 60)
        with mock.patch.object(xendit, "_sleep"):
            with self.assertRaises(ApiError) as caught:
                xendit.ensure_invoice(order.pk)
        self.assertEqual((caught.exception.code, caught.exception.status), ("invoice_busy", 409))

    def test_a_webhook_that_got_there_first_wins_over_the_late_invoice_store(self):
        _, _, order = self.placed_xendit()
        PackOrder.objects.filter(pk=order.pk).update(xendit_invoice_url="", xendit_invoice_id="")
        real = xendit.xendit_client.create_invoice

        def create_then_expire_order(*args, **kwargs):
            doc = real(*args, **kwargs)
            PackOrder.objects.filter(pk=order.pk).update(payment_status=C.XENDIT_EXPIRED)
            return doc

        with mock.patch.object(xendit.xendit_client, "find_invoices", return_value=[]), \
                mock.patch.object(xendit.xendit_client, "create_invoice", create_then_expire_order):
            got = xendit.ensure_invoice(order.pk)
        self.assertEqual(got.payment_status, C.XENDIT_EXPIRED)
        self.assertEqual(got.xendit_invoice_url, "")  # not stored on a closed order

