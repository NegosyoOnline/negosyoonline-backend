"""A refused edit or delete of an issued statement, its lines or an invoice item leaves one
operator audit row (ClickUp z8uq9mevfx). The refusal itself is unchanged: `test_guards` holds it."""
from unittest import mock

from django.db import transaction
from django.test import TestCase, override_settings

from apps.invoices.models import InvoiceEvent, ManualInvoiceItem
from apps.operators.models import OperatorAuditEntry
from apps.statements.managers import Immutable, acting_operator
from apps.statements.models import Statement, StatementLine
from apps.statements.tests.base import make_item, make_line, make_staff, make_statement

EDIT, DELETE = "billing_record_edit_refused", "billing_record_delete_refused"


def rows(action):
    return list(OperatorAuditEntry.objects.filter(action=action).order_by("pk"))


@override_settings(BILLING_ENABLED=True)
class RefusalAuditTests(TestCase):
    def setUp(self):
        self.statement = make_statement()
        self.line = make_line(self.statement)
        self.item = make_item()

    def one(self, action, target_type, target_id):
        found = rows(action)
        self.assertEqual(len(found), 1, found)
        self.assertEqual((found[0].target_type, found[0].target_id), (target_type, target_id))
        self.assertIsNone(found[0].operator)  # a raw model call has no actor
        self.assertEqual(found[0].personal, {})
        return found[0]

    def test_statement_save(self):
        self.statement.total_centavos = 1
        with self.assertRaises(Immutable):
            self.statement.save()
        self.one(EDIT, "statement", self.statement.public_id)

    def test_statement_delete(self):
        with self.assertRaises(Immutable):
            self.statement.delete()
        self.one(DELETE, "statement", self.statement.public_id)

    def test_statement_queryset_update_delete_bulk_update(self):
        with self.assertRaises(Immutable):
            Statement.objects.update(total_centavos=1)
        entry = self.one(EDIT, "statement", self.statement.public_id)
        self.assertEqual(entry.detail, {"count": 1})
        with self.assertRaises(Immutable):
            Statement.objects.bulk_update([self.statement], ["total_centavos"])
        self.assertEqual(len(rows(EDIT)), 2)
        with self.assertRaises(Immutable):
            Statement.objects.all().delete()
        self.one(DELETE, "statement", self.statement.public_id)

    def test_statement_bulk_create_with_conflict_update(self):
        with self.assertRaises(Immutable):
            Statement.objects.bulk_create(
                [self.statement], update_conflicts=True, update_fields=["total_centavos"],
                unique_fields=["pk"],
            )
        self.one(EDIT, "statement", self.statement.public_id)

    def test_line_paths_are_audited_against_the_statement(self):
        self.line.amount_centavos = 1
        with self.assertRaises(Immutable):
            self.line.save()
        self.assertEqual(len(rows(EDIT)), 1)
        with self.assertRaises(Immutable):
            self.line.delete()
        self.one(DELETE, "statement", self.statement.public_id)
        with self.assertRaises(Immutable):
            StatementLine.objects.update(amount_centavos=1)
        self.assertEqual(len(rows(EDIT)), 2)
        with self.assertRaises(Immutable):
            StatementLine.objects.all().delete()
        self.assertEqual(len(rows(DELETE)), 2)
        self.assertTrue(all(r.target_id == self.statement.public_id for r in rows(EDIT) + rows(DELETE)))

    def test_item_frozen_field_names_the_field_only(self):
        self.item.total_centavos = 1
        with self.assertRaises(Immutable):
            self.item.save()
        entry = self.one(EDIT, "invoice_item", self.item.public_id)
        self.assertEqual(entry.detail, {"field": "total_centavos", "count": 1})

    def test_item_workflow_refusal(self):
        self.item.status = "verified"  # pending cannot jump to verified
        with self.assertRaises(Immutable):
            self.item.save()
        entry = self.one(EDIT, "invoice_item", self.item.public_id)
        self.assertEqual(entry.detail["field"], "workflow")

    def test_item_delete_and_queryset_paths(self):
        with self.assertRaises(Immutable):
            self.item.delete()
        self.one(DELETE, "invoice_item", self.item.public_id)
        with self.assertRaises(Immutable):
            ManualInvoiceItem.objects.update(total_centavos=1)
        with self.assertRaises(Immutable):
            ManualInvoiceItem.objects.bulk_update([self.item], ["total_centavos"])
        with self.assertRaises(Immutable):
            ManualInvoiceItem.objects.bulk_create(
                [self.item], update_conflicts=True, update_fields=["total_centavos"],
                unique_fields=["pk"],
            )
        self.assertEqual(len(rows(EDIT)), 3)
        with self.assertRaises(Immutable):
            ManualInvoiceItem.objects.all().delete()
        self.assertEqual(len(rows(DELETE)), 2)

    def test_an_allowed_move_writes_nothing(self):
        self.item.status = "recorded"
        self.item.save()
        self.assertEqual(OperatorAuditEntry.objects.filter(action__in=(EDIT, DELETE)).count(), 0)

    def test_a_record_that_does_not_opt_in_is_not_audited(self):
        event = InvoiceEvent.objects.create(
            item=self.item, code="opened", idempotency_key="k1", recorded_at=self.item.created_at,
        )
        with self.assertRaises(Immutable):
            event.delete()
        self.assertEqual(OperatorAuditEntry.objects.filter(action__in=(EDIT, DELETE)).count(), 0)

    def test_the_staff_actor_is_recorded_when_a_route_set_one(self):
        staff = make_staff("actor")
        with acting_operator(staff):
            with self.assertRaises(Immutable):
                self.statement.delete()
        self.assertEqual(rows(DELETE)[0].operator, staff)
        with self.assertRaises(Immutable):  # the context ended: no actor again
            self.statement.delete()
        self.assertIsNone(rows(DELETE)[1].operator)

    def test_a_failing_audit_never_lifts_the_refusal(self):
        with mock.patch("apps.operators.audit.record", side_effect=RuntimeError("down")):
            with self.assertRaises(Immutable):
                self.statement.delete()
        self.assertEqual(Statement.objects.filter(pk=self.statement.pk).count(), 1)
        self.assertEqual(rows(DELETE), [])

    def test_a_caught_refusal_keeps_its_row_but_a_rolled_back_one_does_not(self):
        with transaction.atomic():
            try:
                self.statement.delete()
            except Immutable:
                pass
        self.assertEqual(len(rows(DELETE)), 1)
        try:
            with transaction.atomic():
                self.statement.delete()  # the refusal propagates and rolls the transaction back
        except Immutable:
            pass
        self.assertEqual(len(rows(DELETE)), 1)  # documented limit: that attempt's row rolled back
