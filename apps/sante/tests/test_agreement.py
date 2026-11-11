"""The central Membership Agreement: drafts, publishing, the command and the audit row.

TWCinc's test_agreements (38), test_agreement_operator (20) and test_agreement_file_helpers (5)
were the checklist. Not ported, with reasons: the Master Store / Subscriber write rules, the
billing-only guard, the admin forms and the authorization-evidence table (none exist here:
only an operator command writes, and the audit reason replaces the evidence); per-site
eligibility (every Santé site reads the one agreement; the route checks the site).
"""
import hashlib
import io
from datetime import date
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.common import private_files
from apps.operators.models import OperatorAuditEntry
from apps.payments.tests.base import use_local_private_store
from apps.sante import agreement
from apps.sante.models import MembershipAgreement, MembershipAgreementVersion

PDF = b"%PDF-1.4\n" + b"sample agreement\n" * 20 + b"%%EOF"
DAY = date(2026, 11, 15)
REASON = "Authorized by Coach Ed, filed in ClickUp z8uq9me7x7"


@override_settings(SANTE_AGREEMENT_ENABLED=True)
class AgreementCase(TestCase):
    def setUp(self):
        self.directory = use_local_private_store(self)

    def draft(self, label="v1", content=PDF, effective=DAY):
        return agreement.create_draft(label=label, effective_date=effective, content=content)


class DraftTests(AgreementCase):
    def test_a_draft_keeps_the_hash_size_and_a_private_key(self):
        version = self.draft()
        self.assertEqual(version.sha256, hashlib.sha256(PDF).hexdigest())
        self.assertEqual(version.size, len(PDF))
        self.assertEqual(version.state, "draft")
        self.assertRegex(version.file_key, r"^sante/agreement/[0-9a-f]{32}\.pdf$")
        self.assertEqual(private_files.read(version.file_key), PDF)
        self.assertIsNone(agreement.active_version())

    def test_only_a_pdf_is_stored(self):
        for content in (b"\x89PNG\r\n\x1a\n" + b"0" * 40, b"plain text", b""):
            with self.assertRaisesMessage(agreement.AgreementError, "not a PDF"):
                self.draft(content=content)
        self.assertEqual(MembershipAgreementVersion.objects.count(), 0)
        self.assertEqual(list(self.directory.rglob("*.*")), [])

    def test_twenty_megabytes_is_the_limit(self):
        self.draft(label="ok", content=PDF + b"0" * (agreement.MAX_BYTES - len(PDF)))
        with self.assertRaisesMessage(agreement.AgreementError, "over 20 MB"):
            self.draft(label="big", content=PDF + b"0" * (agreement.MAX_BYTES - len(PDF) + 1))

    def test_labels_are_checked_and_unique(self):
        for bad in ("", " ", "a b", "x" * 33, "../x"):
            with self.assertRaises(agreement.AgreementError):
                self.draft(label=bad)
        self.draft()
        with self.assertRaisesMessage(agreement.AgreementError, "already exists"):
            self.draft()
        self.assertEqual(len(list(self.directory.rglob("*.pdf"))), 1)

    def test_the_date_must_be_a_date(self):
        with self.assertRaises(agreement.AgreementError):
            self.draft(effective="2026-11-15")

    def test_a_failed_save_discards_the_file(self):
        with mock.patch.object(
            MembershipAgreementVersion.objects, "create", side_effect=RuntimeError("db")
        ):
            with self.assertRaises(RuntimeError):
                self.draft()
        self.assertEqual(list(self.directory.rglob("*.pdf")), [])


class PublishTests(AgreementCase):
    def test_publish_moves_the_pointer_and_audits_the_reason(self):
        first = self.draft()
        agreement.publish("v1", reason=REASON)
        first.refresh_from_db()
        self.assertEqual(first.state, "published")
        self.assertIsNotNone(first.published_at)
        self.assertEqual(agreement.active_version(), first)
        entry = OperatorAuditEntry.objects.get(action="sante_agreement_published")
        self.assertIsNone(entry.operator)
        self.assertEqual(entry.reason, REASON)
        self.assertEqual(entry.target_id, str(first.pk))
        self.assertEqual(entry.detail["sha256"], first.sha256)
        self.assertNotIn("sample", str(entry.detail))

    def test_a_newer_version_replaces_the_active_one_and_the_old_stays_readable(self):
        first = self.draft("v1")
        agreement.publish("v1", reason=REASON)
        second = self.draft("v2", content=PDF + b"more")
        agreement.publish("v2", reason=REASON)
        self.assertEqual(agreement.active_version(), second)
        self.assertEqual(agreement.published_bytes(first.pk)[1], PDF)
        entry = OperatorAuditEntry.objects.filter(action="sante_agreement_published").order_by("-pk").first()
        self.assertEqual(entry.detail["previous_version_id"], first.pk)

    def test_a_reason_is_required_and_capped(self):
        self.draft()
        for reason in ("", "   ", "x" * 501):
            with self.assertRaises(agreement.AgreementError):
                agreement.publish("v1", reason=reason)
        self.assertIsNone(agreement.active_version())
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)

    def test_unknown_and_already_published_labels_are_refused(self):
        with self.assertRaisesMessage(agreement.AgreementError, "no such version"):
            agreement.publish("nope", reason=REASON)
        self.draft()
        agreement.publish("v1", reason=REASON)
        with self.assertRaisesMessage(agreement.AgreementError, "already published"):
            agreement.publish("v1", reason=REASON)

    def test_a_file_that_no_longer_matches_cannot_be_published(self):
        version = self.draft()
        (self.directory / version.file_key).write_bytes(PDF + b"tampered")
        with self.assertRaisesMessage(agreement.AgreementError, "could not be verified"):
            agreement.publish("v1", reason=REASON)
        self.assertIsNone(agreement.active_version())
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)

    def test_a_draft_and_a_tampered_file_are_never_served(self):
        version = self.draft()
        self.assertIsNone(agreement.published_bytes(version.pk))
        agreement.publish("v1", reason=REASON)
        self.assertEqual(agreement.published_bytes(version.pk)[1], PDF)
        (self.directory / version.file_key).write_bytes(b"%PDF-changed")
        self.assertIsNone(agreement.published_bytes(version.pk))

    def test_one_agreement_row_only(self):
        self.draft()
        self.assertEqual(MembershipAgreement.objects.count(), 1)
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            MembershipAgreement.objects.create(id=2)


class CommandTests(AgreementCase):
    def run_command(self, *args, stdin=None):
        out = io.StringIO()
        if stdin is None:
            call_command("sante_agreement_publish", *args, stdout=out)
        else:
            with mock.patch("sys.stdin", mock.Mock(buffer=io.BytesIO(stdin))):
                call_command("sante_agreement_publish", *args, stdout=out)
        return out.getvalue()

    def pdf_file(self):
        path = self.directory / "in.pdf"
        path.write_bytes(PDF)
        return str(path)

    def test_draft_then_publish_from_a_file(self):
        out = self.run_command("draft", "--label", "v1", "--effective-date", "2026-11-15",
                               "--file", self.pdf_file())
        self.assertIn(hashlib.sha256(PDF).hexdigest(), out)
        self.assertNotIn("sample agreement", out)
        out = self.run_command("publish", "--label", "v1", "--reason", REASON)
        self.assertIn("Published v1", out)
        self.assertEqual(agreement.active_version().label, "v1")

    def test_the_pdf_can_come_on_standard_input(self):
        self.run_command("draft", "--label", "v1", "--effective-date", "2026-11-15",
                         "--file", "-", stdin=PDF)
        self.assertEqual(MembershipAgreementVersion.objects.get().size, len(PDF))

    def test_refusals_are_command_errors_without_file_contents(self):
        with self.assertRaisesMessage(CommandError, "could not be read"):
            self.run_command("draft", "--label", "v1", "--effective-date", "2026-11-15",
                             "--file", "/no/such/file.pdf")
        with self.assertRaisesMessage(CommandError, "YYYY-MM-DD"):
            self.run_command("draft", "--label", "v1", "--effective-date", "soon",
                             "--file", self.pdf_file())
        with self.assertRaisesMessage(CommandError, "no such version"):
            self.run_command("publish", "--label", "v9", "--reason", REASON)

    @override_settings(SANTE_AGREEMENT_ENABLED=False)
    def test_the_command_refuses_while_the_switch_is_off(self):
        with self.assertRaisesMessage(CommandError, "SANTE_AGREEMENT_ENABLED is off"):
            self.run_command("publish", "--label", "v1", "--reason", REASON)
        self.assertEqual(MembershipAgreement.objects.count(), 0)
