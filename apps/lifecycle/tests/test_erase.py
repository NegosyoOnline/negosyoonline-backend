"""What an approved run does to one ended site (plan z8uq9mb22v, sections 3, 6.4, 6.5, 7 and 9)."""
import json
from datetime import date
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import override_settings

from apps.accounts import retention as account_retention
from apps.accounts.models import Account
from apps.billing.models import BankTransferPayment, Subscription
from apps.common import private_files
from apps.lifecycle import eligibility, erase, workflow
from apps.lifecycle.models import ErasureBatch, ErasureHold, ErasureItem
from apps.lifecycle.tests.base import DUE, ENDED, NOW, State, days, ended_site, open_charge, second_site
from apps.lifecycle.tests.run_base import (
    EMAIL, MOBILE, NAME, NUMBER, RunTestCase, add_due_site, every_row, kept_rows, staff_user,
)
from apps.members.models import Member, MemberOtpChallenge, MemberSession
from apps.meta_ads.models import MetaOrderContext, MetaSettings
from apps.operators.audit import ACTIONS, KEPT
from apps.operators.models import OperatorAuditEntry
from apps.inbox.models import Contact, ContactGroup, Conversation, ConversationMessage, DoNotText
from apps.inquiries import retention as inquiries_retention
from apps.inquiries.models import InquiryConnection, InquiryConversation
from apps.inquiries.tests.base import make_stored
from apps.pack import purge
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission, SanteRegistration
from apps.payments.models import SitePaymentAccount
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.shop.models import ShopOrder, ShopProofSubmission
from apps.sites.models import Site, SiteBusinessProfile, Subdomain

PLANTED = (NAME, EMAIL, MOBILE, NUMBER, "Planted Trading", "PLANTED-SPONSOR", "CIPHER", "TEST123")


class PerTableTests(RunTestCase):
    def setUp(self):
        super().setUp()
        self.rows_before = {
            "orders": PackOrder.objects.count(), "shop_orders": ShopOrder.objects.count(),
            "members": Member.objects.count(), "accounts": SitePaymentAccount.objects.count(),
            "proofs": PackProofSubmission.objects.count(),
            "shop_proofs": ShopProofSubmission._base_manager.count(),
            "subdomains": Subdomain.objects.count(), "sites": Site.objects.count(),
        }
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", None):  # gate closed
            self.result = self.run_batch(self.approved())

    def test_the_run_erases_the_site(self):
        self.assertEqual(self.result["erased"], 1)
        self.assertEqual(self.result["status"], ErasureBatch.Status.DONE)

    def test_personal_fields_are_blank_and_kept_fields_stay(self):
        site = Site.objects.get(pk=self.site.pk)
        self.assertEqual((site.contact_email, site.contact_mobile, site.facebook_url), ("", "", ""))
        self.assertEqual(site.display_name, f"Deleted site #{site.pk}")
        self.assertIsNotNone(site.deleted_at)
        self.assertEqual((site.public_id, site.template), (self.site.public_id, "sante"))
        profile = SiteBusinessProfile.objects.get(site=site)
        self.assertEqual((profile.business_name, profile.tin, profile.seal_key), ("", "", ""))
        self.assertEqual(Subdomain.objects.get(site=site).state, "retired")
        self.assertEqual(SanteSiteSettings.objects.get(site=site).sponsor_id, "")
        pay = SitePaymentAccount.objects.filter(site=site).first()
        self.assertEqual((pay.account_name, pay.account_number, pay.qr_file_key), ("", "", ""))
        self.assertIsNotNone(pay.removed_at)
        self.assertEqual((pay.kind, pay.provider_name), ("bank", "BDO"))
        meta = MetaSettings.objects.get(site=site)
        self.assertEqual((meta.token_encrypted, meta.test_event_code), ("", ""))
        self.assertEqual(MetaOrderContext.objects.filter(site=site).count(), 0)

    def test_members_orders_and_proofs_are_anonymised_in_place(self):
        member = Member.objects.get(pk=self.planted["member"].pk)
        self.assertEqual((member.full_name, member.delivery_address), ("", {}))
        self.assertNotIn(EMAIL, member.email)
        self.assertNotEqual(member.mobile, MOBILE)
        self.assertEqual(member.public_id, self.planted["member"].public_id)
        self.assertEqual(MemberSession.objects.filter(site=self.site).count(), 0)
        self.assertEqual(MemberOtpChallenge.objects.filter(site=self.site).count(), 0)
        order = PackOrder.objects.get(pk=self.planted["pack_order"].pk)
        self.assertEqual((order.buyer_name, order.delivery_address), (purge.BUYER_PLACEHOLDER, {}))
        self.assertEqual(order.price_centavos, self.planted["pack_order"].price_centavos)
        reg = SanteRegistration.objects.get(site=self.site)
        self.assertEqual((reg.date_of_birth, reg.desired_username), (None, ""))
        shop = ShopOrder.objects.get(pk=self.planted["shop_order"].pk)
        self.assertEqual((shop.receiver_name, shop.receiver_mobile, shop.contact_email), ("", "", ""))
        self.assertEqual(shop.total_centavos, self.planted["shop_order"].total_centavos)
        self.assertEqual(shop.lines.count(), 1)
        proof = ShopProofSubmission._base_manager.get(pk=self.planted["shop_proof"].pk)
        self.assertEqual((proof.reference_number, proof.account_name, proof.file_key), ("", "", ""))

    def test_the_site_inbox_is_deleted_and_the_do_not_text_list_stays(self):
        self.assertEqual(Conversation.objects.filter(site=self.site).count(), 0)
        self.assertEqual(ConversationMessage.objects.count(), 0)
        self.assertEqual(Contact.objects.filter(site=self.site).count(), 0)
        self.assertEqual(ContactGroup.objects.filter(site=self.site).count(), 0)
        self.assertEqual(DoNotText.objects.filter(mobile=MOBILE).count(), 1)

    def test_no_row_is_removed(self):
        now = {
            "orders": PackOrder.objects.count(), "shop_orders": ShopOrder.objects.count(),
            "members": Member.objects.count(), "accounts": SitePaymentAccount.objects.count(),
            "proofs": PackProofSubmission.objects.count(),
            "shop_proofs": ShopProofSubmission._base_manager.count(),
            "subdomains": Subdomain.objects.count(), "sites": Site.objects.count(),
        }
        self.assertEqual(now, self.rows_before)

    def test_the_seal_qr_and_shop_proof_files_go_and_the_pack_proof_file_stays(self):
        self.assertEqual(self.files(), sorted(self.planted["kept"]))
        proof = PackProofSubmission._base_manager.get(pk=self.planted["pack_proof"].pk)
        self.assertEqual(proof.file_key, self.planted["pack_proof"].file_key)
        self.assertEqual((proof.sender_name, proof.sender_account_number, proof.reference_number), ("", "", ""))

    def test_the_last_four_digits_of_the_receiving_account_are_blank(self):
        self.assertTrue(self.planted["shop_proof"].account_number_last4)
        for proof in ShopProofSubmission._base_manager.all():
            self.assertEqual(proof.account_number_last4, "")
        # Pack proofs are blanked by the purge; the same copy.
        for proof in PackProofSubmission._base_manager.all():
            self.assertEqual(proof.account_number_last4, "")

    def test_the_account_goes_with_its_only_site(self):
        account = Account.objects.get(pk=self.site.account_id)
        self.assertTrue(account_retention.is_disposed(account))
        item = ErasureItem.objects.get()
        self.assertTrue(item.account_disposed)
        self.assertEqual(item.status, "erased")
        self.assertEqual(item.counts["sites"], 1)


def plant_inquiries(site, token="ERASE-CIPHER", name="Zuleika Customer"):
    """A connection row holding a token, and a stored (website chat) conversation of a named customer."""
    connection = InquiryConnection.objects.create(
        site=site, provider="direct", status=InquiryConnection.ACTIVE, token_encrypted=token, external_page_id="page-1",
    )
    conversation = make_stored(site, name=name)
    return connection, conversation


class InquiriesErasureTests(RunTestCase):
    """The connection token and the customer names go with an erased site (z8uq9md3n6)."""

    def setUp(self):
        super().setUp()
        self.connection, self.conversation = plant_inquiries(self.site)
        # A site of the same account that is not due keeps the account, so the account step
        # does not blank these rows itself.
        self.other = second_site(self.sub)
        self.other_connection, self.other_conversation = plant_inquiries(self.other, "OTHER-CIPHER", "Other Customer")
        # A site of another account, not in the batch.
        self.stranger = ended_site(email="bo@example.test", name="bos-store").site
        self.stranger_connection, self.stranger_conversation = plant_inquiries(
            self.stranger, "STRANGER-CIPHER", "Stranger Customer",
        )

    def test_the_token_status_and_customer_name_are_blank_and_counted(self):
        batch = self.approved()
        self.run_batch(batch)
        connection = InquiryConnection.objects.get(pk=self.connection.pk)
        self.assertEqual((connection.token_encrypted, connection.status), ("", InquiryConnection.UNSET))
        self.assertEqual(InquiryConversation.objects.get(pk=self.conversation.pk).display_name, "")
        item = self.item(batch)
        self.assertFalse(item.account_disposed)
        self.assertEqual(item.counts["inquiry_connections"], 1)
        self.assertEqual(item.counts["inquiry_conversations"], 1)

    def test_other_sites_are_untouched(self):
        self.run_batch(self.approved())
        for connection, conversation, token, name in (
            (self.other_connection, self.other_conversation, "OTHER-CIPHER", "Other Customer"),
            (self.stranger_connection, self.stranger_conversation, "STRANGER-CIPHER", "Stranger Customer"),
        ):
            row = InquiryConnection.objects.get(pk=connection.pk)
            self.assertEqual((row.token_encrypted, row.status), (token, InquiryConnection.ACTIVE))
            self.assertEqual(InquiryConversation.objects.get(pk=conversation.pk).display_name, name)

    def test_the_audit_entry_names_the_inquiry_fields_and_holds_no_value(self):
        self.run_batch(self.approved())
        entry = OperatorAuditEntry.objects.get(action="site_data_erased")
        for table, fields in inquiries_retention.blanked().items():
            self.assertEqual(entry.detail["blanked"][table], fields)
        text = json.dumps(entry.detail)
        for value in ("ERASE-CIPHER", "Zuleika Customer"):
            self.assertNotIn(value, text)

    def test_it_blanks_whatever_the_inquiries_switch_says(self):
        with override_settings(INQUIRIES_ENABLED=False):
            self.run_batch(self.approved())
        self.assertEqual(InquiryConnection.objects.get(pk=self.connection.pk).token_encrypted, "")

    def test_the_dry_run_reports_the_same_counts_and_changes_nothing(self):
        from apps.lifecycle.dry_run import erasure_dry_run

        before = every_row()
        entry = erasure_dry_run(NOW)["eligible"][0]
        self.assertEqual(every_row(), before)
        self.assertEqual(entry["site"], self.site.public_id)
        self.assertEqual((entry["counts"]["inquiry_connections"], entry["counts"]["inquiry_conversations"]), (1, 1))
        batch = self.approved()
        self.run_batch(batch)
        item = self.item(batch)
        for key in ("inquiry_connections", "inquiry_conversations"):
            self.assertEqual(item.counts[key], entry["counts"][key])


class KeptListTests(RunTestCase):
    def test_no_billing_statement_invoice_ledger_or_audit_row_is_changed(self):
        BankTransferPayment.objects.create(
            subscription=self.sub, reference="kept-1", plan_code="usage_only", amount_centavos=100,
            amount_received_centavos=100, decided_by=self.staff, decided_at=ENDED, status="confirmed", transferred_on=ENDED.date(), proof_key="billing/proofs/k.png",
            proof_content_type="image/png", proof_size=1, proof_sha256="0" * 64,
            submitted_by=self.site.account, submitted_at=ENDED,
        )
        batch = self.approved()
        before = kept_rows()
        self.run_batch(batch)
        after = kept_rows()
        audit_before = before.pop("operators.OperatorAuditEntry")
        audit_after = after.pop("operators.OperatorAuditEntry")
        self.assertEqual(after, before)
        self.assertEqual(audit_after[: len(audit_before)], audit_before)
        self.assertGreater(len(audit_after), len(audit_before))
        subscription = Subscription.objects.get(pk=self.sub.pk)
        self.assertEqual(subscription.deletion_due_at, DUE)

    @mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", None)  # gate closed
    def test_no_billing_proof_key_is_deleted(self):
        key = private_files.save("billing/proofs", b"\x89PNG\r\n\x1a\n" + b"1" * 32, "image/png")
        BankTransferPayment.objects.create(
            subscription=self.sub, reference="kept-2", plan_code="usage_only", amount_centavos=100,
            amount_received_centavos=100, decided_by=self.staff, decided_at=ENDED, status="confirmed", transferred_on=ENDED.date(), proof_key=key,
            proof_content_type="image/png", proof_size=1, proof_sha256="0" * 64,
            submitted_by=self.site.account, submitted_at=ENDED,
        )
        deleted = []
        real = private_files.delete
        with mock.patch.object(private_files, "delete", side_effect=lambda k: (deleted.append(k), real(k))):
            self.run_batch(self.approved())
        self.assertNotIn(key, deleted)
        self.assertIn(key, self.files())
        self.assertEqual(set(deleted), set(self.planted["deleted"]))


class MultiSiteTests(RunTestCase):
    def test_the_other_site_and_the_account_are_untouched_while_it_is_not_due(self):
        other = second_site(self.sub)
        other_files = {"qr": private_files.save("payment-qr", b"\x89PNG\r\n\x1a\n" + b"2" * 32, "image/png")}
        SitePaymentAccount.objects.create(
            site=other, kind="bank", account_type="savings", provider_name="BDO",
            account_name="Other Owner", account_number="777", qr_file_key=other_files["qr"],
        )
        Site.objects.filter(pk=other.pk).update(contact_email="other@example.test")
        batch = self.approved()
        self.run_batch(batch)
        account = Account.objects.get(pk=self.site.account_id)
        self.assertFalse(account_retention.is_disposed(account))
        self.assertEqual(account.email, self.site.account.email)
        self.assertEqual(Site.objects.get(pk=other.pk).contact_email, "other@example.test")
        self.assertEqual(SitePaymentAccount.objects.get(site=other).account_number, "777")
        self.assertIn(other_files["qr"], self.files())
        item = self.item(batch)
        self.assertFalse(item.account_disposed)
        entry = OperatorAuditEntry.objects.get(action="site_data_erased")
        self.assertEqual(entry.detail["account_kept"], "other_live_site")

    def test_both_sites_in_one_batch_dispose_the_account_with_the_second(self):
        other_sub = add_due_site(self.sub)
        fill_other = __import__("apps.lifecycle.tests.run_base", fromlist=["fill"]).fill(other_sub, "o")
        batch = self.approved(self.sub, other_sub)
        self.run_batch(batch)
        first, second = self.item(batch), self.item(batch, other_sub)
        self.assertEqual((first.status, second.status), ("erased", "erased"))
        self.assertEqual((first.account_disposed, second.account_disposed), (False, True))
        self.assertTrue(account_retention.is_disposed(Account.objects.get(pk=self.site.account_id)))
        self.assertEqual(OperatorAuditEntry.objects.filter(action="account_disposed").count(), 1)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="site_data_erased").count(), 2)
        for key in fill_other["deleted"]:
            self.assertNotIn(key, self.files())

    def test_a_second_site_that_is_not_yet_due_keeps_the_account(self):
        add_due_site(self.sub, due=NOW + days(20))
        batch = self.approved()
        self.run_batch(batch)
        self.assertFalse(self.item(batch).account_disposed)
        self.assertFalse(account_retention.is_disposed(Account.objects.get(pk=self.site.account_id)))

    def test_the_catalog_owner_account_is_kept_and_the_site_is_still_erased(self):
        other = second_site(self.sub, name="owner-store")
        SanteCatalogOwner.objects.update_or_create(pk=1, defaults={"site": other})
        with override_settings(SANTE_SETTINGS_ENABLED=True):
            batch = self.approved()
            self.run_batch(batch)
        item = self.item(batch)
        self.assertEqual(item.status, "erased")
        self.assertFalse(item.account_disposed)
        self.assertFalse(account_retention.is_disposed(Account.objects.get(pk=self.site.account_id)))

    def test_the_last_site_after_an_earlier_batch_disposes_the_account(self):
        other_sub = add_due_site(self.sub)
        first = self.approved(self.sub)
        self.run_batch(first)
        self.assertFalse(account_retention.is_disposed(Account.objects.get(pk=self.site.account_id)))
        second = self.approved(other_sub)
        self.run_batch(second)
        self.assertTrue(self.item(second, other_sub).account_disposed)
        self.assertTrue(account_retention.is_disposed(Account.objects.get(pk=self.site.account_id)))


class IdempotenceTests(RunTestCase):
    def test_a_second_run_changes_nothing_and_audits_nothing(self):
        batch = self.approved()
        self.run_batch(batch)
        before = every_row()
        with self.assertRaises(workflow.ApiError) as raised:
            self.run_batch(batch)
        self.assertEqual(raised.exception.code, "not_approved")
        self.assertEqual(every_row(), before)

    def test_the_task_tick_after_a_done_batch_is_a_no_op(self):
        batch = self.approved()
        self.assertEqual(workflow.run_approved(NOW)["erased"], 1)
        before = every_row()
        self.assertEqual(workflow.run_approved(NOW)["batches"], 0)
        self.assertEqual(every_row(), before)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="site_data_erased").count(), 1)
        self.assertEqual(self.fresh_batch(batch).status, "done")

    def test_the_erase_steps_count_the_same_when_repeated(self):
        batch = self.approved()
        self.run_batch(batch)
        sites = Site.objects.filter(pk__in=[self.site.pk])
        from apps.members import retention as members
        from apps.shop import retention as shop

        again = members.blank_members(sites, ignore_switch=True)
        self.assertEqual(again["member_sessions"], 0)
        self.assertEqual(shop.blank_shop(sites, ignore_switch=True, last4=True)["shop_proof_files"], 0)

    def test_the_constraint_refuses_a_second_erased_item_for_one_site(self):
        batch = self.approved()
        self.run_batch(batch)
        other = ErasureBatch.objects.create(
            proposed_by=self.staff, proposed_at=NOW, expires_at=NOW + days(7), list_digest="x",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ErasureItem.objects.create(
                batch=other, site=self.site, subscription_id=self.sub.pk, deletion_due_at=DUE,
                status="erased",
            )

    def test_an_erased_site_cannot_be_proposed_again(self):
        self.run_batch(self.approved())
        with self.assertRaises(workflow.ApiError) as raised:
            self.propose()
        self.assertEqual(raised.exception.code, "not_proposable")
        self.assertIn("already_erased", raised.exception.fields["sites"][0])


class PackProofTests(RunTestCase):
    @mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", None)  # gate closed
    def test_a_pack_proof_file_is_never_deleted_while_the_export_gate_is_closed(self):
        deleted = []
        real = private_files.delete
        with mock.patch.object(private_files, "delete", side_effect=lambda k: (deleted.append(k), real(k))):
            with mock.patch.object(purge, "_delete_files") as pack_delete:
                self.run_batch(self.approved())
        pack_delete.assert_not_called()
        self.assertNotIn(self.planted["kept"][0], deleted)
        self.assertIn(self.planted["kept"][0], self.files())
        self.assertTrue(
            PackProofSubmission._base_manager.get(pk=self.planted["pack_proof"].pk).file_key
        )
        self.assertIsNone(purge.PROOF_EXPORT_READY_FROM)

    @mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", None)  # gate closed
    def test_the_account_step_keeps_them_too_and_the_file_count_excludes_them(self):
        self.run_batch(self.approved())
        self.assertTrue(account_retention.is_disposed(Account.objects.get(pk=self.site.account_id)))
        self.assertIn(self.planted["kept"][0], self.files())
        entry = OperatorAuditEntry.objects.get(action="site_data_erased")
        self.assertEqual(entry.detail["files_deleted"], 3)
        self.assertEqual(entry.detail["counts"]["pack_proof_files"], 0)

    def test_when_the_gate_opens_the_files_go(self):
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", date(2026, 1, 1)):
            self.run_batch(self.approved())
        self.assertNotIn(self.planted["kept"][0], self.files())

    def test_the_account_step_deletes_them_only_when_every_site_is_past_the_gate(self):
        account = Account.objects.get(pk=self.site.account_id)
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", date(2026, 1, 1)):
            self.assertFalse(erase._pack_files_stay(account))
            # Another site with no subscription: its service end is unknown, so the gate is closed.
            second_site(self.sub)
            self.assertTrue(erase._pack_files_stay(account))
        self.assertTrue(erase._pack_files_stay(account))  # gate closed everywhere


class FailureTests(RunTestCase):
    def snapshot(self):
        return {
            "site": Site.objects.get(pk=self.site.pk).contact_email,
            "pay": SitePaymentAccount.objects.filter(site=self.site).first().account_number,
            "member": Member.objects.get(pk=self.planted["member"].pk).full_name,
            "order": PackOrder.objects.get(pk=self.planted["pack_order"].pk).buyer_name,
        }

    def fail_on_third(self):
        real = private_files.delete
        calls = []

        def delete(key):
            calls.append(key)
            if len(calls) == 3:
                raise private_files.StoreError("down")
            real(key)

        return mock.patch.object(private_files, "delete", side_effect=delete)

    def test_a_store_that_fails_on_the_third_file_erases_nothing(self):
        batch = self.approved()
        before = self.snapshot()
        audit_before = OperatorAuditEntry.objects.count()
        with self.fail_on_third():
            result = self.run_batch(batch)
        item = self.item(batch)
        self.assertEqual((result["failed"], result["erased"]), (1, 0))
        self.assertEqual((item.status, item.reason_code, item.attempts), ("failed", "store_unavailable", 1))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(OperatorAuditEntry.objects.count(), audit_before)
        self.assertFalse(ErasureItem.objects.filter(status="erased").exists())
        self.assertFalse(account_retention.is_disposed(Account.objects.get(pk=self.site.account_id)))
        self.assertEqual(self.fresh_batch(batch).status, "running")

    @mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", None)  # gate closed
    def test_a_later_run_with_the_store_fixed_erases_it_and_a_file_already_gone_counts(self):
        batch = self.approved()
        with self.fail_on_third():
            self.run_batch(batch)
        # The first file was deleted by the failed run; the retry finds it gone and goes on.
        result = self.run_batch(batch)
        self.assertEqual(result["erased"], 1)
        item = self.item(batch)
        self.assertEqual((item.status, item.reason_code), ("erased", ""))
        self.assertEqual(self.files(), sorted(self.planted["kept"]))
        self.assertEqual(self.fresh_batch(batch).status, "done")
        self.assertEqual(Site.objects.get(pk=self.site.pk).contact_email, "")

    def test_five_failures_set_needs_attention_and_stop_retrying(self):
        batch = self.approved()
        with mock.patch.object(private_files, "delete", side_effect=private_files.StoreError("down")):
            for _ in range(5):
                self.run_batch(batch)
            item = self.item(batch)
            self.assertEqual((item.status, item.attempts), ("failed", 5))
        self.assertEqual(self.fresh_batch(batch).status, "needs_attention")
        with self.assertRaises(workflow.ApiError):
            self.run_batch(batch)  # a batch that needs attention is not run again by itself
        self.assertEqual(self.item(batch).attempts, 5)

    def test_an_unexpected_error_fails_the_item_and_rolls_back(self):
        batch = self.approved()
        before = self.snapshot()
        with mock.patch.object(erase.meta_retention, "blank_meta", side_effect=RuntimeError("x")):
            result = self.run_batch(batch)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.item(batch).reason_code, "error")
        self.assertEqual(self.snapshot(), before)

    def test_a_failed_account_step_rolls_the_whole_item_back(self):
        batch = self.approved()
        before = self.snapshot()
        with mock.patch.object(account_retention, "dispose_account", side_effect=RuntimeError("x")):
            self.run_batch(batch)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.item(batch).status, "failed")
        self.assertFalse(OperatorAuditEntry.objects.filter(action="site_data_erased").exists())


class RaceTests(RunTestCase):
    """Something changes between approval and execution: the item is skipped, nothing is erased."""

    def assertSkipped(self, batch, reason):
        item = self.item(batch)
        self.assertEqual((item.status, item.reason_code), ("skipped", reason))
        self.assertEqual(Site.objects.get(pk=self.site.pk).contact_email, EMAIL)
        self.assertEqual(self.files(), sorted([*self.planted["deleted"], *self.planted["kept"]]))
        entry = OperatorAuditEntry.objects.get(action="site_erasure_skipped")
        self.assertEqual(entry.detail, {"batch": batch.public_id, "reason_code": reason})
        self.assertFalse(OperatorAuditEntry.objects.filter(action="site_data_erased").exists())
        self.assertEqual(self.fresh_batch(batch).status, "done")

    def test_a_payment_proof_submitted_after_approval(self):
        batch = self.approved()
        from apps.lifecycle.tests.base import submitted_proof

        submitted_proof(self.sub)
        self.run_batch(batch)
        self.assertSkipped(batch, "payment_under_review")

    def test_a_hold_placed_after_approval(self):
        batch = self.approved()
        workflow.place_hold(self.staff, self.site.public_id, reason_code="legal", reference="T-1", now=NOW)
        self.run_batch(batch)
        self.assertSkipped(batch, "held")

    def test_a_payment_that_cleared_the_dates(self):
        batch = self.approved()
        Subscription.objects.filter(pk=self.sub.pk).update(
            state=State.ACTIVE, service_ended_at=None, deletion_due_at=None, download_until=None,
        )
        self.run_batch(batch)
        self.assertSkipped(batch, "no_longer_due")

    def test_a_changed_deletion_date(self):
        batch = self.approved()
        Subscription.objects.filter(pk=self.sub.pk).update(deletion_due_at=DUE - days(1))
        self.run_batch(batch)
        self.assertSkipped(batch, "date_changed")

    def test_an_open_charge_on_a_cancelled_site(self):
        batch = self.approved()
        open_charge(self.sub)
        self.run_batch(batch)
        self.assertSkipped(batch, "unpaid_usage")

    def test_retention_switched_off_after_approval(self):
        batch = self.approved()
        with override_settings(RETENTION_ENABLED=False):
            self.run_batch(batch)
        self.assertSkipped(batch, "retention_switched_off")

    def test_a_suspended_sites_open_lines_are_written_off_then_it_is_erased(self):
        sub = ended_site("s@example.test", "s-store", state=State.SUSPENDED)
        from apps.lifecycle.tests.run_base import fill

        fill(sub, "s")
        line = open_charge(sub)
        batch = self.approved(sub)
        self.run_batch(batch)
        self.assertEqual(self.item(batch, sub).status, "erased")
        line.refresh_from_db()
        self.assertEqual(line.status, "written_off")
        self.assertEqual(line.closed_reason, "suspension_unreturned")

    def test_an_item_of_a_cancelled_batch_is_not_run(self):
        batch = self.approved()
        workflow.cancel(self.staff, batch.public_id, reason="mistake", now=NOW)
        with self.assertRaises(workflow.ApiError) as raised:
            self.run_batch(batch)
        self.assertEqual(raised.exception.code, "not_approved")
        self.assertEqual(Site.objects.get(pk=self.site.pk).contact_email, EMAIL)


class AuditTests(RunTestCase):
    def test_one_entry_per_site_kept_with_names_only(self):
        other_sub = add_due_site(self.sub)
        from apps.lifecycle.tests.run_base import fill

        fill(other_sub, "o")
        batch = self.approved(self.sub, other_sub)
        self.run_batch(batch)
        entries = OperatorAuditEntry.objects.filter(action="site_data_erased").order_by("pk")
        self.assertEqual(entries.count(), 2)
        for entry in entries:
            self.assertEqual(ACTIONS[entry.action], KEPT)
            self.assertEqual(entry.personal, {})
            self.assertIsNone(entry.operator)
            self.assertEqual(entry.detail["batch"], batch.public_id)
            self.assertEqual(entry.detail["approved_by"], self.staff.pk)
            self.assertIn("sponsor_id", json.dumps(entry.detail["blanked"]))
            text = json.dumps(entry.detail) + entry.reason + entry.target_id
            for value in PLANTED:
                self.assertNotIn(value, text)
        self.assertEqual(
            sorted(e.target_id for e in entries), sorted([self.site.public_id, other_sub.site.public_id])
        )

    def test_every_workflow_action_is_declared_and_kept(self):
        for name in (
            "erasure_batch_proposed", "erasure_batch_approved", "erasure_batch_cancelled",
            "site_data_erased", "site_erasure_skipped", "erasure_hold_set", "erasure_hold_released",
        ):
            self.assertEqual(ACTIONS[name], KEPT)

    def test_the_whole_trail_of_a_batch(self):
        batch = self.approved()
        self.run_batch(batch)
        actions = list(OperatorAuditEntry.objects.order_by("pk").values_list("action", flat=True))
        for name in ("erasure_batch_proposed", "erasure_batch_approved", "site_data_erased", "account_disposed"):
            self.assertIn(name, actions)
        disposed = OperatorAuditEntry.objects.get(action="account_disposed")
        self.assertEqual(disposed.reason, f"site_erasure: {batch.public_id}")


class SwitchTests(RunTestCase):
    def test_the_task_returns_at_once_and_reads_no_lifecycle_table_while_off(self):
        batch = self.approved()
        with override_settings(SITE_ERASURE_ENABLED=False):
            with self.assertNumQueries(0):
                self.assertEqual(workflow.run_approved(NOW), {"skipped": "off"})
            from apps.lifecycle import tasks

            with self.assertNumQueries(0):
                self.assertEqual(tasks.run_approved(), {"skipped": "off"})
        self.assertEqual(Site.objects.get(pk=self.site.pk).contact_email, EMAIL)
        self.assertEqual(self.fresh_batch(batch).status, "approved")

    def test_every_entry_point_answers_not_found_while_off(self):
        batch = self.approved()
        with override_settings(SITE_ERASURE_ENABLED=False):
            calls = (
                lambda: workflow.propose(self.staff, [self.site.public_id], NOW),
                lambda: workflow.approve(
                    self.staff, batch.public_id, confirmation="x", exclude=[], reason="r", now=NOW),
                lambda: workflow.cancel(self.staff, batch.public_id, reason="r", now=NOW),
                lambda: workflow.place_hold(
                    self.staff, self.site.public_id, reason_code="legal", reference="", now=NOW),
                lambda: workflow.release_hold(self.staff, self.site.public_id, now=NOW),
                lambda: workflow.run_batch(batch.public_id, now=NOW),
            )
            for call in calls:
                with self.assertRaises(workflow.ApiError) as raised:
                    call()
                self.assertEqual((raised.exception.status, raised.exception.code), (404, "not_found"))

    def test_the_task_is_registered_and_scheduled(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["lifecycle-run-approved"]
        self.assertEqual((entry["task"], entry["schedule"]), ("lifecycle.run_approved", 300))
        from config.celery import app

        from apps.lifecycle import tasks  # noqa: F401  (beat finds it by autodiscovery)

        self.assertIn("lifecycle.run_approved", app.tasks)

    def test_retention_off_refuses_a_site_that_would_dispose_its_account_at_the_proposal(self):
        with override_settings(RETENTION_ENABLED=False):
            with self.assertRaises(workflow.ApiError) as raised:
                self.propose()
        self.assertIn("retention_switched_off", raised.exception.fields["sites"][0])

    def test_retention_off_allows_a_site_whose_account_stays(self):
        second_site(self.sub)
        with override_settings(RETENTION_ENABLED=False):
            batch = self.propose()
            self.approve(batch)
            result = self.run_batch(batch)
        self.assertEqual(result["erased"], 1)


class FeatureSwitchTests(RunTestCase):
    """The G4b promise (plan section 3b): an erased site has no receiving-account name, number,
    QR key or file, whatever the feature switches say."""

    SWITCHES = (
        "PAYMENT_METHODS_ENABLED", "MEMBERS_ENABLED", "SHOP_ENABLED", "SANTE_SETTINGS_ENABLED",
        "PACK_ENABLED",
    )

    def erase_with(self, **switches):
        with override_settings(**{**{name: False for name in self.SWITCHES}, **switches}):
            self.run_batch(self.approved())
        pay = SitePaymentAccount.objects.filter(site=self.site)
        self.assertEqual({(r.account_name, r.account_number, r.qr_file_key) for r in pay}, {("", "", "")})
        self.assertTrue(all(r.removed_at for r in pay))
        for key in self.planted["deleted"]:
            self.assertNotIn(key, self.files())
        for proof in ShopProofSubmission._base_manager.all():
            self.assertEqual(proof.account_number_last4, "")
        self.assertEqual(Member.objects.get(pk=self.planted["member"].pk).full_name, "")
        self.assertEqual(SanteSiteSettings.objects.get(site=self.site).sponsor_id, "")
        self.assertEqual(PackOrder.objects.get(pk=self.planted["pack_order"].pk).delivery_address, {})

    def test_payment_methods_off(self):
        self.erase_with()

    def test_all_switches_on(self):
        self.erase_with(**{name: True for name in self.SWITCHES})


def _alone(name):
    def test(self):
        self.erase_with(**{name: True})
    return test


for _name in FeatureSwitchTests.SWITCHES:
    setattr(FeatureSwitchTests, f"test_{_name.lower()}_on_alone", _alone(_name))


class PackPurgeOverlapTests(RunTestCase):
    def pack_state(self):
        return {
            "orders": list(PackOrder.objects.order_by("pk").values_list("buyer_name", "delivery_address")),
            "proofs": list(PackProofSubmission._base_manager.order_by("pk").values_list(
                "sender_name", "sender_account_number", "file_key", "account_number_last4")),
            "regs": list(SanteRegistration.objects.order_by("pk").values_list(
                "date_of_birth", "desired_username", "alternate_username")),
            "tokens": CheckoutToken.objects.count(),
            "members": list(Member.objects.order_by("pk").values_list("delivery_address", flat=True)),
        }

    @mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", None)  # gate closed
    def test_purge_first_then_executor_equals_executor_alone(self):
        CheckoutToken.objects.create(
            site=self.site, token_hash="t" * 64, expires_at=NOW, pending_contact={"email": "m@x.test"},
        )
        batch = self.approved()
        with override_settings(PACK_PURGE_ENABLED=True, PACK_ENABLED=True):
            purge.run(NOW)
        mid = self.pack_state()
        files_mid = self.files()
        self.run_batch(batch)
        self.assertEqual(self.pack_state(), mid)
        self.assertIn(self.planted["kept"][0], self.files())
        self.assertEqual(self.files(), sorted([*self.planted["kept"]]))
        self.assertGreater(len(files_mid), len(self.files()))
        entry = OperatorAuditEntry.objects.get(action="site_data_erased")
        self.assertEqual(entry.detail["counts"]["pack_orders"], 1)

    def test_executor_alone_then_the_purge_changes_nothing(self):
        self.run_batch(self.approved())
        state = self.pack_state()
        with override_settings(PACK_PURGE_ENABLED=True, PACK_ENABLED=True):
            purge.run(NOW)
        self.assertEqual(self.pack_state(), state)

    def test_the_purge_alone_and_the_executor_alone_give_identical_pack_rows(self):
        from apps.lifecycle.tests.run_base import fill

        other = ended_site("p@example.test", "p-store")
        fill(other, "p")
        self.run_batch(self.approved())
        with override_settings(PACK_PURGE_ENABLED=True, PACK_ENABLED=True):
            purge.purge_site(other.site.pk, NOW)

        def state(site):
            return (
                list(PackOrder.objects.filter(site=site).values_list("buyer_name", "delivery_address")),
                list(PackProofSubmission._base_manager.filter(site=site).values_list(
                    "sender_name", "sender_account_number", "reference_number", "account_number_last4")),
                list(SanteRegistration.objects.filter(site=site).values_list(
                    "date_of_birth", "desired_username", "alternate_username")),
                CheckoutToken.objects.filter(site=site).count(),
            )

        self.assertEqual(state(self.site), state(other.site))


class HoldTests(RunTestCase):
    def test_the_pack_purge_leaves_a_held_site_alone(self):
        workflow.place_hold(self.staff, self.site.public_id, reason_code="fraud", reference="T-9", now=NOW)
        with override_settings(PACK_PURGE_ENABLED=True, PACK_ENABLED=True):
            self.assertFalse(purge.purge_site(self.site.pk, NOW))
            self.assertEqual(purge.run(NOW), {"purged": 0, "failed": 0})
        self.assertEqual(PackOrder.objects.get(pk=self.planted["pack_order"].pk).buyer_name, NAME)

    def test_a_released_hold_lets_it_through(self):
        workflow.place_hold(self.staff, self.site.public_id, reason_code="fraud", reference="", now=NOW)
        workflow.release_hold(self.staff, self.site.public_id, now=NOW)
        with override_settings(PACK_PURGE_ENABLED=True, PACK_ENABLED=True):
            self.assertTrue(purge.purge_site(self.site.pk, NOW))

    def test_with_the_executor_switch_off_the_purge_reads_no_lifecycle_table(self):
        workflow.place_hold(self.staff, self.site.public_id, reason_code="fraud", reference="", now=NOW)
        with override_settings(PACK_PURGE_ENABLED=True, PACK_ENABLED=True, SITE_ERASURE_ENABLED=False):
            with mock.patch.object(purge, "ErasureHold") as table:
                self.assertTrue(purge.purge_site(self.site.pk, NOW))
        table.objects.filter.assert_not_called()

    def test_a_held_site_is_never_erased_and_keeps_its_dates(self):
        workflow.place_hold(self.staff, self.site.public_id, reason_code="legal", reference="T-2", now=NOW)
        with self.assertRaises(workflow.ApiError) as raised:
            self.propose()
        self.assertIn("held", raised.exception.fields["sites"][0])
        self.assertEqual(Subscription.objects.get(pk=self.sub.pk).deletion_due_at, DUE)
        workflow.release_hold(self.staff, self.site.public_id, now=NOW)
        self.assertEqual(self.propose().items.count(), 1)

    def test_a_second_open_hold_is_refused(self):
        workflow.place_hold(self.staff, self.site.public_id, reason_code="legal", reference="", now=NOW)
        with self.assertRaises(workflow.ApiError) as raised:
            workflow.place_hold(self.staff, self.site.public_id, reason_code="fraud", reference="", now=NOW)
        self.assertEqual(raised.exception.code, "already_held")
        self.assertEqual(ErasureHold.objects.count(), 1)


class ExportTests(RunTestCase):
    def test_the_support_export_still_works_for_an_account_with_another_live_site(self):
        second_site(self.sub)
        before = account_retention.export_account_data(
            Account.objects.get(pk=self.site.account_id), actor=self.staff, request_ref="REQ-1"
        )
        self.assertEqual(len(before["sites"]), 2)
        self.run_batch(self.approved())
        after = account_retention.export_account_data(
            Account.objects.get(pk=self.site.account_id), actor=self.staff, request_ref="REQ-2"
        )
        self.assertEqual(len(after["sites"]), 2)

    def test_the_export_works_with_the_switch_off(self):
        with override_settings(SITE_ERASURE_ENABLED=False):
            data = account_retention.export_account_data(
                Account.objects.get(pk=self.site.account_id), actor=self.staff, request_ref="REQ-3"
            )
        self.assertEqual(len(data["sites"]), 1)

    def test_the_export_works_on_the_day_of_the_batch_before_the_run(self):
        batch = self.approved()
        data = account_retention.export_account_data(
            Account.objects.get(pk=self.site.account_id), actor=self.staff, request_ref="REQ-4"
        )
        self.assertEqual(self.fresh_batch(batch).status, "approved")
        self.assertEqual(data["sites"][0]["contact_email"], EMAIL)
