"""Proposing, approving, cancelling and holding (plan z8uq9mb22v, sections 5, 6.2, 6.3 and 9)."""
import io
import json
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.common.errors import ApiError
from apps.lifecycle import workflow
from apps.lifecycle.models import ErasureBatch, ErasureHold, ErasureItem
from apps.lifecycle.tests.base import NOW, ended_site, days
from apps.lifecycle.tests.run_base import EMAIL, RunTestCase, add_due_site, every_row, staff_user
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Site


class ProposeTests(RunTestCase):
    def test_a_proposal_freezes_the_list_deletes_nothing_and_is_audited(self):
        before = {k: v for k, v in every_row().items() if not k.startswith("operators.OperatorAudit")}
        batch = self.propose()
        after = {k: v for k, v in every_row().items() if not k.startswith("operators.OperatorAudit")}
        changed = {k for k in after if after[k] != before[k]}
        self.assertEqual(changed, {"lifecycle.ErasureBatch", "lifecycle.ErasureItem"})
        self.assertEqual(batch.status, "proposed")
        self.assertEqual(batch.expires_at, NOW + days(7))
        self.assertEqual(len(batch.list_digest), 64)
        item = batch.items.get()
        self.assertEqual((item.site_id, item.status, item.subscription_id), (self.site.pk, "pending", self.sub.pk))
        self.assertEqual(item.deletion_due_at, self.sub.deletion_due_at)
        self.assertEqual(item.planned_counts["counts"]["members"], 2)
        self.assertEqual(item.planned_counts["account_action"], "dispose")
        entry = OperatorAuditEntry.objects.get(action="erasure_batch_proposed")
        self.assertEqual(entry.target_id, batch.public_id)
        self.assertEqual(entry.operator, self.staff)
        self.assertEqual(Site.objects.get(pk=self.site.pk).contact_email, EMAIL)

    def test_one_to_twenty_five_sites(self):
        with self.assertRaises(ApiError) as raised:
            workflow.propose(self.staff, [], NOW)
        self.assertEqual(raised.exception.code, "invalid_request")
        with self.assertRaises(ApiError):
            workflow.propose(self.staff, [f"site_{i}" for i in range(26)], NOW)
        with self.assertRaises(ApiError):
            workflow.propose(self.staff, "site_x", NOW)

    def test_a_site_that_is_not_eligible_is_refused_with_its_code(self):
        not_due = ended_site("n@example.test", "n-store", due=NOW + days(3))
        with self.assertRaises(ApiError) as raised:
            workflow.propose(self.staff, [self.site.public_id, not_due.site.public_id], NOW)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.fields, {"sites": [f"{not_due.site.public_id}: not_due"]})
        self.assertEqual(ErasureBatch.objects.count(), 0)
        with self.assertRaises(ApiError) as raised:
            workflow.propose(self.staff, ["site_missing"], NOW)
        self.assertIn("not_due", raised.exception.fields["sites"][0])

    def test_a_site_in_an_open_batch_cannot_be_proposed_again(self):
        self.propose()
        with self.assertRaises(ApiError) as raised:
            self.propose()
        self.assertIn("already_in_batch", raised.exception.fields["sites"][0])

    def test_a_cancelled_batch_frees_its_sites(self):
        batch = self.propose()
        workflow.cancel(self.staff, batch.public_id, reason="redo", now=NOW)
        self.assertEqual(self.propose().items.count(), 1)

    def test_duplicates_in_the_request_count_once(self):
        batch = workflow.propose(self.staff, [self.site.public_id, self.site.public_id], NOW)
        self.assertEqual(batch.items.count(), 1)

    def test_the_digest_follows_the_list(self):
        other = add_due_site(self.sub)
        one = workflow.propose(self.staff, [self.site.public_id], NOW)
        workflow.cancel(self.staff, one.public_id, reason="redo", now=NOW)
        two = workflow.propose(self.staff, [self.site.public_id, other.site.public_id], NOW)
        self.assertNotEqual(one.list_digest, two.list_digest)


class ApproveTests(RunTestCase):
    def test_the_typed_confirmation_approves(self):
        batch = self.propose()
        workflow.approve(
            self.staff, batch.public_id, confirmation=f"erase batch {batch.public_id} 1 sites",
            exclude=[], reason="weekly review", now=NOW,
        )
        batch = self.fresh_batch(batch)
        self.assertEqual((batch.status, batch.approved_by, batch.approved_at), ("approved", self.staff, NOW))
        self.assertEqual(batch.approval_reason, "weekly review")
        entry = OperatorAuditEntry.objects.get(action="erasure_batch_approved")
        self.assertEqual(entry.detail, {"kept": 1, "excluded": 0, "digest": batch.list_digest})
        self.assertEqual(entry.reason, "weekly review")

    def test_anything_else_is_refused(self):
        batch = self.propose()
        for typed in (
            "", None, "erase batch 1 sites", f"erase batch {batch.public_id} 2 sites",
            f"erase batch {batch.public_id} 1 site", f"ERASE BATCH {batch.public_id} 1 SITES",
            f"erase {batch.public_id} 1 sites", "yes",
        ):
            with self.subTest(typed=typed):
                with self.assertRaises(ApiError) as raised:
                    workflow.approve(
                        self.staff, batch.public_id, confirmation=typed, exclude=[], reason="r", now=NOW
                    )
                self.assertEqual(raised.exception.code, "confirmation_mismatch")
        self.assertEqual(self.fresh_batch(batch).status, "proposed")

    def test_the_reason_is_required_short_and_has_no_address(self):
        batch = self.propose()
        for reason in ("", None, "x" * 301, "mail me at a@b.test"):
            with self.subTest(reason=reason):
                with self.assertRaises(ApiError) as raised:
                    workflow.approve(
                        self.staff, batch.public_id,
                        confirmation=f"erase batch {batch.public_id} 1 sites", exclude=[],
                        reason=reason, now=NOW,
                    )
                self.assertEqual(raised.exception.code, "invalid_request")

    def test_exclusion_drops_an_item_and_the_confirmation_counts_the_rest(self):
        other = add_due_site(self.sub)
        batch = self.propose(self.sub, other)
        with self.assertRaises(ApiError):  # two sites remain: "1 sites" is wrong
            workflow.approve(
                self.staff, batch.public_id, confirmation=f"erase batch {batch.public_id} 1 sites",
                exclude=[], reason="r", now=NOW,
            )
        self.approve(batch, exclude=[other.site.public_id])
        self.assertEqual(self.item(batch, other).status, "excluded")
        self.assertEqual(self.item(batch).status, "pending")
        self.run_batch(batch)
        self.assertEqual(Site.objects.get(pk=other.site.pk).contact_email, other.site.contact_email)
        self.assertEqual(self.item(batch).status, "erased")
        self.assertEqual(self.item(batch, other).status, "excluded")
        self.assertEqual(self.fresh_batch(batch).status, "done")

    def test_an_item_cannot_be_added_and_not_everything_can_be_excluded(self):
        batch = self.propose()
        with self.assertRaises(ApiError) as raised:
            workflow.approve(
                self.staff, batch.public_id, confirmation=f"erase batch {batch.public_id} 1 sites",
                exclude=["site_not_in_it"], reason="r", now=NOW,
            )
        self.assertEqual(raised.exception.fields["exclude"], ["Only sites of this batch can be excluded."])
        with self.assertRaises(ApiError):
            workflow.approve(
                self.staff, batch.public_id, confirmation=f"erase batch {batch.public_id} 0 sites",
                exclude=[self.site.public_id], reason="r", now=NOW,
            )

    def test_an_expired_proposal_cannot_be_approved_and_is_cancelled(self):
        batch = self.propose()
        late = NOW + days(8)
        with self.assertRaises(ApiError) as raised:
            self.approve(batch, now=late)
        self.assertEqual(raised.exception.code, "proposal_expired")
        self.assertEqual(self.fresh_batch(batch).status, "cancelled")
        entry = OperatorAuditEntry.objects.get(action="erasure_batch_cancelled")
        self.assertEqual(entry.reason, "expired")

    def test_a_batch_is_approved_once(self):
        batch = self.approved()
        with self.assertRaises(ApiError) as raised:
            self.approve(batch)
        self.assertEqual(raised.exception.code, "wrong_status")

    def test_running_an_unapproved_batch_is_refused(self):
        batch = self.propose()
        with self.assertRaises(ApiError) as raised:
            self.run_batch(batch)
        self.assertEqual(raised.exception.code, "not_approved")
        self.assertEqual(Site.objects.get(pk=self.site.pk).contact_email, EMAIL)

    def test_an_unknown_batch_is_not_found(self):
        with self.assertRaises(ApiError) as raised:
            self.approve_missing()
        self.assertEqual(raised.exception.status, 404)

    def approve_missing(self):
        return workflow.approve(
            self.staff, "ebt_missing", confirmation="x", exclude=[], reason="r", now=NOW
        )

    def test_an_approved_batch_that_never_started_expires(self):
        batch = self.approved()
        later = NOW + days(8)
        result = self.run_batch(batch, now=later)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(Site.objects.get(pk=self.site.pk).contact_email, EMAIL)

    def test_the_task_cancels_old_proposals(self):
        batch = self.propose()
        self.assertEqual(workflow.run_approved(NOW + days(8))["expired"], 1)
        self.assertEqual(self.fresh_batch(batch).status, "cancelled")

    def test_cancel(self):
        batch = self.approved()
        workflow.cancel(self.staff, batch.public_id, reason="owner asked", now=NOW)
        self.assertEqual(self.fresh_batch(batch).status, "cancelled")
        with self.assertRaises(ApiError) as raised:
            workflow.cancel(self.staff, batch.public_id, reason="again", now=NOW)
        self.assertEqual(raised.exception.code, "wrong_status")


class HoldWorkflowTests(RunTestCase):
    def test_place_and_release_are_audited(self):
        workflow.place_hold(self.staff, self.site.public_id, reason_code="dispute", reference="T-7", now=NOW)
        hold = ErasureHold.objects.get()
        self.assertEqual((hold.reason_code, hold.reference, hold.placed_by), ("dispute", "T-7", self.staff))
        workflow.release_hold(self.staff, self.site.public_id, now=NOW)
        hold.refresh_from_db()
        self.assertEqual(hold.released_by, self.staff)
        entries = OperatorAuditEntry.objects.filter(action__startswith="erasure_hold").order_by("pk")
        self.assertEqual([e.action for e in entries], ["erasure_hold_set", "erasure_hold_released"])
        self.assertEqual(entries[0].detail, {"reason_code": "dispute", "reference": "T-7"})
        self.assertEqual(entries[0].target_id, self.site.public_id)

    def test_the_reason_and_the_reference_are_checked(self):
        for kwargs in (
            {"reason_code": "because"}, {"reason_code": "legal", "reference": "a@b.test"},
            {"reason_code": "legal", "reference": "x" * 101},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ApiError):
                    workflow.place_hold(
                        self.staff, self.site.public_id, **{"reference": "", **kwargs}, now=NOW
                    )
        with self.assertRaises(ApiError):
            workflow.place_hold(self.staff, "site_missing", reason_code="legal", reference="", now=NOW)
        self.assertEqual(ErasureHold.objects.count(), 0)

    def test_releasing_a_site_with_no_hold_is_refused(self):
        with self.assertRaises(ApiError) as raised:
            workflow.release_hold(self.staff, self.site.public_id, now=NOW)
        self.assertEqual(raised.exception.code, "not_held")


class CommandTests(RunTestCase):
    def setUp(self):
        super().setUp()
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def call(self, name, *args, **options):
        out = io.StringIO()
        call_command(name, *args, stdout=out, **options)
        return out.getvalue()

    def test_propose_approve_run_by_command(self):
        out = json.loads(self.call("erasure_propose", self.site.public_id, actor=self.staff.username))
        batch_id = out["id"]
        self.assertEqual(out["status"], "proposed")
        approved = json.loads(self.call(
            "erasure_approve", batch_id, actor=self.staff.username, reason="weekly review",
            confirm=f"erase batch {batch_id} 1 sites",
        ))
        self.assertEqual(approved["status"], "approved")
        ran = json.loads(self.call("erasure_run", batch_id))
        self.assertEqual((ran["status"], ran["erased"]), ("done", 1))
        self.assertEqual(Site.objects.get(pk=self.site.pk).contact_email, "")

    def test_the_commands_refuse_a_wrong_confirmation_and_a_non_staff_actor(self):
        out = json.loads(self.call("erasure_propose", self.site.public_id, actor=self.staff.username))
        with self.assertRaises(CommandError):
            self.call("erasure_approve", out["id"], actor=self.staff.username, reason="r", confirm="yes")
        with self.assertRaises(CommandError):
            self.call("erasure_propose", self.site.public_id, actor="nobody")
        with self.assertRaises(CommandError):
            self.call("erasure_approve", out["id"], actor=self.staff.username, reason="r")
        with self.assertRaises(CommandError):
            self.call("erasure_run", out["id"])  # not approved

    def test_cancel_and_hold_by_command(self):
        out = json.loads(self.call("erasure_propose", self.site.public_id, actor=self.staff.username))
        cancelled = json.loads(self.call(
            "erasure_approve", out["id"], actor=self.staff.username, reason="redo", cancel=True
        ))
        self.assertEqual(cancelled["status"], "cancelled")
        self.call("erasure_hold", self.site.public_id, actor=self.staff.username,
                  reason_code="fraud", reference="T-3")
        self.assertEqual(ErasureHold.objects.filter(released_at__isnull=True).count(), 1)
        self.call("erasure_hold", self.site.public_id, actor=self.staff.username, release=True)
        self.assertEqual(ErasureHold.objects.filter(released_at__isnull=True).count(), 0)

    def test_every_command_says_so_while_the_switch_is_off(self):
        with override_settings(SITE_ERASURE_ENABLED=False):
            for name, args, options in (
                ("erasure_propose", [self.site.public_id], {"actor": self.staff.username}),
                ("erasure_approve", ["ebt_x"], {"actor": self.staff.username, "reason": "r", "confirm": "x"}),
                ("erasure_hold", [self.site.public_id], {"actor": self.staff.username, "reason_code": "legal"}),
                ("erasure_run", ["ebt_x"], {}),
            ):
                with self.subTest(name=name):
                    with self.assertRaisesMessage(CommandError, "SITE_ERASURE_ENABLED is off"):
                        self.call(name, *args, **options)
        self.assertEqual(ErasureBatch.objects.count(), 0)

    def test_the_dry_run_still_works_while_the_switch_is_off(self):
        with override_settings(SITE_ERASURE_ENABLED=False):
            out = json.loads(self.call("erasure_dry_run", as_of="2026-12-30"))
        self.assertEqual(len(out["eligible"]), 1)
