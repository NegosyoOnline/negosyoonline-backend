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


@override_settings(RETENTION_ENABLED=True)
class RegistrationDetailsRetentionTests(PackTestCase):
    """TWCinc's 90-day erase of the date of birth and the two Santé usernames."""

    def setUp(self):
        super().setUp()
        from apps.pack.models import SanteRegistration
        self.placed()
        self.reg = SanteRegistration.objects.get()

    def fill(self, status, age_days=91):
        from apps.pack.models import SanteRegistration
        now = timezone.now()
        SanteRegistration.objects.filter(pk=self.reg.pk).update(
            status=status, date_of_birth=now.date().replace(year=1990), desired_username="mariasantos1",
            alternate_username="mariasantos2", details_confirmed_at=now,
            status_entered_at=now - timedelta(days=age_days),
        )

    def run_rule(self, dry_run=False):
        return retention.registration_details(timezone.now(), dry_run)

    def test_a_waiting_registration_is_erased_and_goes_to_needs_buyer_action_t24(self):
        from apps.pack.models import SanteRegistration, SanteRegistrationEvent
        self.fill("waiting_for_accept")
        self.assertEqual(self.run_rule(), 1)
        reg = SanteRegistration.objects.get()
        self.assertEqual((reg.date_of_birth, reg.desired_username, reg.alternate_username), (None, "", ""))
        self.assertEqual((reg.status, reg.details_confirmed_at is None), ("needs_buyer_action", True))
        self.assertIsNotNone(reg.details_purged_at)
        event = SanteRegistrationEvent.objects.last()
        self.assertEqual((event.edge, event.reason_code), ("T24", "details_expired"))

    def test_a_row_waiting_on_the_customer_loses_its_confirmation_and_keeps_its_status(self):
        from apps.pack.models import SanteRegistration
        self.fill("awaiting_buyer_details")
        self.run_rule()
        reg = SanteRegistration.objects.get()
        self.assertEqual((reg.status, reg.details_confirmed_at, reg.date_of_birth), ("awaiting_buyer_details", None, None))

    def test_a_row_younger_than_ninety_days_or_without_details_is_left_alone(self):
        self.fill("waiting_for_accept", age_days=89)
        self.assertEqual(self.run_rule(), 0)
        self.fill("waiting_for_accept")
        from apps.pack.models import SanteRegistration
        SanteRegistration.objects.update(date_of_birth=None, desired_username="", alternate_username="")
        self.assertEqual(self.run_rule(), 0)

    def test_a_dry_run_counts_and_changes_nothing(self):
        from apps.pack.models import SanteRegistration
        self.fill("waiting_for_accept")
        self.assertEqual(self.run_rule(dry_run=True), 1)
        self.assertEqual(SanteRegistration.objects.get().desired_username, "mariasantos1")

    def test_disposal_blanks_the_details_and_keeps_the_row(self):
        from apps.pack.models import SanteRegistration
        self.fill("waiting_for_accept", age_days=1)
        counts = retention.blank_packs_of(self.site.account)
        self.assertEqual(counts["sante_registrations"], 1)
        reg = SanteRegistration.objects.get()
        self.assertEqual((reg.date_of_birth, reg.desired_username), (None, ""))

    def test_the_export_lists_registrations(self):
        self.fill("waiting_for_accept", age_days=1)
        row = retention.export_packs_of(self.site.account)["sante_registrations"][0]
        self.assertEqual((row["desired_username"], row["status"]), ("mariasantos1", "waiting_for_accept"))
