from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.statements.managers import Immutable
from apps.statements.models import IssuerProfile, Statement, StatementLine
from apps.statements.tests.base import WHEN, make_issuer, make_line, make_staff, make_statement


@override_settings(BILLING_ENABLED=True)
class StatementGuardTests(TestCase):
    def setUp(self):
        self.statement = make_statement()
        self.line = make_line(self.statement)

    def test_save_of_existing_row_refused(self):
        self.statement.total_centavos = 1
        with self.assertRaises(Immutable):
            self.statement.save()
        self.line.amount_centavos = 1
        with self.assertRaises(Immutable):
            self.line.save()

    def test_delete_refused_on_model_and_queryset(self):
        for obj in (self.statement, self.line):
            with self.assertRaises(Immutable):
                obj.delete()
        with self.assertRaises(Immutable):
            Statement.objects.all().delete()
        with self.assertRaises(Immutable):
            StatementLine.objects.all().delete()

    def test_bulk_changes_refused(self):
        with self.assertRaises(Immutable):
            Statement.objects.update(total_centavos=1)
        with self.assertRaises(Immutable):
            Statement.objects.bulk_update([self.statement], ["total_centavos"])
        with self.assertRaises(Immutable):
            StatementLine.objects.bulk_create([self.line], update_conflicts=True,
                                              update_fields=["amount_centavos"], unique_fields=["pk"])

    def test_one_statement_per_source_key(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_statement(self.statement.subscription, key=self.statement.source_key)

    def test_one_statement_per_payment_even_with_usage_only_and_no_cycle(self):
        sub = self.statement.subscription
        usage = make_statement(sub, key="payment:btp_u", kind="usage_only", cycle=None)
        self.assertIsNone(usage.cycle)
        with self.assertRaises(IntegrityError), transaction.atomic():
            # The same payment cannot get a second statement, whatever its kind.
            make_statement(sub, key="payment:btp_u", kind="final_usage", cycle=None)

    def test_term_statement_needs_cycle_and_adjustment_needs_original(self):
        sub = self.statement.subscription
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_statement(sub, kind="annual", cycle=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_statement(sub, document_type="adjustment", total_centavos=-1)

    def test_total_sign(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_statement(self.statement.subscription, total_centavos=-1)

    def test_line_position_once(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_line(self.statement, position=1)


@override_settings(BILLING_ENABLED=True)
class IssuerGuardTests(TestCase):
    def test_verified_once_then_frozen(self):
        issuer = make_issuer()
        issuer.verified_at, issuer.verified_by = WHEN, make_staff("v")
        issuer.save()
        issuer.verification_note = "again"
        with self.assertRaises(Immutable):
            issuer.save()

    def test_identity_fields_frozen(self):
        issuer = make_issuer()
        issuer.legal_name = "Other"
        with self.assertRaises(Immutable):
            issuer.save()

    def test_verified_needs_verifier(self):
        issuer = make_issuer()
        issuer.verified_at = WHEN
        with self.assertRaises(IntegrityError), transaction.atomic():
            issuer.save()

    def test_delete_and_bulk_refused(self):
        issuer = make_issuer()
        with self.assertRaises(Immutable):
            issuer.delete()
        with self.assertRaises(Immutable):
            IssuerProfile.objects.update(legal_name="x")
