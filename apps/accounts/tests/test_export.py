import json
import stat
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts import retention as rules
from apps.accounts.management.commands.export_account import Command as ExportCommand
from apps.accounts.tests.disposal_base import (
    EMAIL,
    MOBILE,
    OTHER_EMAIL,
    DisposalTestCase,
    snapshot,
)
from apps.operators.models import OperatorAuditEntry

COUNTS = {
    "policy_acceptances": 1,
    "sign_in_providers": 1,
    "sessions": 2,
    "audit_events": 3,
    "mobile_numbers": 1,
    "mobile_challenges": 2,
    "email_send_records": 1,
    "sites": 1,
}


def exported():
    return OperatorAuditEntry.objects.filter(action="account_exported")


class ExportAccountDataTests(DisposalTestCase):
    def export(self):
        return rules.export_account_data(
            self.account, actor=self.operator, request_ref="DPO-2026-002"
        )

    def test_it_holds_the_account_and_everything_kept_about_it(self):
        data = self.export()
        account = data["account"]
        self.assertEqual(account["public_id"], self.account.public_id)
        self.assertEqual((account["email"], account["name"]), (EMAIL, "Ana Reyes"))
        self.assertEqual(account["referral_code"], self.account.referral_code)
        self.assertEqual(account["created_at"], self.account.created_at.isoformat())
        self.assertEqual(data["policy_acceptances"][0]["policy_version"], "2026-11")
        self.assertEqual(data["policy_acceptances"][0]["ip"], "203.0.113.7")
        self.assertEqual(
            [identity["provider"] for identity in data["sign_in_providers"]], ["google"]
        )
        self.assertEqual(
            sorted(session["device_label"] for session in data["sessions"]), ["laptop", "phone"]
        )
        self.assertEqual(data["sessions"][0]["masked_ip"], "203.0.113.0")
        self.assertEqual(
            [event["event"] for event in data["audit_events"]],
            ["signed_in", "email_changed", "mobile_verified"],
        )
        self.assertEqual(
            data["mobile"], {"number": MOBILE, "verified_at": self.now.isoformat()}
        )
        self.assertEqual(len(data["mobile_challenges"]), 2)
        self.assertEqual(
            sorted(data["mobile_challenges"][0]),
            ["created_at", "expires_at", "number", "superseded_at", "used_at"],
        )
        self.assertEqual(data["mobile_challenges"][0]["number"], MOBILE)
        self.assertEqual(
            [record["category"] for record in data["email_send_records"]], ["signin_code"]
        )
        self.assertEqual(data["sites"][0]["business_profile"]["tin"], "123-456-789-000")
        self.assertEqual(data["sites"][0]["web_addresses"][0]["name"], "anas-store")
        self.assertIn("onboarding", data)
        json.dumps(data)  # plain values only

    def test_it_holds_no_token_hash_no_provider_user_id_and_no_row_number(self):
        text = json.dumps(self.export())
        for secret in ("ana-phone", "ana-laptop", "token_hash", "g-ana", '"id"', '"pk"',
                       "code_hash", "code-hash", "sms_ana", "sms_public_id", "attempts"):
            self.assertNotIn(secret, text)

    def test_it_holds_nothing_of_another_account(self):
        text = json.dumps(self.export())
        for theirs in (OTHER_EMAIL, self.other.public_id, self.other.referral_code,
                       "bens-store", "ben-store@example.test", "g-ben", "ben-first"):
            self.assertNotIn(theirs, text)

    def test_an_account_without_a_mobile_number_exports_none(self):
        from apps.accounts.models import AccountMobile, MobileOtpChallenge

        AccountMobile.objects.filter(account=self.account).delete()
        MobileOtpChallenge.objects.filter(account=self.account).delete()
        data = self.export()
        self.assertIsNone(data["mobile"])
        self.assertEqual(data["mobile_challenges"], [])

    def test_it_writes_one_audit_entry_with_counts_only_and_changes_nothing_else(self):
        before = snapshot()
        self.export()
        entry = exported().get()
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual((entry.target_type, entry.target_id), ("account", self.account.public_id))
        self.assertEqual(entry.reason, "DPO-2026-002")
        self.assertEqual(entry.detail, {"counts": COUNTS})
        self.assertEqual(entry.personal, {})
        after = snapshot()
        after["operators.OperatorAuditEntry"] = before["operators.OperatorAuditEntry"]
        self.assertEqual(after, before)

    @override_settings(RETENTION_ENABLED=True)
    def test_a_disposed_account_is_refused(self):
        rules.dispose_account(self.account, now=self.now, rule="request")
        self.account.refresh_from_db()
        with self.assertRaises(rules.AlreadyDisposed):
            self.export()
        self.assertFalse(exported().exists())

    def test_a_request_reference_that_looks_like_an_address_is_refused(self):
        with self.assertRaises(ValueError):
            rules.export_account_data(self.account, actor=self.operator, request_ref=EMAIL)
        self.assertFalse(exported().exists())


class ExportAccountCommandTests(DisposalTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def run_command(self, *args, account=None, **options):
        out, err = StringIO(), StringIO()
        options.setdefault("actor", "coach")
        options.setdefault("request_ref", "DPO-2026-002")
        # The streams go to the command itself: call_command would read a `stdout` keyword as
        # the command's own --stdout option.
        call_command(
            ExportCommand(stdout=out, stderr=err), account or self.account.public_id, *args,
            **options,
        )
        return out.getvalue(), err.getvalue()

    def test_it_writes_one_owner_only_json_file_and_says_where(self):
        out, _ = self.run_command(out_dir=str(self.folder))
        (path,) = self.folder.iterdir()
        self.assertRegex(path.name, rf"^account-export-{self.account.public_id}-\d{{8}}T\d{{6}}Z\.json$")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())["account"]["email"], EMAIL)
        self.assertIn(str(path), out)
        self.assert_no_personal_value(out)
        self.assertEqual(exported().count(), 1)

    def test_an_existing_file_is_never_overwritten(self):
        with mock.patch("apps.accounts.management.commands.export_account.timezone.now",
                        return_value=self.now):
            self.run_command(out_dir=str(self.folder))
            (path,) = self.folder.iterdir()
            path.write_text("kept")
            with self.assertRaisesMessage(CommandError, "already exists"):
                self.run_command(out_dir=str(self.folder))
        self.assertEqual(path.read_text(), "kept")
        self.assertEqual(exported().count(), 1)  # the refused export is not logged

    def test_stdout_prints_the_json_alone(self):
        out, err = self.run_command("--stdout")
        data = json.loads(out)
        self.assertEqual(data["account"]["public_id"], self.account.public_id)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assert_no_personal_value(err)
        self.assertEqual(exported().count(), 1)

    def test_it_finds_the_account_by_email(self):
        out, _ = self.run_command("--stdout", account=EMAIL)
        self.assertEqual(json.loads(out)["account"]["public_id"], self.account.public_id)

    def test_it_needs_exactly_one_destination(self):
        with self.assertRaises(CommandError):
            self.run_command()
        with self.assertRaises(CommandError):
            self.run_command("--stdout", "--out-dir", str(self.folder))
        self.assertFalse(exported().exists())

    def test_a_missing_folder_account_or_actor_is_refused(self):
        with self.assertRaisesMessage(CommandError, "is not a folder"):
            self.run_command(out_dir=str(self.folder / "missing"))
        with self.assertRaisesMessage(CommandError, "No such account."):
            self.run_command("--stdout", account="acc_missing")
        with self.assertRaisesMessage(CommandError, "--actor must be an active staff user."):
            self.run_command("--stdout", actor="nobody")
        self.assertFalse(exported().exists())

    @override_settings(RETENTION_ENABLED=True)
    def test_a_disposed_account_is_refused(self):
        rules.dispose_account(self.account, now=self.now, rule="request")
        with self.assertRaisesMessage(CommandError, "disposed of"):
            self.run_command("--stdout")
        self.assertFalse(exported().exists())

    def test_it_works_with_the_switch_off(self):
        out, _ = self.run_command("--stdout")
        self.assertTrue(json.loads(out)["sites"])


@override_settings(BILLING_ENABLED=True)
class ExportBankTransfersTests(DisposalTestCase):
    def test_the_senders_details_are_in_the_export_and_no_file_key_or_hash(self):
        from apps.billing.tests.base import SENDER, days, submit, transfers_on
        from apps.billing import services as billing_services
        from apps.sites.models import Site

        site = Site.objects.filter(account=self.account).first()
        subscription = billing_services.start_subscription(site, self.account, now=self.now)
        with transfers_on():
            submit(subscription, self.now + days(1))
        data = rules.export_account_data(
            self.account, actor=self.operator, request_ref="DPO-2026-003"
        )
        (row,) = data["bank_transfers"]
        self.assertEqual(
            (row["sender_bank"], row["sender_first_name"], row["sender_last_name"],
             row["sender_account_number"], row["transfer_reference"]),
            (SENDER["sender_bank"], SENDER["sender_first_name"], SENDER["sender_last_name"],
             SENDER["sender_account_number"], SENDER["transfer_reference"]),
        )
        self.assertEqual(row["amount_sent_centavos"], 99900)
        text = json.dumps(data)
        self.assertNotIn("proof_key", text)
        self.assertNotIn("proof_sha256", text)
        self.assertEqual(
            exported().get().detail["counts"]["bank_transfers"], 1
        )
