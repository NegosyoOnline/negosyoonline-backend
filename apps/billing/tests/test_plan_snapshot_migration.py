"""Migrations 0003 and 0004 (Billing Build B3): the snapshot columns, the backfill, and the
consent record. The backfill is run on rows built as B2a wrote them."""
import importlib

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings

from apps.billing.models import BankTransferPayment, BillingCycle, RenewalConsent
from apps.billing.tests.base import CYCLE_1_END, T0, TRIAL_END, make_subscription

backfill = importlib.import_module("apps.billing.migrations.0004_plan_snapshot_backfill").backfill


@override_settings(BILLING_ENABLED=True)
class BackfillTests(TransactionTestCase):
    """Rows as B2a wrote them cannot exist once 0005 is applied, so these run with the schema
    at 0004 (0005 unapplied, which only drops the constraint) and the schema is put back."""

    def setUp(self):
        self.migrate("0004_plan_snapshot_backfill")
        self.addCleanup(self.migrate_forward)
        self.subscription = make_subscription()
        self.account = self.subscription.site.account

    def migrate_forward(self):
        self.run_backfill()  # a row left empty by a test would stop the constraint
        self.migrate(None)

    @staticmethod
    def migrate(name):
        executor = MigrationExecutor(connection)
        if name is None:
            target = executor.loader.graph.leaf_nodes("billing")
        else:
            target = [("billing", name)]
        executor.migrate(target)

    def cycle(self, sequence, settlement, **fields):
        return BillingCycle.objects.create(
            subscription=self.subscription, sequence=sequence, kind="paid",
            start_at=TRIAL_END, end_at=CYCLE_1_END, settlement=settlement, **fields,
        )

    def payment(self, plan_code, amount, cycle=None, status="submitted"):
        # The schema is at 0004: a payment is written through the model as that migration saw
        # it, because 0007 (B4) added columns the table does not have yet.
        state = MigrationExecutor(connection).loader.project_state(
            ("billing", "0004_plan_snapshot_backfill")
        )
        model = state.apps.get_model("billing", "BankTransferPayment")
        values = {"cycle_id": cycle.pk} if cycle is not None else {}
        row = model.objects.create(
            subscription_id=self.subscription.pk, plan_code=plan_code, amount_centavos=amount,
            status=status, transferred_on=T0.date(), proof_key="k", proof_content_type="image/png",
            proof_size=1, proof_sha256="0" * 64, submitted_by_id=self.account.pk,
            submitted_at=T0, **values,
        )
        return BankTransferPayment(pk=row.pk)

    def run_backfill(self):
        # As the migration saw the tables: 0007 (B4) added payment columns this schema lacks.
        state = MigrationExecutor(connection).loader.project_state(
            ("billing", "0004_plan_snapshot_backfill")
        )
        backfill(state.apps, None)

    def test_a_paid_cycle_takes_the_plan_of_its_payment(self):
        promo = self.cycle(1, "paid")
        self.payment("launch_promo", 499_900, cycle=promo)
        self.run_backfill()
        promo.refresh_from_db()
        self.assertEqual(
            (promo.plan_code, promo.plan_version, promo.price_centavos, promo.term_months),
            ("launch_promo", 1, 499_900, 6),
        )

    def test_a_pending_or_lapsed_cycle_takes_monthly(self):
        pending = self.cycle(1, "pending")
        self.run_backfill()
        pending.refresh_from_db()
        self.assertEqual(
            (pending.plan_code, pending.plan_version, pending.price_centavos, pending.term_months),
            ("monthly", 1, 249_900, 1),
        )

    def test_the_trial_cycle_stays_empty(self):
        trial = self.subscription.cycles.get(sequence=0)
        self.run_backfill()
        trial.refresh_from_db()
        self.assertEqual((trial.plan_code, trial.plan_version, trial.price_centavos), ("", None, None))

    def test_a_payment_takes_version_one_and_its_term(self):
        payment = self.payment("launch_promo", 499_900, status="rejected")
        self.run_backfill()
        payment.refresh_from_db(fields=["plan_version", "term_months"])
        self.assertEqual((payment.plan_version, payment.term_months), (1, 6))

    def test_a_second_run_changes_nothing(self):
        cycle = self.cycle(1, "pending")
        self.run_backfill()
        BillingCycle.objects.filter(pk=cycle.pk).update(price_centavos=1)
        self.run_backfill()
        cycle.refresh_from_db()
        self.assertEqual(cycle.price_centavos, 1)

    def test_the_constraint_then_refuses_a_paid_cycle_without_a_snapshot(self):
        self.migrate(None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.cycle(1, "pending")
        self.cycle(1, "pending", plan_code="monthly", plan_version=1, price_centavos=1, term_months=1)


@override_settings(BILLING_ENABLED=True)
class ConsentRecordTests(TestCase):
    def setUp(self):
        self.subscription = make_subscription()
        account = self.subscription.site.account
        self.payment = BankTransferPayment.objects.create(
            subscription=self.subscription, plan_code="launch_promo", amount_centavos=499_900,
            transferred_on=T0.date(), proof_key="k", proof_content_type="image/png",
            proof_size=1, proof_sha256="0" * 64, submitted_by=account, submitted_at=T0,
        )
        self.values = dict(
            subscription=self.subscription, payment=self.payment, plan_code="launch_promo",
            plan_version=1, then_plan_code="monthly", then_price_centavos=249_900,
            text_version="promo-consent-1", accepted_by=account, accepted_at=T0,
        )

    def test_a_consent_is_kept_and_not_changed(self):
        consent = RenewalConsent.objects.create(**self.values)
        with self.assertRaises(RuntimeError):
            consent.delete()
        consent.text_version = "other"
        with self.assertRaises(RuntimeError):
            consent.save()
        self.assertEqual(RenewalConsent.objects.get().text_version, "promo-consent-1")

    def test_a_payment_has_one_consent(self):
        RenewalConsent.objects.create(**self.values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RenewalConsent.objects.create(**self.values)
