"""The named shop test-order deletion command (ClickUp z8uq9mfzzn)."""
import json
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
from apps.members.models import Member, MemberNotification, MemberSession
from apps.meta_ads.models import MetaDelivery
from apps.operators.models import OperatorAuditEntry
from apps.pack.tests.owner_base import make_order as make_pack_order
from apps.pack.tests.test_delete_test_order import HANDLED as PACK_HANDLED
from apps.payments.tests.base import use_local_private_store
from apps.sante.tests.agreement_helpers import make_operator
from apps.shop.management.commands import shop_delete_test_order as command
from apps.shop.models import (
    ImmutableRecordError, ProductReview, ShopOrder, ShopOrderEvent, ShopOrderLine, ShopProofSubmission,
)
from apps.shop.tests.owner_base import make_order

STAGING = override_settings(
    RAILWAY_ENVIRONMENT_NAME="staging", SHOP_ENABLED=True, PAYMENT_METHODS_ENABLED=True,
    PACK_ENABLED=True, MEMBERS_ENABLED=True, BILLING_ENABLED=True,
)

# Every model that points at a shop order, directly or through what the command deletes, and who
# handles it. A new foreign key to any of them fails `test_every_reference_is_covered`.
HANDLED = {
    "shop.ShopOrderLine", "shop.ShopProofSubmission", "shop.ShopOrderEvent", "shop.ProductReview",
}
APPEND_ONLY = [ShopOrderLine, ShopProofSubmission, ShopOrderEvent]
COUNTED = HANDLED | {"shop.ShopOrder", "members.Member", "members.MemberSession", "members.MemberNotification",
                     "pack.PackOrder"}
ORDER_KEYS = ("shop_orders", "product_reviews", "shop_proof_submissions", "shop_order_events", "shop_order_lines")


def run(site, *orders, flags=()):
    out = StringIO()
    args = ["--site", site.public_id]
    for order in orders:
        args += ["--order", order if isinstance(order, str) else order.order_number]
    call_command("shop_delete_test_order", *args, *flags, stdout=out)
    return out.getvalue()


def snapshot():
    return {label: django_apps.get_model(label)._base_manager.count() for label in COUNTED}


def audit_count():
    return OperatorAuditEntry.objects.filter(action="shop_test_order_deleted").count()


@STAGING
class DeleteTestShopOrderTests(TestCase):
    def setUp(self):
        use_local_private_store(self)
        self.site = make_subscription().site
        self.other_site = make_subscription(email="ben@example.test", name="bens-store").site
        self.member = Member.objects.create(
            site=self.site, full_name="Maria Santos", mobile="+639189900001", email="shopper1@buyer.test",
            mobile_state="verified", email_state="verified",
        )
        self.order, self.proof = make_order(self.site, member=self.member, status="verified", shipped=True, delivered=True)
        self.review = ProductReview.objects.create(
            site=self.site, order=self.order, order_line=self.order.lines.get(), product_id="prod_x", rating=5,
            display_name="Maria S.",
        )
        now = timezone.now()
        MemberSession.objects.create(
            site=self.site, member=self.member, token_hash=f"{self.member.pk:064d}", expires_at=now + timedelta(days=1),
        )
        MemberNotification.objects.create(site=self.site, member=self.member, kind="notice", title="Hi")
        self.keep, self.keep_proof = make_order(self.site)  # a guest order of the same site
        self.far, self.far_proof = make_order(self.other_site)  # another site's order
        MetaDelivery.objects.create(site=self.site, event_name="Purchase", event_id=f"p_{self.keep.public_id}")

    def run_apply(self, *orders):
        with self.captureOnCommitCallbacks(execute=True):
            return run(self.site, *(orders or (self.order,)), flags=["--apply"])

    def counts(self, *flags):
        with self.captureOnCommitCallbacks(execute=True):
            out = run(self.site, self.order, flags=flags)
        return json.loads(out[out.index("{"):])

    # -- the whole list is covered ------------------------------------------------------------

    def test_every_reference_is_covered(self):
        """A foreign key to a shop order, or to a row the command deletes with it, that is not in
        HANDLED fails here; so does a new foreign key to a Member that the pack command's member
        cleanup does not know."""
        seen, targets = set(), {"shop.ShopOrder"}
        for _ in range(3):  # order -> line -> review
            for model in django_apps.get_models():
                for field in model._meta.get_fields():
                    if isinstance(field, (ForeignKey, OneToOneField)) and field.related_model._meta.label in targets:
                        seen.add(model._meta.label)
            targets = targets | seen
        self.assertEqual(seen - HANDLED, set())
        member_side = {
            model._meta.label for model in django_apps.get_models() for field in model._meta.get_fields()
            if isinstance(field, (ForeignKey, OneToOneField)) and field.related_model._meta.label == "members.Member"
        }
        self.assertEqual(member_side - PACK_HANDLED, set())

    # -- dry run and apply --------------------------------------------------------------------

    def test_dry_run_writes_nothing(self):
        before = snapshot()
        out = run(self.site, self.order)
        self.assertIn("Dry run", out)
        self.assertIn('"shop_orders": 1', out)
        self.assertIn('"members_deleted": 1', out)
        self.assertEqual(snapshot(), before)
        self.assertEqual(audit_count(), 0)
        self.assertTrue(private_files.store().get(self.proof.file_key))

    def test_apply_deletes_exactly_the_named_order_and_its_member(self):
        before = snapshot()
        self.run_apply()
        after = snapshot()
        gone = {label: before[label] - after[label] for label in before}
        self.assertEqual(
            gone,
            {"shop.ShopOrder": 1, "shop.ShopOrderLine": 1, "shop.ShopProofSubmission": 1, "shop.ShopOrderEvent": 1,
             "shop.ProductReview": 1, "members.Member": 1, "members.MemberSession": 1,
             "members.MemberNotification": 1, "pack.PackOrder": 0},
        )
        self.assertFalse(ShopOrder.objects.filter(pk=self.order.pk).exists())
        self.assertFalse(Member.objects.filter(pk=self.member.pk).exists())
        for order in (self.keep, self.far):
            self.assertTrue(ShopOrder.objects.filter(pk=order.pk).exists())
            self.assertEqual(order.lines.count(), 1)
            self.assertEqual(order.events.count(), 1)
            self.assertTrue(ShopProofSubmission.objects.filter(order=order).exists())
        self.assertEqual(MetaDelivery.objects.count(), 1)

    def test_apply_counts_every_related_table(self):
        counts = self.counts("--apply")
        for key in ORDER_KEYS:
            self.assertEqual(counts[key], 1, key)
        self.assertEqual((counts["members_deleted"], counts["members_kept"]), (1, 0))
        self.assertEqual(counts["proof_files_to_delete"], 1)

    def test_guest_order_has_no_member(self):
        self.run_apply(self.keep)
        self.assertFalse(ShopOrder.objects.filter(pk=self.keep.pk).exists())
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())
        entry = OperatorAuditEntry.objects.get(action="shop_test_order_deleted")
        self.assertEqual(entry.detail["members_deleted"], [])

    # -- the member --------------------------------------------------------------------------

    def test_member_with_another_shop_order_is_kept(self):
        other, _ = make_order(self.site, member=self.member)
        out = self.run_apply()
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())
        self.assertTrue(ShopOrder.objects.filter(pk=other.pk).exists())
        self.assertIn(f"Member {self.member.public_id} is kept", out)

    def test_member_credited_on_another_shop_order_is_kept(self):
        other, _ = make_order(self.site)
        ShopOrder.objects.filter(pk=other.pk).update(credited_member=self.member)
        self.run_apply()
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())

    def test_member_with_a_pack_order_is_kept(self):
        pack_order, _ = make_pack_order(self.site)
        pack_order.member = self.member
        pack_order.save()
        self.run_apply()
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())

    def test_credited_member_goes_when_unused(self):
        credited = Member.objects.create(
            site=self.site, full_name="Ana Cruz", mobile="+639189900002", email="shopper2@buyer.test",
            mobile_state="verified", email_state="verified",
        )
        ShopOrder.objects.filter(pk=self.order.pk).update(credited_member=credited)
        self.run_apply()
        self.assertFalse(Member.objects.filter(pk=credited.pk).exists())
        self.assertFalse(Member.objects.filter(pk=self.member.pk).exists())

    def test_two_named_orders_in_one_run(self):
        self.run_apply(self.order, self.keep.public_id)
        self.assertEqual(ShopOrder.objects.filter(site=self.site).count(), 0)
        self.assertEqual(ShopOrder.objects.filter(site=self.other_site).count(), 1)

    def test_a_public_id_names_an_order_too(self):
        self.run_apply(self.order.public_id)
        self.assertFalse(ShopOrder.objects.filter(pk=self.order.pk).exists())

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

    def test_dry_run_registers_no_callback(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            run(self.site, self.order)
        self.assertEqual(callbacks, [])

    def test_one_audit_entry_with_ids_and_counts_only(self):
        operator = make_operator("staffer")
        with self.captureOnCommitCallbacks(execute=True):
            call_command(
                "shop_delete_test_order", "--site", self.site.public_id, "--order", self.order.order_number,
                "--apply", "--actor", "staffer", stdout=StringIO(),
            )
        entry = OperatorAuditEntry.objects.get(action="shop_test_order_deleted")
        self.assertEqual(entry.operator_id, operator.pk)
        self.assertEqual(entry.target_id, self.site.public_id)
        self.assertEqual(entry.detail["orders"], [self.order.public_id])
        self.assertEqual(entry.detail["members_deleted"], [self.member.public_id])
        self.assertEqual(entry.detail["counts"]["shop_proof_submissions"], 1)
        self.assertEqual(entry.personal, {})
        detail = str(entry.detail)
        for secret in (self.member.email, self.order.contact_email, self.proof.file_key, self.order.receiver_name):
            self.assertNotIn(secret, detail)

    def test_running_it_again_refuses_cleanly(self):
        self.run_apply()
        before = snapshot()
        with self.assertRaisesMessage(CommandError, "No such order"):
            run(self.site, self.order, flags=["--apply"])
        self.assertEqual(snapshot(), before)
        self.assertEqual(audit_count(), 1)

    def test_unknown_or_other_site_order_refuses_all(self):
        before = snapshot()
        with self.assertRaisesMessage(CommandError, "No such order"):
            run(self.site, self.order, "SO00000", flags=["--apply"])
        with self.assertRaisesMessage(CommandError, "not on that site"):
            run(self.site, self.order, self.far.public_id, flags=["--apply"])
        with self.assertRaisesMessage(CommandError, "not on that site"):
            run(self.site, self.far.public_id, flags=["--apply"])
        with self.assertRaisesMessage(CommandError, "No such site"):
            run(self.site.__class__(public_id="site_nope"), self.order, flags=["--apply"])
        self.assertEqual(snapshot(), before)
        self.assertEqual(audit_count(), 0)

    def test_the_actor_must_be_staff(self):
        with self.assertRaises(CommandError):
            call_command(
                "shop_delete_test_order", "--site", self.site.public_id, "--order", self.order.order_number,
                "--actor", "nobody", stdout=StringIO(),
            )

    def test_an_order_is_required(self):
        with self.assertRaises(CommandError):
            call_command("shop_delete_test_order", "--site", self.site.public_id, stdout=StringIO())

    # -- the environment ---------------------------------------------------------------------

    def test_production_is_refused_before_any_database_read(self):
        before = snapshot()
        for name, debug in (("production", False), ("production", True), ("", False), ("preview", False)):
            with override_settings(RAILWAY_ENVIRONMENT_NAME=name, DEBUG=debug):
                with self.assertNumQueries(0):
                    with self.assertRaisesMessage(CommandError, "staging and local only"):
                        run(self.site, self.order, flags=["--apply"])
        self.assertEqual(snapshot(), before)
        self.assertEqual(audit_count(), 0)

    def test_local_debug_and_staging_are_allowed(self):
        with override_settings(RAILWAY_ENVIRONMENT_NAME="", DEBUG=True):
            self.assertTrue(command.environment_allows())
        with override_settings(RAILWAY_ENVIRONMENT_NAME="Staging", DEBUG=False):
            self.assertTrue(command.environment_allows())

    # -- the guards stay in force --------------------------------------------------------------

    def test_other_paths_still_refuse_for_every_append_only_model(self):
        for model in APPEND_ONLY:
            row = model._base_manager.filter(order=self.keep).first()
            self.assertIsNotNone(row, model.__name__)
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                row.delete()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                model.objects.filter(pk=row.pk).delete()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                model.objects.all().delete()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                model.objects.filter(pk=row.pk).update(order=row.order)
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                row.save()
            self.assertTrue(model._base_manager.filter(pk=row.pk).exists())

    def test_model_deletes_still_refuse_for_orders_and_reviews(self):
        for row in (self.keep, self.review):
            with self.assertRaises(ImmutableRecordError, msg=type(row).__name__):
                row.delete()
        self.run_apply()
        self.assertTrue(ShopOrder.objects.filter(pk=self.keep.pk).exists())
        self.assertFalse(ProductReview.objects.filter(pk=self.review.pk).exists())
        with self.assertRaises(ImmutableRecordError):
            self.keep.delete()
