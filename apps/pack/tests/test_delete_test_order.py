"""The named test-order deletion command (ClickUp z8uq9mfztm)."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.apps import apps as django_apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import ForeignKey, OneToOneField
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.tests.base import make_subscription
from apps.common import private_files
from apps.members.models import (
    ImmutableRecordError as MemberImmutable, Member, MemberAddressEvent, MemberContactAudit,
    MemberNotification, MemberOrderLog, MemberOtpChallenge, MemberRecoveryCheck, MemberSecurityHold,
    MemberSession,
)
from apps.meta_ads.models import MetaDelivery, MetaOrderContext
from apps.operators.models import OperatorAuditEntry
from apps.pack import records
from apps.pack.management.commands import pack_delete_test_order as command
from apps.pack.models import (
    CheckoutToken, ImmutableRecordError, PackAgreementAcceptance, PackBuyerNotice,
    PackEnrollmentAuthorization, PackOrder, PackOrderEvent, PackProofDecision, PackProofSubmission,
    PackRegisteredNotice, PackVisit, SanteRegistration, SanteRegistrationEvent,
)
from apps.pack.tests.owner_base import make_order
from apps.payments.tests.base import use_local_private_store
from apps.sante.tests.agreement_helpers import make_master, make_operator, published
from apps.shop.models import ShopOrder
from apps.shop.tests.owner_base import make_order as make_shop_order
from apps.training.models import Course, Lesson, LessonProgress, Module

STAGING = override_settings(
    RAILWAY_ENVIRONMENT_NAME="staging", PACK_ENABLED=True, MEMBERS_ENABLED=True, BILLING_ENABLED=True,
)

# Every model that points at a Pack order or a Member, directly or through what the command
# deletes, and who handles it. A new foreign key anywhere fails `test_every_reference_is_covered`.
HANDLED = {
    "pack.PackVisit", "pack.PackProofSubmission", "pack.PackProofDecision", "pack.PackOrderEvent",
    "pack.PackAgreementAcceptance", "pack.PackEnrollmentAuthorization", "pack.SanteRegistration",
    "meta_ads.MetaOrderContext", "pack.SanteRegistrationEvent", "pack.PackRegisteredNotice",
    "members.MemberSession", "members.MemberOtpChallenge", "members.MemberRecoveryCheck",
    "members.MemberContactAudit", "members.MemberAddressEvent", "members.MemberSecurityHold",
    "members.MemberNotification", "members.MemberOrderLog", "pack.PackOrder", "pack.PackBuyerNotice",
    "training.LessonProgress", "shop.ShopOrder",
}
APPEND_ONLY = [
    PackProofSubmission, PackProofDecision, PackOrderEvent, PackAgreementAcceptance,
    PackEnrollmentAuthorization, SanteRegistrationEvent,
]


def run(site, *orders, flags=(), actor=None):
    out = StringIO()
    args = ["--site", site.public_id]
    for order in orders:
        args += ["--order", order if isinstance(order, str) else order.order_number]
    call_command("pack_delete_test_order", *args, *flags, stdout=out)
    return out.getvalue()


def snapshot():
    """Every row count of every model the command may touch, for before and after."""
    models = [django_apps.get_model(label) for label in HANDLED | {"members.Member", "meta_ads.MetaDelivery"}]
    return {m._meta.label: m._base_manager.count() for m in models}


@STAGING
class DeleteTestOrderTests(TestCase):
    def setUp(self):
        use_local_private_store(self)
        self.operator = make_operator("op")
        make_master(self.operator)
        self.version = published(self.operator)
        self.site = make_subscription().site
        self.other_site = make_subscription(email="ben@example.test", name="bens-store").site
        self.order, self.proof = make_order(self.site)
        self.member = self.order.member
        self.fill(self.site, self.order)
        self.keep, self.keep_proof = make_order(self.site)  # a real order of the same site
        self.fill(self.site, self.keep)
        self.far, self.far_proof = make_order(self.other_site)  # another site's order
        self.fill(self.other_site, self.far)
        MetaDelivery.objects.create(site=self.site, event_name="Purchase", event_id=f"p_{self.order.public_id}")
        MetaDelivery.objects.create(site=self.site, event_name="Purchase", event_id=f"p_{self.keep.public_id}")

    def fill(self, site, order):
        """One row in every table that references the order or its member."""
        member, now = order.member, timezone.now()
        PackProofDecision.objects.create(
            site=site, order=order, submission=order.submissions.get(), decision="approved", actor_type="system",
        )
        PackOrderEvent.objects.create(
            site=site, order=order, kind="proof_submitted", actor_type="member",
        )
        records.write_acceptance(order, self.version, now)
        records.write_enrollment(order, now)
        reg = SanteRegistration.objects.create(
            site=site, member=member, pack_order=order, status="awaiting_buyer_details", status_entered_at=now,
        )
        SanteRegistrationEvent.objects.create(
            site=site, registration=reg, edge="T3", to_status="waiting_for_accept", actor_type="member",
        )
        PackRegisteredNotice.objects.create(site=site, registration=reg)
        CheckoutToken.objects.create(
            site=site, token_hash=f"{order.pk:064d}", expires_at=now, order_ref=order.pk,
            pending_contact={"email": "m@x.test"},
        )
        PackVisit.objects.create(site=site, visitor_ref=f"v{order.pk}".ljust(20, "x"), order=order)
        MetaOrderContext.objects.create(order=order, site=site, client_ip="203.0.113.9")
        MemberSession.objects.create(
            site=site, member=member, token_hash=f"{member.pk:064d}", expires_at=now + timedelta(days=1),
        )
        MemberOtpChallenge.objects.create(
            site=site, member=member, purpose="signin", channel="email", target="m@x.test",
            requested_contact="m@x.test", code_hash="c" * 64, binding_hash="b" * 64, expires_at=now,
        )
        check = MemberRecoveryCheck.objects.create(
            site=site, member=member, actor_account_id=1, actor_email_masked="m***@x.test", items={},
            passed=True, created_at=now, expires_at=now + timedelta(days=1),
        )
        audit = MemberContactAudit.objects.create(
            site=site, member=member, kind="registered", field="email", actor_type="member",
            recovery_check=check,
        )
        MemberSecurityHold.objects.create(
            site=site, member=member, kind="recovery", starts_at=now, ends_at=now + timedelta(days=1), audit=audit,
        )
        MemberAddressEvent.objects.create(site=site, member=member, old={}, new={"city": "Naga"})
        MemberNotification.objects.create(site=site, member=member, kind="notice", title="Hi")
        MemberOrderLog.objects.create(
            site=site, member=member, order_number="PH-1", ordered_on=now.date(), customer_first_name="Ana",
            city="Manila", payment="cod", total_centavos=10000, status="for_approval",
        )
        PackBuyerNotice.objects.create(site=site, member=member, deletion_due_at=now)
        course = Course.objects.create(site=site, title=f"C{order.pk}", slug=f"c{order.pk}")
        module = Module.objects.create(course=course, title="M")
        lesson = Lesson.objects.create(module=module, title="L")
        LessonProgress.objects.create(member=member, lesson=lesson)

    # -- the whole list is covered ------------------------------------------------------------

    def test_every_reference_is_covered(self):
        """A foreign key to a Pack order, a Member or a row deleted with them that is not in HANDLED
        fails here, so a new model cannot be forgotten."""
        seen = set()
        targets = {"pack.PackOrder", "members.Member"}
        for _ in range(3):  # follow the chain: order -> proof -> decision, registration -> event, audit -> hold
            for model in django_apps.get_models():
                for field in model._meta.get_fields():
                    if isinstance(field, (ForeignKey, OneToOneField)) and field.related_model._meta.label in targets:
                        seen.add(model._meta.label)
            targets = targets | (seen - {"shop.ShopOrder"})  # a shop order only keeps its member
        self.assertEqual(seen - HANDLED, set())

    def run_json(self, *flags):
        import json
        with self.captureOnCommitCallbacks(execute=True):
            out = run(self.site, self.order, flags=flags)
        return json.loads(out[out.index("{"):])

    # -- dry run and apply --------------------------------------------------------------------

    def test_dry_run_writes_nothing(self):
        before = snapshot()
        out = run(self.site, self.order)
        self.assertIn("Dry run", out)
        self.assertIn('"pack_orders": 1', out)
        self.assertIn('"members_deleted": 1', out)
        self.assertEqual(snapshot(), before)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="pack_test_order_deleted").count(), 0)
        self.assertTrue(private_files.store().get(self.proof.file_key))

    def test_apply_deletes_exactly_the_named_order_and_its_member(self):
        before = snapshot()
        with self.captureOnCommitCallbacks(execute=True):
            out = run(self.site, self.order, flags=["--apply"])
        self.assertIn("Applied", out)
        after = snapshot()
        # Exactly one order's worth is gone from every table: the order, its rows and its member.
        for label, count in before.items():
            gone = count - after[label]
            expected = 0 if label == "meta_ads.MetaDelivery" else (1 if label != "shop.ShopOrder" else 0)
            self.assertEqual(gone, expected, label)
        self.assertFalse(PackOrder.objects.filter(pk=self.order.pk).exists())
        self.assertFalse(Member.objects.filter(pk=self.member.pk).exists())
        # The same site's other order, the other site's order and all their rows are untouched.
        for order in (self.keep, self.far):
            self.assertTrue(PackOrder.objects.filter(pk=order.pk).exists())
            self.assertTrue(Member.objects.filter(pk=order.member_id).exists())
            self.assertEqual(order.submissions.count(), 1)
            self.assertEqual(MetaOrderContext.objects.filter(order=order).count(), 1)
            self.assertEqual(SanteRegistration.objects.filter(pack_order=order).count(), 1)
            self.assertEqual(MemberSession.objects.filter(member=order.member).count(), 1)
        # Meta delivery rows stay and are counted.
        self.assertEqual(MetaDelivery.objects.count(), 2)
        self.assertIn('"meta_delivery_rows_kept": 1', out)

    def test_apply_covers_every_related_table(self):
        counts = self.run_json("--apply")
        for key in (
            "pack_proof_decisions", "pack_proof_submissions", "pack_order_events", "pack_agreement_acceptances",
            "pack_enrollment_authorizations", "sante_registration_events", "pack_registered_notices",
            "sante_registrations", "checkout_tokens", "pack_visits", "meta_order_contexts",
            "member_security_holds", "member_contact_audits", "member_recovery_checks", "member_sessions",
            "member_otp_challenges", "member_notifications", "member_order_logs", "member_address_events",
            "pack_buyer_notices", "lesson_progress",
        ):
            self.assertEqual(counts[key], 1, key)
        self.assertEqual((counts["members_deleted"], counts["members_kept"]), (1, 0))

    # -- the member --------------------------------------------------------------------------

    def test_member_with_a_shop_order_is_kept(self):
        order, _ = make_shop_order(self.site, member=self.member)
        self.assertEqual(ShopOrder.objects.get(pk=order.pk).member_id, self.member.pk)
        self.run_apply()
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())
        self.assertTrue(ShopOrder.objects.filter(pk=order.pk).exists())

    def test_member_credited_on_a_shop_order_is_kept(self):
        order, _ = make_shop_order(self.site)
        ShopOrder.objects.filter(pk=order.pk).update(credited_member=self.member)
        self.run_apply()
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())

    def run_apply(self, *orders):
        with self.captureOnCommitCallbacks(execute=True):
            return run(self.site, *(orders or (self.order,)), flags=["--apply"])

    def test_two_named_orders_in_one_run(self):
        self.run_apply(self.order, self.keep.public_id)
        self.assertEqual(PackOrder.objects.filter(site=self.site).count(), 0)
        self.assertEqual(PackOrder.objects.filter(site=self.other_site).count(), 1)

    # -- files, audit, refusals --------------------------------------------------------------

    def test_files_are_deleted_only_after_the_commit(self):
        key = self.proof.file_key
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            run(self.site, self.order, flags=["--apply"])
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(private_files.store().get(key))
        callbacks[0]()
        with self.assertRaises(Exception):
            private_files.store().get(key)
        self.assertTrue(private_files.store().get(self.keep_proof.file_key))
        self.assertTrue(private_files.store().get(self.far_proof.file_key))

    def test_a_failed_file_deletion_logs_the_row_id_never_the_key(self):
        key = self.proof.file_key
        with mock.patch.object(private_files, "delete", side_effect=private_files.StoreError("boom")):
            with self.assertLogs(command.logger, level="ERROR") as logs:
                self.run_apply()
        text = "\n".join(logs.output)
        self.assertIn(str(self.proof.pk), text)
        self.assertNotIn(key, text)

    def test_dry_run_deletes_no_file_and_registers_no_callback(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            run(self.site, self.order)
        self.assertEqual(callbacks, [])

    def test_one_audit_entry_with_ids_and_counts_only(self):
        operator = make_operator("staffer")
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command(
                "pack_delete_test_order", "--site", self.site.public_id, "--order", self.order.order_number,
                "--apply", "--actor", "staffer", stdout=out,
            )
        entry = OperatorAuditEntry.objects.get(action="pack_test_order_deleted")
        self.assertEqual(entry.operator_id, operator.pk)
        self.assertEqual(entry.target_id, self.site.public_id)
        self.assertEqual(entry.detail["orders"], [self.order.public_id])
        self.assertEqual(entry.detail["members_deleted"], [self.member.public_id])
        self.assertEqual(entry.detail["counts"]["pack_proof_submissions"], 1)
        self.assertEqual(entry.personal, {})
        self.assertNotIn(self.member.email, str(entry.detail))
        self.assertNotIn(self.proof.file_key, str(entry.detail))

    def test_audit_entries_survive(self):
        self.run_apply()
        self.assertEqual(OperatorAuditEntry.objects.filter(action="pack_test_order_deleted").count(), 1)

    def test_running_it_again_refuses_cleanly(self):
        self.run_apply()
        before = snapshot()
        with self.assertRaisesMessage(CommandError, "No such order"):
            run(self.site, self.order, flags=["--apply"])
        self.assertEqual(snapshot(), before)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="pack_test_order_deleted").count(), 1)

    def test_unknown_or_other_site_order_refuses_all(self):
        before = snapshot()
        with self.assertRaisesMessage(CommandError, "No such order"):
            run(self.site, self.order, "SMP00000", flags=["--apply"])
        with self.assertRaisesMessage(CommandError, "not on that site"):
            run(self.site, self.order, self.far, flags=["--apply"])
        with self.assertRaisesMessage(CommandError, "not on that site"):
            run(self.site, self.far.public_id, flags=["--apply"])
        with self.assertRaisesMessage(CommandError, "No such site"):
            run(self.site.__class__(public_id="site_nope"), self.order, flags=["--apply"])
        self.assertEqual(snapshot(), before)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="pack_test_order_deleted").count(), 0)

    def test_a_public_id_names_an_order_too(self):
        self.run_apply(self.order.public_id)
        self.assertFalse(PackOrder.objects.filter(pk=self.order.pk).exists())

    def test_the_actor_must_be_staff(self):
        with self.assertRaises(CommandError):
            call_command(
                "pack_delete_test_order", "--site", self.site.public_id, "--order", self.order.order_number,
                "--actor", "nobody", stdout=StringIO(),
            )

    def test_an_order_is_required(self):
        with self.assertRaises(CommandError):
            call_command("pack_delete_test_order", "--site", self.site.public_id, stdout=StringIO())

    # -- the environment ---------------------------------------------------------------------

    def test_production_is_refused_writing_nothing(self):
        before = snapshot()
        for name, debug in (("production", False), ("production", True), ("", False), ("preview", False)):
            with override_settings(RAILWAY_ENVIRONMENT_NAME=name, DEBUG=debug):
                with self.assertRaisesMessage(CommandError, "staging and local only"):
                    run(self.site, self.order, flags=["--apply"])
        self.assertEqual(snapshot(), before)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="pack_test_order_deleted").count(), 0)

    def test_local_debug_and_staging_are_allowed(self):
        with override_settings(RAILWAY_ENVIRONMENT_NAME="", DEBUG=True):
            self.assertTrue(command.environment_allows())
        with override_settings(RAILWAY_ENVIRONMENT_NAME="Staging", DEBUG=False):
            self.assertTrue(command.environment_allows())

    # -- the append-only guards stay in force -------------------------------------------------

    def test_other_paths_still_refuse_for_every_append_only_model(self):
        for model in APPEND_ONLY:
            row = model._base_manager.filter(**({"order": self.keep} if hasattr(model, "order") else {
                "registration__pack_order": self.keep
            })).first()
            self.assertIsNotNone(row, model.__name__)
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                row.delete()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                model.objects.filter(pk=row.pk).delete()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                model.objects.all().delete()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                model.objects.filter(pk=row.pk).update(site=row.site)
            self.assertTrue(model._base_manager.filter(pk=row.pk).exists())

    def test_model_deletes_still_refuse_where_the_model_guards_them(self):
        guarded = [
            (PackOrder, ImmutableRecordError, self.keep),
            (SanteRegistration, ImmutableRecordError, SanteRegistration.objects.get(pack_order=self.keep)),
            (MemberRecoveryCheck, MemberImmutable, MemberRecoveryCheck.objects.get(member=self.keep.member)),
            (MemberSecurityHold, MemberImmutable, MemberSecurityHold.objects.get(member=self.keep.member)),
        ]
        for model, error, row in guarded:
            with self.assertRaises(error, msg=model.__name__):
                row.delete()
        self.run_apply()
        for model, _, row in guarded:
            self.assertTrue(model._base_manager.filter(pk=row.pk).exists(), model.__name__)
