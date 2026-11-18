"""Where a Xendit order shows (member panel, My Orders, the Subscriber's list), that existing
bank-transfer orders keep their review path with direct switched off, and that retention, purge and
export cover the new fields and never carry a secret."""
import json

from django.core import mail
from django.core.cache import cache
from django.test import override_settings

from apps.accounts import retention as rules
from apps.members import sessions
from apps.pack import constants as C, retention, review
from apps.pack.models import PackOrder
from apps.pack.tests.base import HOST
from apps.pack.tests.xendit_base import XenditPackCase, API_KEY, TOKEN, fake_xendit
from apps.payments import retention as payment_retention
from apps.payments.models import SitePaymentSettings, SiteXenditCredential
from apps.storefront.tests.base import SECRET


class MemberSurfaceCase(XenditPackCase):
    def member_post(self, path, session):
        return self.client.post(
            "/storefront/v1/member/" + path, {"host": HOST, "token": session}, content_type="application/json",
            secure=True, HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def signed_in(self, order):
        _, session = sessions.create_session(self.site, order.member)
        return session


class MemberPanelTests(MemberSurfaceCase):
    def test_an_awaiting_order_shows_its_own_pay_url_the_server_amount_and_the_method(self):
        _, body, order = self.placed_xendit()
        pack = self.member_post("pack/", self.signed_in(order)).json()["pack"]
        self.assertEqual(pack["payment_status"], "awaiting_xendit")
        self.assertEqual(pack["payment_label"], "Waiting for payment")
        self.assertEqual(pack["payment_method"], "xendit")
        self.assertEqual(pack["payment_method_label"], "Pay online (Xendit)")
        self.assertEqual(pack["method_label"], "Pay online (Xendit)")
        self.assertEqual(pack["pay_url"], body["pay_url"])
        self.assertEqual((pack["price_centavos"], pack["currency"]), (148800, "PHP"))
        self.assertIsNone(pack["paid_at"])
        self.assertEqual(pack["tone"], "rev")
        self.assertEqual(pack["proofs"], [])
        self.assertIsNone(pack["lock"])

    def test_expired_and_mismatch_states_have_their_chips_and_no_pay_url(self):
        _, _, order = self.placed_xendit()
        session = self.signed_in(order)
        self.webhook(order, status="EXPIRED")
        pack = self.member_post("pack/", session).json()["pack"]
        self.assertEqual((pack["payment_status"], pack["payment_label"], pack["tone"]), ("xendit_expired", "Payment not completed", "att"))
        self.assertIsNone(pack["pay_url"])
        PackOrder.objects.filter(pk=order.pk).update(payment_status=C.XENDIT_MISMATCH)
        pack = self.member_post("pack/", session).json()["pack"]
        self.assertEqual((pack["payment_status"], pack["payment_label"]), ("xendit_mismatch", "Under review"))

    def test_an_expired_invoice_url_is_not_offered(self):
        from datetime import timedelta
        from django.utils import timezone
        _, _, order = self.placed_xendit()
        PackOrder.objects.filter(pk=order.pk).update(xendit_expires_at=timezone.now() - timedelta(minutes=1))
        pack = self.member_post("pack/", self.signed_in(order)).json()["pack"]
        self.assertIsNone(pack["pay_url"])

    def test_a_paid_order_is_verified_with_paid_at_and_no_pay_url(self):
        _, _, order = self.placed_xendit()
        self.webhook(order)
        pack = self.member_post("pack/", self.signed_in(order)).json()["pack"]
        self.assertEqual((pack["payment_status"], pack["payment_method"], pack["tone"]), ("verified", "xendit", "ok"))
        self.assertEqual(pack["paid_at"], "2026-11-18T10:00:00+00:00")
        self.assertIsNone(pack["pay_url"])
        self.assertFalse(pack["benefits_off"])

    def test_my_orders_and_home_read_every_xendit_state(self):
        _, body, order = self.placed_xendit()
        session = self.signed_in(order)
        card = self.member_post("orders/", session).json()["cards"][0]
        self.assertEqual((card["payment_status"], card["payment_method"], card["awaiting_payment"]), ("awaiting_xendit", "xendit", True))
        self.assertEqual((card["payment_label"], card["pay_url"], card["chip_label"]), ("Pay online (Xendit)", body["pay_url"], "Waiting for payment"))
        self.assertEqual(self.member_post("home/", session).status_code, 200)
        self.webhook(order, status="EXPIRED")
        card = self.member_post("orders/", session).json()["cards"][0]
        self.assertEqual((card["chip_label"], card["problem"], card["problem_line"]), ("Payment not completed", True, "Your online payment was not completed."))
        self.assertFalse(card["awaiting_payment"])

    def test_a_paid_xendit_order_is_in_the_buyers_portal_like_any_verified_order(self):
        from apps.pack import portal
        _, _, order = self.placed_xendit()
        self.webhook(order)
        order.refresh_from_db()
        card = portal.card(order.member, order)
        self.assertEqual((card["payment_method"], card["payment_method_label"], card["payment_status"]), ("xendit", "Pay online (Xendit)", "verified"))
        self.assertEqual(card["paid_at"], "2026-11-18T10:00:00+00:00")
        self.assertEqual(card["total_text"], "1,488.00")
        self.assertEqual(card["currency"], "PHP")
        self.assertEqual(card["steps"][1]["status"], "done")

    def test_a_direct_order_card_is_the_same_as_before_plus_the_method_fields(self):
        token, _ = self.placed()
        order = PackOrder.objects.get()
        pack = self.member_post("pack/", self.signed_in(order)).json()["pack"]
        self.assertEqual((pack["payment_method"], pack["method_label"], pack["pay_url"], pack["paid_at"]),
                         ("direct", "Bank or e-wallet transfer", None, None))
        self.assertEqual(len(pack["proofs"]), 1)

    def onboarding_state(self, token):
        response = self.client.post(
            "/storefront/v1/onboarding/state/", {"host": HOST, "visitor_ip": "198.51.100.77", "token": token},
            content_type="application/json", secure=True, HTTP_X_REAL_IP="198.51.100.200",
            HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_the_onboarding_state_names_the_xendit_method(self):
        token, _, order = self.placed_xendit()
        self.webhook(order)
        self.assertEqual(self.onboarding_state(token)["payment_method_label"], "Pay online (Xendit)")

    def test_the_onboarding_state_names_the_transfer_method_for_a_bank_order(self):
        token, _ = self.placed()
        self.assertEqual(self.onboarding_state(token)["payment_method_label"], "Bank or e-wallet transfer")


class OwnerSurfaceTests(XenditPackCase):
    def setUp(self):
        super().setUp()
        self.owner = self.site.account

    def test_the_review_actions_refuse_a_xendit_order_that_has_no_proof(self):
        from apps.common.errors import ApiError
        _, _, order = self.placed_xendit()
        for call in (
            lambda: review.approve(self.owner, self.site, order.pk, state_version=order.state_version),
            lambda: review.reject(self.owner, self.site, order.pk, state_version=order.state_version, reason_code="other", other_text="x"),
        ):
            with self.assertRaises(ApiError) as caught:
                call()
            self.assertEqual(caught.exception.code, "wrong_status")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)

    def test_the_owner_row_names_the_method_and_paid_at(self):
        from apps.pack import api_owner
        _, _, order = self.placed_xendit()
        self.webhook(order)
        order.refresh_from_db()
        row = api_owner.row(order)
        self.assertEqual((row["method"], row["payment_method_label"], row["payment_status"]), ("xendit", "Pay online (Xendit)", "verified"))
        self.assertEqual(row["paid_at"], "2026-11-18T10:00:00+00:00")
        self.assertEqual(row["shipment_status"], "not_shipped")

    def test_a_paid_xendit_order_is_verified_so_shipping_and_registration_read_it_like_any_paid_order(self):
        _, _, order = self.placed_xendit()
        self.webhook(order)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)  # the same status shipping, registration and lists key on
        self.assertIn(order.payment_status, C.PAYMENT_OK)


class BankTransferStillWorksTests(XenditPackCase):
    """Existing bank-transfer orders keep review, proof and the member panel with direct switched off."""

    def test_review_works_for_an_order_placed_before_direct_was_switched_off(self):
        token, _ = self.placed()
        order = PackOrder.objects.get()
        SitePaymentSettings.objects.filter(site=self.site).update(direct_enabled=False)
        with self.captureOnCommitCallbacks(execute=True):
            review.approve(self.site.account, self.site, order.pk, state_version=order.state_version)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertIsNone(order.paid_at)
        self.assertEqual(order.payment_method, "direct")
        self.assertEqual(len([m for m in mail.outbox if "is verified" in m.subject]), 1)

    def test_reject_and_resubmit_still_work_with_direct_off(self):
        from apps.pack.tests.base import PNG, VISITOR
        from django.core.files.uploadedfile import SimpleUploadedFile
        token, _ = self.placed()
        order = PackOrder.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            review.reject(self.site.account, self.site, order.pk, state_version=order.state_version, reason_code="amount_mismatch")
        SitePaymentSettings.objects.filter(site=self.site).update(direct_enabled=False)
        _, session = sessions.create_session(self.site, order.member)
        data = {"host": HOST, "visitor_ip": VISITOR, **self.proof_fields(session, submit_key=None), "proof": SimpleUploadedFile("p.png", PNG, content_type="image/png")}
        data = {k: v for k, v in data.items() if v is not None}
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/storefront/v1/member/pack/resubmit/", data, secure=True,
                HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["pack"]["payment_status"], "pending_review")

    def test_the_member_panel_of_a_bank_order_still_shows_its_proof_and_accounts_with_direct_off(self):
        token, _ = self.placed()
        order = PackOrder.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            review.reject(self.site.account, self.site, order.pk, state_version=order.state_version, reason_code="amount_mismatch")
        SitePaymentSettings.objects.filter(site=self.site).update(direct_enabled=False)
        _, session = sessions.create_session(self.site, order.member)
        response = self.client.post(
            "/storefront/v1/member/pack/", {"host": HOST, "token": session}, content_type="application/json",
            secure=True, HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )
        pack = response.json()["pack"]
        self.assertEqual(pack["payment_status"], "rejected")
        self.assertEqual(len(pack["proofs"]), 1)
        self.assertEqual(pack["method_label"], "Bank or e-wallet transfer")

    def test_with_direct_off_the_buyer_cannot_start_a_new_bank_transfer(self):
        SitePaymentSettings.objects.filter(site=self.site).update(direct_enabled=False)
        token = self.start()
        response = self.place(token)
        self.assertEqual(response.status_code, 409)


class RetentionTests(XenditPackCase):
    def setUp(self):
        super().setUp()
        self.owner = self.site.account

    def test_the_account_export_carries_xendit_ids_and_never_a_secret(self):
        _, _, order = self.placed_xendit()
        self.webhook(order)
        order.refresh_from_db()
        data = payment_retention.export_payments_of(self.owner)
        packs = retention.export_packs_of(self.owner)
        text = json.dumps({**data, **packs})
        row = SiteXenditCredential.objects.get(site=self.site)
        for secret in (API_KEY, TOKEN, row.api_key_ciphertext, row.webhook_token_ciphertext, row.webhook_key, order.xendit_invoice_url):
            self.assertNotIn(secret, text)
        self.assertNotIn("ciphertext", text)
        self.assertEqual(data["site_xendit_credentials"], [{
            "site": self.site.public_id, "configured": True, "key_mode": "test", "key_failed_at": None,
            "updated_at": row.updated_at.isoformat(),
        }])
        self.assertEqual(data["site_payment_settings"][0]["xendit_enabled"], True)
        exported = next(o for o in packs["pack_orders"] if o["id"] == order.public_id)
        self.assertEqual(exported["payment_method"], "xendit")
        self.assertEqual(exported["xendit_external_id"], order.xendit_external_id)
        self.assertEqual(exported["xendit_invoice_id"], order.xendit_invoice_id)
        self.assertEqual(exported["paid_at"], "2026-11-18T10:00:00+00:00")
        self.assertEqual(exported["status"], "verified")

    def test_a_direct_order_exports_as_it_did_before(self):
        self.placed()
        exported = retention.export_packs_of(self.owner)["pack_orders"][0]
        self.assertNotIn("xendit_external_id", exported)
        self.assertNotIn("payment_method", exported)

    @override_settings(RETENTION_ENABLED=True)
    def test_account_disposal_wipes_the_key_and_its_audit_entry_names_no_secret(self):
        import json
        from django.utils import timezone
        from apps.operators.models import OperatorAuditEntry
        from apps.sante.tests.agreement_helpers import make_operator
        _, _, order = self.placed_xendit()
        counts = rules.dispose_account(
            self.owner, now=timezone.now(), rule=rules.REQUEST, actor=make_operator("dpo"), request_ref="DPO-2026-012",
        )
        self.assertEqual(counts["site_xendit_credentials"], 1)
        row = SiteXenditCredential.objects.get(site=self.site)
        self.assertEqual((row.api_key_ciphertext, row.webhook_token_ciphertext), ("", ""))
        entry = json.dumps(OperatorAuditEntry.objects.get(action="account_disposed").detail)
        self.assertNotIn(API_KEY, entry)
        self.assertNotIn(TOKEN, entry)

    @override_settings(RETENTION_ENABLED=True)
    def test_the_account_export_endpoint_data_holds_no_secret(self):
        import json
        from apps.sante.tests.agreement_helpers import make_operator
        self.placed_xendit()
        text = json.dumps(rules.export_account_data(self.owner, actor=make_operator("dpo"), request_ref="DPO-2"))
        row = SiteXenditCredential.objects.get(site=self.site)
        for secret in (API_KEY, TOKEN, row.api_key_ciphertext, row.webhook_token_ciphertext):
            self.assertNotIn(secret, text)
        self.assertIn("site_xendit_credentials", text)

    def test_disposal_wipes_the_key_and_token_keeps_the_row_and_counts_it(self):
        from django.utils import timezone
        now = timezone.now()
        dry = payment_retention.blank_payments_of(self.owner, now, dry_run=True)
        self.assertEqual(dry["site_xendit_credentials"], 1)
        row = SiteXenditCredential.objects.get(site=self.site)
        self.assertNotEqual(row.api_key_ciphertext, "")
        counts = payment_retention.blank_payments_of(self.owner, now)
        self.assertEqual(counts["site_xendit_credentials"], 1)
        row.refresh_from_db()
        self.assertEqual((row.api_key_ciphertext, row.webhook_token_ciphertext, row.key_mode), ("", "", ""))
        self.assertTrue(row.webhook_key)
        self.assertEqual(payment_retention.blank_payments_of(self.owner, now)["site_xendit_credentials"], 0)

    def test_another_accounts_key_is_not_touched(self):
        from django.utils import timezone
        from apps.pack.tests.xendit_base import give_key
        give_key(self.other_site)
        payment_retention.blank_payments_of(self.owner, timezone.now())
        self.assertNotEqual(SiteXenditCredential.objects.get(site=self.other_site).api_key_ciphertext, "")

    def test_a_site_with_no_xendit_row_has_the_old_counts_and_export(self):
        from django.utils import timezone
        SiteXenditCredential.objects.all().delete()
        counts = payment_retention.blank_payments_of(self.owner, timezone.now())
        self.assertNotIn("site_xendit_credentials", counts)
        self.assertNotIn("site_xendit_credentials", payment_retention.export_payments_of(self.owner))

    def test_the_buyer_purge_blanks_a_xendit_orders_personal_fields_and_keeps_its_provider_ids(self):
        from django.utils import timezone
        from apps.pack import purge
        _, _, order = self.placed_xendit()
        self.webhook(order)
        purge._clear_details(self.site.pk, timezone.now())
        order.refresh_from_db()
        self.assertEqual(order.delivery_address, {})
        self.assertNotEqual(order.buyer_name, "Maria Santos")
        self.assertTrue(order.xendit_external_id)
        self.assertTrue(order.xendit_invoice_id)
