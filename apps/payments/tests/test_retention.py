"""Account disposal and export with payment rows: account names and numbers are blanked, QR
code files deleted and the rows kept; the export holds the account's own rows only."""
import json
from unittest import mock

from django.test import override_settings

from apps.accounts import retention as rules
from apps.accounts.tests.disposal_base import DisposalTestCase, snapshot
from apps.operators.models import OperatorAuditEntry
from apps.common import private_files
from apps.payments import retention
from apps.payments.models import SitePaymentAccount, SitePaymentSettings
from apps.payments.tests.base import (
    BANK, PNG, make_payment_account, payment_rows, stored_files, switched_on,
    use_local_private_store,
)
from apps.sites.models import Site

NUMBER, NAME = BANK["account_number"], BANK["account_name"]
THEIR_NUMBER = "777700001111"
KEYS = ("site_payment_accounts", "site_payment_settings")


@switched_on
@override_settings(RETENTION_ENABLED=True)
class PaymentsDisposalTestCase(DisposalTestCase):
    def setUp(self):
        super().setUp()
        self.private_dir = use_local_private_store(self)
        self.site = Site.objects.get(account=self.account)
        self.their_site = Site.objects.get(account=self.other)
        self.listed = make_payment_account(self.site)
        self.removed = make_payment_account(
            self.site, kind="ewallet", account_type="ewallet", provider_name="GCash",
            account_number="09171234567", removed_at=self.now,
        )
        self.theirs = make_payment_account(self.their_site, account_number=THEIR_NUMBER)
        SitePaymentSettings.objects.create(site=self.site, direct_enabled=False)
        SitePaymentSettings.objects.create(site=self.their_site)

    def give_qr(self, row):
        """A stored QR file for the account row. Returns its key."""
        row.qr_file_key = private_files.save(f"sante/qr/site-{row.site_id}", PNG, "image/png")
        row.save()
        return row.qr_file_key

    def dispose(self, account=None, **extra):
        return rules.dispose_account(
            account or self.account, now=self.now, rule=rules.REQUEST, actor=self.operator,
            request_ref="DPO-2026-009", **extra,
        )


class BlankingTests(PaymentsDisposalTestCase):
    def test_names_and_numbers_are_blanked_every_account_removed_and_the_rows_kept(self):
        counts = self.dispose()
        self.assertEqual(counts["site_payment_accounts"], 2)
        rows = SitePaymentAccount.objects.filter(site=self.site)
        self.assertEqual(rows.count(), 2)
        for row in rows:
            self.assertEqual((row.account_name, row.account_number), ("", ""))
            self.assertIsNotNone(row.removed_at)
            self.assertEqual(row.provider_name, getattr(self, "listed" if row.kind == "bank" else "removed").provider_name)
        self.assertEqual(SitePaymentSettings.objects.filter(site=self.site).count(), 1)

    def test_another_accounts_rows_are_untouched(self):
        self.dispose()
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.account_number, THEIR_NUMBER)
        self.assertIsNone(self.theirs.removed_at)

    def test_the_audit_entry_names_the_fields_and_never_a_value(self):
        self.dispose()
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        self.assertEqual(
            entry.detail["blanked"]["site_payment_accounts"],
            ["account_name", "account_number", "qr_file_key"],
        )
        self.assertEqual(entry.detail["counts"]["site_payment_accounts"], 2)
        text = json.dumps(entry.detail)
        for value in (NUMBER, NAME, "09171234567"):
            self.assertNotIn(value, text)

    def test_the_dry_run_counts_and_changes_nothing(self):
        before = snapshot(), payment_rows()
        self.assertEqual(self.dispose(dry_run=True)["site_payment_accounts"], 2)
        self.assertEqual((snapshot(), payment_rows()), before)

    def test_an_account_with_no_payment_row_counts_zero(self):
        SitePaymentAccount.objects.filter(site=self.site).update(site=self.their_site)
        counts = self.dispose()
        self.assertEqual((counts["site_payment_accounts"], counts["site_payment_qr_files"]), (0, 0))

    def test_qr_files_are_deleted_their_keys_blanked_and_another_accounts_file_kept(self):
        for row in (self.listed, self.removed):  # a removed account's file goes too
            self.give_qr(row)
        their_key = self.give_qr(self.theirs)
        counts = self.dispose()
        self.assertEqual(counts["site_payment_qr_files"], 2)
        self.assertEqual(
            set(SitePaymentAccount.objects.filter(site=self.site).values_list("qr_file_key", flat=True)),
            {""},
        )
        self.assertEqual(stored_files(self.private_dir), [their_key])
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.qr_file_key, their_key)
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        self.assertEqual(entry.detail["counts"]["site_payment_qr_files"], 2)
        self.assertNotIn("sante/qr", json.dumps(entry.detail))

    def test_the_dry_run_counts_qr_files_and_deletes_none(self):
        key = self.give_qr(self.listed)
        before = snapshot(), payment_rows()
        self.assertEqual(self.dispose(dry_run=True)["site_payment_qr_files"], 1)
        self.assertEqual((snapshot(), payment_rows()), before)
        self.assertEqual(stored_files(self.private_dir), [key])

    def test_a_file_that_cannot_be_deleted_stops_the_disposal_and_a_rerun_finishes_it(self):
        key = self.give_qr(self.listed)
        before = payment_rows()
        failing = mock.Mock()
        failing.delete.side_effect = private_files.StoreError("down")
        with mock.patch.object(private_files, "store", return_value=failing):
            with self.assertRaises(private_files.StoreError):
                self.dispose()
        self.assertEqual(payment_rows(), before)
        self.assertEqual(stored_files(self.private_dir), [key])
        (self.private_dir / key).unlink()  # a file already gone does not stop the rerun
        self.assertEqual(self.dispose()["site_payment_qr_files"], 1)
        self.assertEqual(stored_files(self.private_dir), [])


class ExportTests(PaymentsDisposalTestCase):
    def export(self):
        return rules.export_account_data(self.account, actor=self.operator, request_ref="DPO-2")

    def test_it_holds_every_account_of_the_accounts_sites_and_the_switch(self):
        data = self.export()
        self.assertEqual(data["site_payment_accounts"][0], {
            "site": self.site.public_id, "id": self.listed.public_id, "kind": "bank",
            "account_type": "savings", "provider_name": "BDO", "account_name": NAME,
            "account_number": NUMBER, "has_qr": False,
            "created_at": self.listed.created_at.isoformat(),
            "removed_at": None,
        })
        self.assertEqual(data["site_payment_accounts"][1]["removed_at"], self.now.isoformat())
        row = SitePaymentSettings.objects.get(site=self.site)
        self.assertEqual(data["site_payment_settings"], [{
            "site": self.site.public_id, "direct_enabled": False,
            "updated_at": row.updated_at.isoformat(),
        }])
        entry = OperatorAuditEntry.objects.get(action="account_exported")
        self.assertEqual(entry.detail["counts"]["site_payment_accounts"], 2)
        self.assertEqual(entry.detail["counts"]["site_payment_settings"], 1)

    def test_it_holds_nothing_of_another_account_and_changes_nothing(self):
        before = payment_rows()
        text = json.dumps(self.export())
        self.assertNotIn(THEIR_NUMBER, text)
        self.assertNotIn(self.their_site.public_id, text)
        self.assertEqual(payment_rows(), before)

    def test_it_says_an_account_has_a_qr_and_never_carries_its_key(self):
        key = self.give_qr(self.listed)
        data = self.export()
        self.assertEqual([row["has_qr"] for row in data["site_payment_accounts"]], [True, False])
        text = json.dumps(data)
        self.assertNotIn(key, text)
        self.assertNotIn("qr_file_key", text)
        self.assertEqual(stored_files(self.private_dir), [key])

    def test_it_carries_no_xendit_key(self):
        self.assertNotIn("xendit", json.dumps(self.export()).lower())


@override_settings(PAYMENT_METHODS_ENABLED=False)
class SwitchedOffTests(PaymentsDisposalTestCase):
    """Off, disposal and export behave exactly as before this app and read no payment table."""

    def test_the_helpers_answer_empty_without_a_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(retention.blank_payments_of(self.account, self.now), {})
            self.assertEqual(retention.export_payments_of(self.account), {})
            self.assertEqual(retention.blanked(), {})

    def test_disposal_and_export_carry_no_payment_key_and_touch_no_payment_row(self):
        before = payment_rows()
        data = rules.export_account_data(self.account, actor=self.operator, request_ref="DPO-2")
        counts = self.dispose()
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        for key in KEYS:
            self.assertNotIn(key, data)
            self.assertNotIn(key, counts)
            self.assertNotIn(key, entry.detail["blanked"])
        self.assertEqual(payment_rows(), before)
