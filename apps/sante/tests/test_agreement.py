"""The central Membership Agreement: drafts, publishing, the command, the audit.

TWCinc's test_agreements (38), test_agreement_operator (20) and test_agreement_file_helpers (5)
were the checklist; every group is ported below except, with reasons:
- Santé's authorization letter (record, verify, revoke, the publish gate): dropped by Coach Ed's
  ruling of 2026-11-11 (no need for it); publish keeps an audit reason instead.
- Subscriber/Master Store write path, its 403s and the Subscriber API shape: there is no
  Subscriber path here; only an operator command writes (no Django admin screens, ruling
  2026-11-11).
- Admin form, admin view and admin migration tests (test_agreement_admin, ..._version_admin,
  ..._migration): no admin screens; the tables are created by sante.0002.
- Per-site eligibility (template catalog_key, site state): the storefront route checks the
  host's served Santé site; the agreement itself is the same for every site.
- Race tests (TWCinc locks on SQLite-less Postgres): the lock order is ported; the concurrent
  cases need the PostgreSQL job and are not written here.
"""
import hashlib
import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.common import private_files
from apps.billing import services as billing
from apps.operators.models import OperatorAuditEntry
from apps.payments.tests.base import use_local_private_store
from apps.sante import agreement
from apps.sante.models import (
    ImmutableRecordError, MembershipAgreement, MembershipAgreementVersion,
)
from apps.sante.tests.agreement_helpers import (
    DAY, LETTER_DAY, PDF, PNG, REASON, draft, make_master, make_operator, published,
)


@override_settings(SANTE_AGREEMENT_ENABLED=True, BILLING_ENABLED=True, SITE_PARENT_HOST="negosyotest.com")
class AgreementCase(TestCase):
    def setUp(self):
        self.directory = use_local_private_store(self)
        self.op = make_operator()
        self.site = make_master(self.op)

    def actions(self, name):
        return OperatorAuditEntry.objects.filter(action=name).order_by("pk")


class DraftTests(AgreementCase):
    def test_a_draft_keeps_the_hash_size_and_a_private_key(self):
        version = draft(self.op)
        self.assertEqual(version.sha256, hashlib.sha256(PDF).hexdigest())
        self.assertEqual(version.size, len(PDF))
        self.assertEqual(version.state, "draft")
        self.assertEqual(version.master_site, self.site)
        self.assertRegex(version.storage_key, r"^sante/agreement/[0-9a-f]{32}\.pdf$")
        self.assertEqual(private_files.read(version.storage_key), PDF)
        self.assertIsNone(agreement.active_version())
        self.assertEqual(self.actions("sante_agreement_version_created").count(), 1)

    def test_only_a_pdf_is_stored(self):
        for content in (PNG, b"plain text", b""):
            with self.assertRaisesMessage(agreement.AgreementError, "not an allowed file type"):
                draft(self.op, content=content)
        self.assertEqual(MembershipAgreementVersion.objects.count(), 0)
        self.assertEqual(list(self.directory.rglob("*.pdf")), [])

    def test_twenty_megabytes_is_the_limit(self):
        draft(self.op, "ok", content=PDF + b"0" * (agreement.MAX_BYTES - len(PDF)))
        with self.assertRaisesMessage(agreement.AgreementError, "over 20 MB"):
            draft(self.op, "big", content=PDF + b"0" * (agreement.MAX_BYTES - len(PDF) + 1))

    def test_title_label_and_date_are_checked_and_the_label_is_unique(self):
        for changes in ({"title": " "}, {"title": "x" * 161}, {"label": " "}, {"label": "x" * 33},
                        {"effective": "2026-11-15"}):
            changes = {"label": "z", **changes} if "label" not in changes else changes
            with self.assertRaises(agreement.AgreementError):
                draft(self.op, **changes)
        draft(self.op)
        with self.assertRaisesMessage(agreement.AgreementError, "already exists"):
            draft(self.op)
        self.assertEqual(len(list(self.directory.rglob("agreement/*.pdf"))), 1)

    def test_a_failed_save_discards_the_file(self):
        with mock.patch.object(MembershipAgreementVersion.objects, "create",
                               side_effect=RuntimeError("db")):
            with self.assertRaises(RuntimeError):
                draft(self.op)
        self.assertEqual(list(self.directory.rglob("*.pdf")), [])

    def test_update_and_delete_work_on_a_draft_only(self):
        draft(self.op)
        agreement.update_draft(self.op, "v1", title="New title", new_version_label="v1b",
                               effective_date=LETTER_DAY)
        version = MembershipAgreementVersion.objects.get()
        self.assertEqual((version.title, version.version_label, version.effective_date),
                         ("New title", "v1b", LETTER_DAY))
        draft(self.op, "v2")
        with self.assertRaisesMessage(agreement.AgreementError, "already exists"):
            agreement.update_draft(self.op, "v2", title="t", new_version_label="v1b",
                                   effective_date=DAY)
        key = version.storage_key
        with self.captureOnCommitCallbacks(execute=True):
            agreement.delete_draft(self.op, "v1b")
        self.assertFalse(MembershipAgreementVersion.objects.filter(pk=version.pk).exists())
        with self.assertRaises(Exception):
            private_files.read(key)
        self.assertEqual(self.actions("sante_agreement_version_deleted").count(), 1)
        with self.assertRaisesMessage(agreement.AgreementError, "not found"):
            agreement.delete_draft(self.op, "nope")
        published(self.op, "v3")
        for call in (lambda: agreement.delete_draft(self.op, "v3"),
                     lambda: agreement.update_draft(self.op, "v3", title="t",
                                                    new_version_label="v3", effective_date=DAY)):
            with self.assertRaisesMessage(agreement.AgreementError, "published version cannot"):
                call()


class WriterTests(AgreementCase):
    def test_only_an_active_staff_user_may_write(self):
        from django.contrib.auth import get_user_model

        user = get_user_model()
        plain = user.objects.create_user("plain", "p@example.test", "pw")
        gone = make_operator("gone", is_active=False)
        for who in (plain, gone, None):
            with self.assertRaisesMessage(agreement.AgreementError, "active staff operator"):
                draft(who)

    def test_no_owner_means_no_writes(self):
        from apps.sante import services

        services.set_catalog_owner(None, operator=self.op)
        with self.assertRaisesMessage(agreement.AgreementError, "Set the Santé catalog owner"):
            draft(self.op)

    def test_a_billing_only_owner_site_cannot_write(self):
        with mock.patch.object(billing, "position",
                               return_value=mock.Mock(billing_only=True)):
            with self.assertRaisesMessage(agreement.AgreementError, "suspended, cancelled"):
                draft(self.op)
        draft(self.op)  # reinstated: passes again by itself


class PublishTests(AgreementCase):
    def test_publish_moves_the_pointer_and_audits(self):
        first = published(self.op)
        self.assertEqual(first.state, "published")
        self.assertIsNotNone(first.published_at)
        self.assertEqual(agreement.active_version(), first)
        entry = self.actions("sante_agreement_published").get()
        self.assertEqual(entry.operator, self.op)
        self.assertEqual(entry.target_id, str(first.pk))
        self.assertEqual(entry.detail["sha256"], first.sha256)
        self.assertEqual(entry.reason, REASON)
        self.assertNotIn("sample", str(entry.detail))

    def test_a_newer_version_replaces_the_active_one_and_the_old_stays_readable(self):
        first = published(self.op, "v1")
        second = published(self.op, "v2", content=PDF + b"more")
        self.assertEqual(agreement.active_version(), second)
        self.assertEqual(agreement.published_bytes(first.pk)[1], PDF)
        self.assertEqual(self.actions("sante_agreement_published").last().detail["previous_version_id"],
                         first.pk)

    def test_a_reason_is_required_and_capped(self):
        draft(self.op)
        for reason in ("", "   ", "x" * 501):
            with self.assertRaises(agreement.AgreementError):
                agreement.publish(self.op, "v1", reason=reason)
        self.assertIsNone(agreement.active_version())
        self.assertEqual(self.actions("sante_agreement_published").count(), 0)

    def test_unknown_and_already_published_labels_are_refused(self):
        with self.assertRaisesMessage(agreement.AgreementError, "not found"):
            agreement.publish(self.op, "nope", reason=REASON)
        published(self.op)
        with self.assertRaisesMessage(agreement.AgreementError, "published version cannot"):
            agreement.publish(self.op, "v1", reason=REASON)

    def test_a_file_that_no_longer_matches_cannot_be_published(self):
        version = draft(self.op)
        (self.directory / version.storage_key).write_bytes(PDF + b"tampered")
        with self.assertRaisesMessage(agreement.AgreementError, "could not be verified"):
            agreement.publish(self.op, "v1", reason=REASON)
        self.assertIsNone(agreement.active_version())
        self.assertEqual(self.actions("sante_agreement_published").count(), 0)

    def test_a_draft_and_a_tampered_file_are_never_served(self):
        version = draft(self.op)
        self.assertIsNone(agreement.published_bytes(version.pk))
        agreement.publish(self.op, "v1", reason=REASON)
        self.assertEqual(agreement.published_bytes(version.pk)[1], PDF)
        (self.directory / version.storage_key).write_bytes(b"%PDF-changed")
        self.assertIsNone(agreement.published_bytes(version.pk))

    def test_a_published_row_cannot_be_changed_or_deleted(self):
        version = published(self.op)
        version.title = "x"
        with self.assertRaises(ImmutableRecordError):
            version.save()
        with self.assertRaises(ImmutableRecordError):
            version.delete()

    def test_one_agreement_row_only(self):
        draft(self.op)
        self.assertEqual(MembershipAgreement.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MembershipAgreement.objects.create(id=2)

    def test_the_status_lines_show_no_file_contents(self):
        published(self.op)
        text = "\n".join(agreement.status())
        self.assertIn("ACTIVE", text)
        self.assertNotIn("sample", text)


class CommandTests(AgreementCase):
    def run_command(self, *args, stdin=None):
        out = io.StringIO()
        if stdin is None:
            call_command("sante_agreement_publish", *args, stdout=out)
        else:
            with mock.patch("sys.stdin", mock.Mock(buffer=io.BytesIO(stdin))):
                call_command("sante_agreement_publish", *args, stdout=out)
        return out.getvalue()

    def file(self, content=PDF, name="in.pdf"):
        path = self.directory / name
        path.write_bytes(content)
        return str(path)

    def test_the_whole_process_by_command(self):
        op = ["--operator", "op"]
        out = self.run_command("draft", *op, "--title", "T", "--label", "v1",
                               "--effective-date", "2026-11-15", "--file", "-", stdin=PDF)
        self.assertIn(hashlib.sha256(PDF).hexdigest(), out)
        self.assertNotIn("sample agreement", out)
        self.run_command("update", *op, "--label", "v1", "--title", "T2", "--new-label", "v1",
                         "--effective-date", "2026-11-16")
        with self.assertRaises(CommandError):  # --reason is required
            self.run_command("publish", *op, "--label", "v1")
        out = self.run_command("publish", *op, "--label", "v1", "--reason", REASON)
        self.assertIn("Published v1", out)
        status = self.run_command("status")
        self.assertIn("ACTIVE", status)
        self.assertNotIn("sample agreement", status)

    def test_refusals_are_command_errors(self):
        op = ["--operator", "op"]
        with self.assertRaisesMessage(CommandError, "could not be read"):
            self.run_command("draft", *op, "--title", "T", "--label", "v1",
                             "--effective-date", "2026-11-15", "--file", "/no/such.pdf")
        with self.assertRaisesMessage(CommandError, "YYYY-MM-DD"):
            self.run_command("draft", *op, "--title", "T", "--label", "v1",
                             "--effective-date", "soon", "--file", self.file())
        with self.assertRaisesMessage(CommandError, "active staff operator"):
            self.run_command("delete", "--operator", "nobody", "--label", "v1")

    @override_settings(SANTE_AGREEMENT_ENABLED=False)
    def test_the_command_refuses_while_the_switch_is_off(self):
        with self.assertRaisesMessage(CommandError, "SANTE_AGREEMENT_ENABLED is off"):
            self.run_command("status")
