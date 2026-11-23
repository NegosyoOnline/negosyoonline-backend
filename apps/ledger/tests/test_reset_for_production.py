"""The production reset empties a posted ledger entry, which the ledger's PostgreSQL triggers
refuse to delete one row at a time (the command truncates the tables instead)."""
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from apps.ledger.models import AccountingEntity, JournalEntry, JournalLine, LedgerEvent
from apps.ledger.tests.base import make_user, posted_entry


@override_settings(APP_ENVIRONMENT="production")
class ResetEmptiesPostedBooks(TestCase):
    def test_a_posted_entry_its_lines_and_the_history_are_emptied(self):
        make_user("coach", staff=True)
        entry = posted_entry()
        LedgerEvent.objects.create(entity=entry.entity, kind="test", target_type="t", target_ref="r")
        if connection.vendor == "postgresql":
            # The balance trigger is deferred to commit and a test never commits: fire it now.
            connection.cursor().execute("SET CONSTRAINTS ALL IMMEDIATE")
        self.assertEqual(JournalEntry.objects.filter(status="posted").count(), 1)

        call_command("billing_reset_for_production", "--apply", "--operator", "coach")

        for model in (AccountingEntity, JournalEntry, JournalLine, LedgerEvent):
            self.assertEqual(model._base_manager.count(), 0, model)
