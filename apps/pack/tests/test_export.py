"""The Pack files export (ClickUp z8uq9mbu0p, plan 2026-11-13-export-pack-proofs, T1 to T13)."""
import base64
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.accounts import retention as account_retention
from apps.billing.models import Subscription
from apps.billing.tests.base import T0, make_subscription
from apps.common import private_files
from apps.operators.models import OperatorAuditEntry
from apps.pack import constants as C, export, purge
from apps.pack.models import (
    PackAgreementAcceptance, PackEnrollmentAuthorization, PackOrder, PackOrderEvent,
    PackProofDecision, PackProofSubmission,
)
from apps.pack.tests.owner_base import make_order
from apps.pack.tests.test_portal import SUSPENDED_AT, PortalBase, days
from apps.sites.tests.base import make_site

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes" * 30
PNG = b"\x89PNG\r\n\x1a\n" + b"png-bytes" * 30
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"webp-bytes" * 30
INSIDE = SUSPENDED_AT + days(5)
COMMAND = "apps.pack.management.commands.export_pack_files.timezone"


def zip_of(path):
    return zipfile.ZipFile(path)


def parts(archive, site):
    root = f"pack-export-{site.public_id}/"
    return (
        json.loads(archive.read(root + "manifest.json")),
        json.loads(archive.read(root + "pack.json")),
        root,
    )


class ExportBase(PortalBase):
    """The site (ended by a suspension at SUSPENDED_AT, so its window runs 30 days) with three
    verified orders whose proofs are a PNG, a JPEG and a WebP, plus decision, event, agreement and
    enrollment rows; a second site of the same account and another account's site hold files too."""

    def setUp(self):
        super().setUp()
        self.suspend()
        self.staff = get_user_model().objects.create_user("dpo", "dpo@example.test", "pw", is_staff=True)
        self.orders = [(self.order, self.proof)]
        for content, kind in ((JPEG, "image/jpeg"), (WEBP, "image/webp")):
            self.orders.append(make_order(self.site, status=C.VERIFIED, content=content, content_type=kind))
        # the first order's file is PNG bytes by make_order's default
        for order, proof in self.orders[:2]:
            PackProofDecision.objects.create(
                site=self.site, order=order, submission=proof, decision="approved",
                actor_type="owner", actor_ref=77, actor_label="ana@example.test",
            )
        PackOrderEvent.objects.create(
            site=self.site, order=self.order, kind=C.ORDER_EVENT_KINDS[0][0], old={"x": "1 Rizal St"},
            actor_type="owner", actor_ref=77, actor_label="ana@example.test",
        )
        PackAgreementAcceptance.objects.create(
            site=self.site, order=self.order, agreement=self.version.agreement, version=self.version,
            sha256=self.version.sha256, accepted_at=timezone.now(),
        )
        PackEnrollmentAuthorization.objects.create(
            site=self.site, order=self.order, text_version="v1", text="I agree to be enrolled.",
            accepted_at=timezone.now(),
        )
        self.account_ = self.site.account
        self.second_site = make_site(self.account_, name="second-store")
        self.second_order, self.second_proof = make_order(self.second_site, status=C.VERIFIED, content=JPEG, content_type="image/jpeg")
        self.foreign_order, self.foreign_proof = make_order(self.other_site, status=C.VERIFIED)
        self.out = Path(tempfile.mkdtemp(prefix="export-test-out-"))
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def run_export(self, *, at=INSIDE, site=None, ref="DPO-1", actor="dpo", basis=None, account=None,
                   mode="out", **extra):
        args = [(account or self.account_).public_id]
        options = {
            "site": (site or self.site).public_id, "actor": actor, "request_ref": ref, **extra,
        }
        if basis:
            options["basis"] = basis
        if mode == "out":
            options["out_dir"] = str(self.out)
        else:
            options["to_terminal"] = True
        out, err = io.StringIO(), io.StringIO()
        with mock.patch(COMMAND) as clock:
            clock.now.return_value = at
            call_command("export_pack_files", *args, stdout=out, stderr=err, **options)
        return out.getvalue(), err.getvalue()

    def archive_path(self, ref="DPO-1", site=None):
        return self.out / f"pack-export-{(site or self.site).public_id}-{ref}.zip"

    def audits(self):
        return OperatorAuditEntry.objects.filter(action="pack_files_exported")

    def assert_refused(self, message=None, **kwargs):
        with self.assertRaises(CommandError) as caught:
            self.run_export(**kwargs)
        if message:
            self.assertIn(message, str(caught.exception))
        self.assertEqual(self.audits().count(), 0)
        self.assertEqual(list(self.out.iterdir()), [])


class ContentTests(ExportBase):
    def test_t1_every_file_and_row_is_in_the_archive_with_matching_sha256(self):
        self.run_export()
        with zip_of(self.archive_path()) as archive:
            manifest, pack, root = parts(archive, self.site)
            self.assertEqual(
                manifest["counts"],
                {"orders": 3, "proofs": 3, "files": 3, "bytes": sum(i["bytes"] for i in manifest["files"])},
            )
            self.assertEqual(len(manifest["files"]), 3)
            for item in manifest["files"]:
                data = archive.read(root + item["file"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                self.assertEqual(len(data), item["bytes"])
            stored = {proof.public_id: private_files.read(proof.file_key) for _, proof in self.orders}
            by_proof = {item["proof"]: item for item in manifest["files"]}
            for proof_id, content in stored.items():
                self.assertEqual(by_proof[proof_id]["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(sorted(i["file"].rsplit(".", 1)[1] for i in manifest["files"]), ["jpg", "png", "webp"])
            self.assertEqual(len(pack["orders"]), 3)
            self.assertEqual(len(pack["proofs"]), 3)
            self.assertEqual(
                {p["sha256"] for p in pack["proofs"]}, {i["sha256"] for i in manifest["files"]}
            )
            self.assertEqual(len(pack["decisions"]), 2)
            self.assertEqual(len(pack["order_events"]), 1)
            self.assertEqual(len(pack["agreement_acceptances"]), 1)
            self.assertEqual(pack["enrollment_authorizations"][0]["text"], "I agree to be enrolled.")
            self.assertEqual(manifest["pack_json"]["sha256"], hashlib.sha256(archive.read(root + "pack.json")).hexdigest())
            self.assertEqual(manifest["basis"], "window")
            self.assertEqual(datetime.fromisoformat(manifest["window"]["service_ended_at"]), SUSPENDED_AT)

    def test_t2_the_archive_holds_the_image_the_purge_would_delete(self):
        self.run_export()
        key, content = self.proof.file_key, private_files.read(self.proof.file_key)
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", SUSPENDED_AT.date()):
            self.assertTrue(purge.files_deletable(SUSPENDED_AT))
            private_files.delete(key)  # what a purge does to the file
        with self.assertRaises(FileNotFoundError):
            private_files.read(key)
        with zip_of(self.archive_path()) as archive:
            manifest, _, root = parts(archive, self.site)
            item = next(i for i in manifest["files"] if i["proof"] == self.proof.public_id)
            self.assertEqual(archive.read(root + item["file"]), content)

    def test_t3_nothing_of_another_site_or_account(self):
        self.run_export()
        with zip_of(self.archive_path()) as archive:
            blob = b"".join(archive.read(n) for n in archive.namelist()).decode("latin-1")
        for gone in (
            self.second_order.public_id, self.second_order.order_number, self.second_proof.public_id,
            self.foreign_order.public_id, self.foreign_order.order_number, self.foreign_proof.public_id,
            self.second_site.public_id, self.other_site.public_id, self.second_order.member.public_id,
        ):
            self.assertNotIn(gone, blob)
        self.assertEqual(self.audits().count(), 1)

    def test_t3_another_accounts_site_is_refused(self):
        self.assert_refused("No such site for this account", site=self.other_site)
        self.assert_refused("No such site for this account", site=mock.Mock(public_id="site_nope"))


class WindowTests(ExportBase):
    def test_t4_refused_before_the_end_after_the_window_and_for_served_sites(self):
        self.assert_refused("outside its 30-day", at=SUSPENDED_AT - timedelta(seconds=1))
        self.assert_refused("outside its 30-day", at=SUSPENDED_AT + days(30) + timedelta(seconds=1))
        # a trial site is still served
        served = make_subscription(email="cy@example.test", name="cys-store").site
        make_order(served, status=C.VERIFIED)
        self.assert_refused("not in its export window", site=served, account=served.account, at=T0 + days(2))

    def test_t4_exact_edges_are_accepted(self):
        self.run_export(at=SUSPENDED_AT)
        self.run_export(at=SUSPENDED_AT + days(30), ref="DPO-2")
        self.assertEqual(self.audits().count(), 2)

    def test_t4_exempt_and_reactivated_sites_are_refused(self):
        Subscription.objects.filter(site=self.site).update(billing_exempt=True)
        self.assert_refused("not in its export window")

    def test_t4_the_window_never_outlives_the_deletion_point(self):
        # a never-paid site: its deletion point is exactly the end of the 30 days
        end = SUSPENDED_AT
        with mock.patch("apps.pack.export.billing.end_dates", return_value=(end, end + days(30))):
            self.assertEqual(export.window_error(self.site, end + days(30) - timedelta(seconds=1)), "")
            self.assertNotEqual(export.window_error(self.site, end + days(30)), "")
            self.assertEqual(export.window_error(self.site, end), "")

    def test_a_removed_site_is_still_exported_inside_its_window(self):
        from apps.sites.models import Site
        Site.objects.filter(pk=self.site.pk).update(deleted_at=INSIDE)
        self.run_export()
        self.assertEqual(self.audits().count(), 1)


class AccessTests(ExportBase):
    def test_t5_actor_reference_switch_and_account(self):
        self.assert_refused("--actor must be an active staff user", actor="nobody")
        get_user_model().objects.create_user("plain", "p@example.test", "pw")
        self.assert_refused("--actor must be an active staff user", actor="plain")
        get_user_model().objects.filter(username="dpo").update(is_active=False)
        self.assert_refused("--actor must be an active staff user")
        get_user_model().objects.filter(username="dpo").update(is_active=True)
        self.assert_refused("not an address", ref="ana@example.test")
        with override_settings(PACK_ENABLED=False):
            self.assert_refused("Pack is off here")
        with mock.patch.object(account_retention, "is_disposed", return_value=True):
            self.assert_refused("disposed")

    def test_exactly_one_output_is_required(self):
        with self.assertRaises(CommandError):
            call_command("export_pack_files", self.account_.public_id, site=self.site.public_id,
                         actor="dpo", request_ref="R", out_dir=None, to_terminal=False)

    def test_t6_mode_and_replay(self):
        self.run_export()
        path = self.archive_path()
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        before = path.read_bytes()
        with self.assertRaises(CommandError):
            self.run_export()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.audits().count(), 1)
        self.run_export(ref="DPO-2")
        self.assertEqual(self.audits().count(), 2)
        self.assertTrue(self.archive_path("DPO-2").exists())


class SafetyTests(ExportBase):
    def test_t7_nothing_forbidden_is_in_any_json(self):
        self.run_export()
        with zip_of(self.archive_path()) as archive:
            _, pack, root = parts(archive, self.site)
            text = archive.read(root + "pack.json").decode() + archive.read(root + "manifest.json").decode()

        def names(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    yield k
                    yield from names(v)
            elif isinstance(value, list):
                for v in value:
                    yield from names(v)

        for name in names(pack):
            for bad in ("key", "token", "secret", "hash", "ip_advisory", "actor_ref", "actor_label"):
                self.assertNotIn(bad, name.replace("sha256", ""), name)
        for forbidden in (self.proof.file_key, "ana@example.test", "203.0.113.9", "001122" + "x"):
            self.assertNotIn(forbidden, text)
        number = self.account.account_number if hasattr(self.account, "account_number") else None
        if number:
            self.assertNotIn(number, text)
        self.assertEqual(
            {p["account_last4"] for p in pack["proofs"]}, {self.account.account_number[-4:]}
        )

    def test_t8_a_store_error_leaves_no_output_and_no_audit(self):
        real = private_files.read
        calls = []

        def flaky(key):
            calls.append(key)
            if len(calls) == 2:
                raise private_files.StoreError("down")
            return real(key)

        with mock.patch.object(private_files, "read", side_effect=flaky):
            self.assert_refused("could not be read")

    def test_t8_an_empty_key_is_listed_a_wrong_size_is_a_warning(self):
        PackProofSubmission._base_manager.filter(pk=self.proof.pk).update(file_key="")
        PackProofSubmission._base_manager.filter(pk=self.orders[1][1].pk).update(size=1)
        self.run_export()
        with zip_of(self.archive_path()) as archive:
            manifest, pack, _ = parts(archive, self.site)
        self.assertEqual(manifest["no_file"], [{"proof": self.proof.public_id, "reason": "key_empty"}])
        self.assertEqual(manifest["warnings"][0]["warning"], "stored_size_differs")
        self.assertEqual(len(manifest["files"]), 2)
        empty = next(p for p in pack["proofs"] if p["id"] == self.proof.public_id)
        self.assertEqual((empty["file"], empty["no_file_reason"]), (None, "key_empty"))

    def test_t8_over_the_limit_is_refused(self):
        with mock.patch.object(export, "MAX_ARCHIVE_BYTES", 100):
            self.assert_refused("200 MiB")

    def test_t9_one_audit_entry_with_counts_and_no_personal_value(self):
        self.run_export()
        entry = self.audits().get()
        self.assertEqual((entry.target_type, entry.target_id, entry.reason), ("site", self.site.public_id, "DPO-1"))
        self.assertEqual(entry.operator, self.staff)
        self.assertEqual(entry.detail["counts"]["files"], 3)
        self.assertEqual(entry.detail["basis"], "window")
        self.assertEqual(
            entry.detail["archive_sha256"], hashlib.sha256(self.archive_path().read_bytes()).hexdigest()
        )
        self.assertEqual(entry.personal, {})

    def test_t10_the_gate_is_unchanged(self):
        self.assertIsNone(purge.PROOF_EXPORT_READY_FROM)
        self.assertFalse(purge.files_deletable(SUSPENDED_AT))

    def test_t11_it_writes_no_pack_row(self):
        def state():
            return [
                list(model._base_manager.order_by("pk").values())
                for model in (PackOrder, PackProofSubmission, PackProofDecision, PackOrderEvent,
                              PackAgreementAcceptance, PackEnrollmentAuthorization)
            ]

        before, audits = state(), OperatorAuditEntry.objects.count()
        files = sorted(Path(self.directory).rglob("*"))
        self.run_export()
        self.assertEqual(state(), before)
        self.assertEqual(sorted(Path(self.directory).rglob("*")), files)
        self.assertEqual(OperatorAuditEntry.objects.count(), audits + 1)


class OutputTests(ExportBase):
    def test_t12_stdout_base64_decodes_to_the_archive_with_the_printed_sha256(self):
        out, err = self.run_export(mode="stdout")
        data = base64.b64decode(out.replace("\r", "").encode())
        self.assertIn(hashlib.sha256(data).hexdigest(), err)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(parts(archive, self.site)[0]["files"]), 3)
        # a carriage-return redirect (a pty) decodes the same after `tr -d '\r'`
        crlf = out.replace("\n", "\r\n")
        self.assertEqual(base64.b64decode(crlf.replace("\r", "").encode()), data)
        self.assertEqual(self.audits().count(), 1)
        self.run_export(mode="stdout")  # a repeat is another hand-over
        self.assertEqual(self.audits().count(), 2)

    def test_the_same_files_make_the_same_images_and_the_same_manifest_hashes(self):
        self.run_export(ref="A")
        self.run_export(ref="B", at=INSIDE + timedelta(hours=1))
        with zip_of(self.archive_path("A")) as a, zip_of(self.archive_path("B")) as b:
            ma, pa, root = parts(a, self.site)
            mb, pb, _ = parts(b, self.site)
            self.assertEqual(ma["files"], mb["files"])
            self.assertEqual(pa, pb)
            self.assertEqual(a.namelist(), b.namelist())

    def test_t13_the_account_export_pack_part_is_unchanged(self):
        from apps.pack import retention as pack_retention
        data = pack_retention.export_packs_of(self.account_)
        self.assertEqual(sorted(data), ["pack_orders", "pack_proof_submissions", "sante_registrations"])
        self.assertEqual(len(data["pack_orders"]), 4)  # three orders and the second site's
        for row in data["pack_proof_submissions"]:
            self.assertNotIn("file", row)
            self.assertNotIn("sha256", row)


class RequestBasisTests(ExportBase):
    """D3: a person's own access or deletion request on a site that is still served. This class,
    the `basis` argument of export.window_error and the --basis option are the whole of D3."""

    def test_a_served_site_is_exported_on_request_and_the_audit_says_so(self):
        served = make_subscription(email="cy@example.test", name="cys-store").site
        make_order(served, status=C.VERIFIED)
        self.run_export(site=served, account=served.account, basis="request", at=INSIDE)
        entry = self.audits().get()
        self.assertEqual(entry.detail["basis"], "request")
        with zip_of(self.archive_path(site=served)) as archive:
            manifest, _, _ = parts(archive, served)
        self.assertEqual(manifest["basis"], "request")
        self.assertEqual(manifest["counts"]["files"], 1)

    def test_it_still_needs_staff_a_reference_and_pack_orders(self):
        self.assert_refused("--actor must be an active staff user", actor="nobody", basis="request")
        self.assert_refused("not an address", ref="a@b.test", basis="request")
        empty = make_site(self.account_, name="empty-store")
        self.assert_refused("no Pack orders", site=empty, basis="request")

    def test_the_default_basis_is_still_the_window(self):
        self.assert_refused("outside its 30-day", at=SUSPENDED_AT + days(40))
        self.run_export(at=SUSPENDED_AT + days(40), basis="request")
        self.assertEqual(self.audits().get().detail["basis"], "request")
