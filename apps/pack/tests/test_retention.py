"""Disposal, export and the old-token rule for the pack rows."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.accounts import retention as rules
from apps.pack import retention
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission, PackVisit
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

    def test_export_lists_the_sites_live_visits_with_the_exact_fields(self):
        now = timezone.now()
        order = PackOrder.objects.get()
        PackVisit.objects.create(
            site=self.site, visitor_ref="v" * 20, started_watching_at=now, video_percent=50,
            contact_name="Maria Cart", contact_mobile="09171234567", contact_email="m@buyer.test",
            contact_saved_at=now, order=order)
        PackVisit.objects.create(site=self.site, visitor_ref="w" * 20)
        PackVisit.objects.create(site=self.other_site, visitor_ref="x" * 20, contact_name="Other Site Person")
        rows = retention.export_packs_of(self.account_row)["pack_visits"]
        self.assertEqual(len(rows), 2)
        cart, landed = rows
        self.assertEqual((cart["order"], cart["stage"], landed["order"], landed["stage"]),
                         (order.public_id, "start_watching", None, "landing_page"))
        self.assertEqual(sorted(cart), sorted([
            "site", "order", "stage", "video_percent", "landed_at", "started_watching_at", "done_watching_at",
            "checkout_started_at", "contact_name", "contact_mobile", "contact_email", "contact_saved_at",
            "last_activity_at", "created_at"]))
        self.assertEqual((cart["contact_name"], cart["contact_mobile"], cart["contact_email"], cart["video_percent"]),
                         ("Maria Cart", "09171234567", "m@buyer.test", 50))
        text = str(rows)
        self.assertNotIn("v" * 20, text)  # the browser token
        self.assertNotIn("Other Site Person", text)
        self.assertEqual(rows[0]["site"], self.site.public_id)

    def test_export_has_no_deleted_or_blanked_visit_contact(self):
        old = timezone.now() - retention.VISIT_RETENTION - timedelta(days=1)
        PackVisit.objects.create(site=self.site, visitor_ref="a" * 20, contact_name="Gone", last_activity_at=old)
        PackVisit.objects.create(
            site=self.site, visitor_ref="b" * 20, contact_name="Blanked", contact_mobile="0917", order=PackOrder.objects.get(),
            last_activity_at=old)
        retention.old_visits(timezone.now(), False)
        rows = retention.export_packs_of(self.account_row)["pack_visits"]
        self.assertEqual([(r["contact_name"], r["contact_mobile"]) for r in rows], [("", "")])
        self.assertNotIn("Gone", str(rows))

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

    def test_blanking_keeps_the_shipment(self):
        PackOrder.objects.update(
            payment_status="verified", courier="lbc", tracking_number="LBC1", shipment_recorded_at=timezone.now(),
        )
        retention.blank_packs_of(self.account_row)
        order = PackOrder.objects.get()
        self.assertEqual((order.buyer_name, order.courier, order.tracking_number), ("", "lbc", "LBC1"))

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


@override_settings(DOB_ERASE_ENABLED=True)
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

    def test_the_export_carries_member_id_and_registered_at(self):
        from apps.pack.models import SanteRegistration
        SanteRegistration.objects.update(sante_member_id="SM123", registered_at=timezone.now())
        row = retention.export_packs_of(self.site.account)["sante_registrations"][0]
        self.assertEqual(row["sante_member_id"], "SM123")
        self.assertTrue(row["registered_at"])

    def test_disposal_blanks_member_id_keeps_registered_at(self):
        from apps.pack.models import SanteRegistration
        SanteRegistration.objects.update(
            sante_member_id="SM123", registered_at=timezone.now(), date_of_birth=None,
            desired_username="", alternate_username="",
        )
        self.assertEqual(retention.blank_packs_of(self.site.account, dry_run=True)["sante_registrations"], 1)
        self.assertEqual(SanteRegistration.objects.get().sante_member_id, "SM123")
        self.assertEqual(retention.blank_packs_of(self.site.account)["sante_registrations"], 1)
        reg = SanteRegistration.objects.get()
        self.assertEqual(reg.sante_member_id, "")
        self.assertIsNotNone(reg.registered_at)
        self.assertIn("sante_member_id", retention.blanked()["sante_registrations"])

    def test_another_sites_disposal_leaves_member_id(self):
        from apps.pack.models import SanteRegistration
        SanteRegistration.objects.update(sante_member_id="SM123")
        retention.blank_packs_of(self.other_site.account)
        self.assertEqual(SanteRegistration.objects.get().sante_member_id, "SM123")

    def test_the_details_purge_keeps_member_id(self):
        from apps.pack.models import SanteRegistration
        self.fill("waiting_for_accept")
        SanteRegistration.objects.update(sante_member_id="SM123")
        self.run_rule()
        self.assertEqual(SanteRegistration.objects.get().sante_member_id, "SM123")

    def test_the_export_lists_registrations(self):
        self.fill("waiting_for_accept", age_days=1)
        row = retention.export_packs_of(self.site.account)["sante_registrations"][0]
        self.assertEqual((row["desired_username"], row["status"]), ("mariasantos1", "waiting_for_accept"))


class RegistrationDetailsSwitchTests(PackTestCase):
    """Ruling D3: the erase answers to DOB_ERASE_ENABLED, not to RETENTION_ENABLED."""

    def setUp(self):
        super().setUp()
        from apps.common import retention as registry
        from apps.pack.models import SanteRegistration
        self.registry = registry
        self.placed()
        now = timezone.now()
        SanteRegistration.objects.update(
            status="waiting_for_accept", date_of_birth=now.date().replace(year=1990),
            desired_username="mariasantos1", alternate_username="mariasantos2",
            status_entered_at=now - timedelta(days=91),
        )

    def held(self):
        from apps.pack.models import SanteRegistration
        return SanteRegistration.objects.get().date_of_birth is not None

    @override_settings(RETENTION_ENABLED=True, DOB_ERASE_ENABLED=False)
    def test_retention_on_and_dob_off_erases_nothing_and_reports_it_switched_off(self):
        report = self.registry.run_all(only=["pack_registration_details"])
        self.assertEqual(report.switched_off, ["pack_registration_details"])
        self.assertTrue(self.held())

    @override_settings(RETENTION_ENABLED=False, DOB_ERASE_ENABLED=True)
    def test_dob_on_and_retention_off_erases(self):
        report = self.registry.run_all(only=["pack_registration_details"])
        self.assertEqual(report.counts, {"pack_registration_details": 1})
        self.assertEqual(report.switched_off, [])
        self.assertFalse(self.held())

    @override_settings(RETENTION_ENABLED=True, DOB_ERASE_ENABLED=True, PACK_ENABLED=False)
    def test_pack_off_does_nothing_and_reads_no_pack_table(self):
        with self.assertNumQueries(0):
            self.assertEqual(retention.registration_details(timezone.now(), False), 0)
            self.assertEqual(retention.registration_details(timezone.now(), True), 0)

    @override_settings(RETENTION_ENABLED=False, DOB_ERASE_ENABLED=False)
    def test_the_dry_run_counts_with_both_switches_off(self):
        report = self.registry.run_all(dry_run=True, only=["pack_registration_details"])
        self.assertEqual(report.counts, {"pack_registration_details": 1})
        self.assertTrue(self.held())
