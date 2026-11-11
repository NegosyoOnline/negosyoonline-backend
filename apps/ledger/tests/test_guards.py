from django.test import TestCase

from apps.common.ids import is_public_id
from apps.ledger.managers import LedgerRefusal
from apps.ledger.models import (
    AccountingEntity,
    FiscalPeriod,
    GLAccount,
    JournalEntry,
    JournalLine,
    LedgerEvent,
    RecognitionSchedule,
)
from apps.ledger.tests.base import (
    force_post,
    make_account,
    make_draft,
    make_entity,
    make_period,
    make_user,
    posted_entry,
)


class PostedEntryTests(TestCase):
    def setUp(self):
        self.entry = posted_entry()
        self.entity = self.entry.entity
        self.draft = make_draft(self.entity, "2026-01-20")

    def test_save_delete_raise(self):
        self.entry.memo = "changed"
        with self.assertRaises(LedgerRefusal):
            self.entry.save()
        with self.assertRaises(LedgerRefusal):
            self.entry.delete()

    def test_reloaded_posted_entry_still_refuses(self):
        loaded = JournalEntry.objects.get(pk=self.entry.pk)
        with self.assertRaises(LedgerRefusal):
            loaded.save()
        with self.assertRaises(LedgerRefusal):
            loaded.delete()

    def test_queryset_update_bulk_update_delete_raise_when_it_holds_a_posted_row(self):
        qs = JournalEntry.objects.filter(entity=self.entity)
        with self.assertRaises(LedgerRefusal):
            qs.update(memo="x")
        with self.assertRaises(LedgerRefusal):
            qs.delete()
        self.entry.memo = "x"
        with self.assertRaises(LedgerRefusal):
            JournalEntry.objects.bulk_update([self.entry], ["memo"])

    def test_queryset_calls_on_drafts_pass(self):
        drafts = JournalEntry.objects.filter(status="draft")
        self.assertEqual(drafts.update(memo="x"), 1)
        self.draft.refresh_from_db()
        self.draft.memo = "y"
        JournalEntry.objects.bulk_update([self.draft], ["memo"])
        drafts.delete()
        self.assertFalse(JournalEntry.objects.filter(pk=self.draft.pk).exists())

    def test_insert_as_posted_raises_through_create_and_bulk_create(self):
        kwargs = dict(entity=self.entity, entry_date="2026-01-21", reason="t", status="posted")
        with self.assertRaises(LedgerRefusal):
            JournalEntry.objects.create(idempotency_key="a", **kwargs)
        with self.assertRaises(LedgerRefusal):
            JournalEntry.objects.bulk_create([JournalEntry(idempotency_key="b", **kwargs)])


class DraftEntryTests(TestCase):
    def test_draft_save_passes_and_delete_removes_lines(self):
        entity = make_entity()
        bank, rev = make_account(entity, "1020"), make_account(entity, "4010", "revenue")
        draft = make_draft(entity, "2026-01-15", [(bank, 5, 0), (rev, 0, 5)])
        draft.memo = "edited"
        draft.save()
        draft.delete()
        self.assertEqual(JournalLine.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.count(), 0)


class LineTests(TestCase):
    def test_lines_of_a_posted_entry_refuse_every_change(self):
        entry = posted_entry()
        line = entry.lines.first()
        line.memo = "x"
        with self.assertRaises(LedgerRefusal):
            line.save()
        with self.assertRaises(LedgerRefusal):
            line.delete()
        with self.assertRaises(LedgerRefusal):
            JournalLine.objects.filter(entry=entry).update(memo="x")
        with self.assertRaises(LedgerRefusal):
            JournalLine.objects.filter(entry=entry).delete()
        with self.assertRaises(LedgerRefusal):
            JournalLine.objects.bulk_update([line], ["memo"])
        account = make_account(entry.entity, "5010", "expense")
        with self.assertRaises(LedgerRefusal):
            JournalLine.objects.bulk_create(
                [JournalLine(entry=entry, line_no=9, account=account, debit_centavos=1)]
            )
        with self.assertRaises(LedgerRefusal):
            JournalLine.objects.create(entry=entry, line_no=9, account=account, debit_centavos=1)

    def test_lines_of_a_draft_pass(self):
        entity = make_entity()
        bank = make_account(entity, "1020")
        draft = make_draft(entity, "2026-01-15", [(bank, 5, 0)])
        line = draft.lines.get()
        line.memo = "x"
        line.save()
        JournalLine.objects.filter(entry=draft).update(memo="y")
        JournalLine.objects.bulk_create([JournalLine(entry=draft, line_no=2, account=bank, credit_centavos=5)])
        JournalLine.objects.filter(entry=draft, line_no=2).delete()
        line.delete()


class AppendOnlyTests(TestCase):
    def setUp(self):
        self.entry = posted_entry()
        self.entity = self.entry.entity
        self.event = LedgerEvent.objects.create(
            entity=self.entity, kind="test", target_type="entry", target_ref="x"
        )
        self.recognition = RecognitionSchedule.objects.create(
            entity=self.entity, payment_ref="p1", period_month="2026-01",
            service_start="2026-01-01", service_end="2026-02-01",
            amount_to_release_centavos=10, payment_entry=self.entry,
        )

    def check(self, row):
        manager = type(row).objects
        with self.assertRaises(LedgerRefusal):
            row.save()
        with self.assertRaises(LedgerRefusal):
            row.delete()
        with self.assertRaises(LedgerRefusal):
            manager.all().update(entity=self.entity)
        with self.assertRaises(LedgerRefusal):
            manager.bulk_update([row], ["entity"])
        with self.assertRaises(LedgerRefusal):
            manager.all().delete()

    def test_event_is_append_only(self):
        self.check(self.event)
        LedgerEvent.objects.create(entity=self.entity, kind="again", target_type="entry", target_ref="y")
        LedgerEvent.objects.bulk_create(
            [LedgerEvent(entity=self.entity, kind="bulk", target_type="entry", target_ref="z")]
        )
        self.assertEqual(LedgerEvent.objects.count(), 3)

    def test_recognition_is_append_only(self):
        self.check(self.recognition)


class NeverDeletedTests(TestCase):
    def test_entity_account_period_refuse_delete(self):
        entity = make_entity()
        account = make_account(entity, "1020")
        period = make_period(entity)
        for row, model in ((entity, AccountingEntity), (account, GLAccount), (period, FiscalPeriod)):
            with self.assertRaises(LedgerRefusal):
                row.delete()
            with self.assertRaises(LedgerRefusal):
                model.objects.all().delete()

    def test_entity_kind_cannot_change_but_name_can(self):
        entity = make_entity()
        entity = AccountingEntity.objects.get(pk=entity.pk)
        entity.name = "Renamed"
        entity.save()
        entity.kind = "live"
        with self.assertRaises(LedgerRefusal):
            entity.save()


class PublicIdTests(TestCase):
    def test_prefixes(self):
        entity = make_entity()
        account = make_account(entity, "1020")
        period = make_period(entity)
        entry = make_draft(entity, "2026-01-15")
        event = LedgerEvent.objects.create(entity=entity, kind="k", target_type="t", target_ref="r")
        for value, prefix in (
            (entity.public_id, "ent"), (account.public_id, "gla"), (period.public_id, "per"),
            (entry.public_id, "jrn"), (event.public_id, "lev"),
        ):
            self.assertTrue(is_public_id(value, prefix), value)
