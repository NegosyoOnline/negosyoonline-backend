"""Disposal, export and the old-token rule for the pack rows."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.accounts import retention as rules
from apps.pack import retention
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission
from apps.pack.tests.base import PackTestCase


@override_settings(RETENTION_ENABLED=True)
class PackRetentionTests(PackTestCase):
    def setUp(self):
        super().setUp()
        self.placed()
        self.account_row = self.site.account

    def test_export_lists_orders_and_proofs_without_file_key_or_token(self):
        data = retention.export_packs_of(self.account_row)
        order, proof = data["pack_orders"][0], data["pack_proof_submissions"][0]
        self.assertEqual((order["number"], order["buyer_name"]), ("SMP00001", "Maria Santos"))
        self.assertNotIn("file_key", proof)
        self.assertNotIn("token", str(data))

    def test_blanking_deletes_files_and_tokens_and_keeps_rows_and_money(self):
        counts = retention.blank_packs_of(self.account_row)
        self.assertEqual(counts["pack_orders"], 1)
        self.assertEqual(self.proof_files(), [])
        self.assertEqual(CheckoutToken.objects.count(), 0)
        order = PackOrder.objects.get()
        self.assertEqual((order.buyer_name, order.delivery_address, order.price_centavos), ("", {}, 148800))
        proof = PackProofSubmission.objects.get()
        self.assertEqual((proof.sender_name, proof.file_key, proof.reference_number), ("", "", ""))
        self.assertEqual(proof.amount_sent_centavos, 148800)

    def test_a_dry_run_changes_nothing(self):
        retention.blank_packs_of(self.account_row, dry_run=True)
        self.assertEqual(len(self.proof_files()), 1)
        self.assertEqual(PackOrder.objects.get().buyer_name, "Maria Santos")

    def test_another_accounts_rows_are_untouched(self):
        retention.blank_packs_of(self.other_site.account)
        self.assertEqual(PackOrder.objects.get().buyer_name, "Maria Santos")

    def test_everything_is_empty_while_the_switch_is_off(self):
        with override_settings(PACK_ENABLED=False):
            self.assertEqual(retention.blank_packs_of(self.account_row), {})
            self.assertEqual(retention.export_packs_of(self.account_row), {})
            self.assertEqual(retention.blanked(), {})

    def test_the_old_token_rule_waits_the_grace_after_expiry(self):
        row = CheckoutToken.objects.get()
        now = timezone.now()
        CheckoutToken.objects.filter(pk=row.pk).update(expires_at=now - timedelta(days=1))
        self.assertEqual(retention.expired_checkout_tokens(now, False), 0)
        CheckoutToken.objects.filter(pk=row.pk).update(expires_at=now - retention.TOKEN_GRACE - timedelta(days=1))
        self.assertEqual(retention.expired_checkout_tokens(now, True), 1)
        self.assertEqual(CheckoutToken.objects.count(), 1)
        self.assertEqual(retention.expired_checkout_tokens(now, False), 1)
        self.assertEqual(CheckoutToken.objects.count(), 0)

    def test_account_disposal_and_export_carry_the_pack_rows(self):
        from apps.sante.tests.agreement_helpers import make_operator
        counts = rules.dispose_account(
            self.account_row, now=timezone.now(), rule=rules.REQUEST,
            actor=make_operator("dpo"), request_ref="DPO-2026-011",
        )
        self.assertEqual((counts["pack_orders"], counts["pack_proof_files"]), (1, 1))
        self.assertEqual(self.proof_files(), [])
        self.assertEqual(PackOrder.objects.get().buyer_name, "")
