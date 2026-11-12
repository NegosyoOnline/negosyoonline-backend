from datetime import date
from unittest import skipUnless

from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction
from django.test import TestCase, TransactionTestCase

from apps.ledger.models import GLAccount, JournalEntry, JournalLine, LedgerEvent
from apps.ledger.tests.base import make_account, make_draft, make_entity, make_period, posted_entry

on_postgres = skipUnless(connection.vendor == "postgresql", "PostgreSQL triggers")

POSTED = dict(posting_seq=1, posted_at="2026-01-15T00:00:00Z", seal="s" * 64)


class Refuses(TestCase):
    def refused(self, text, call, *args, **kwargs):
        with self.assertRaises(IntegrityError) as caught, transaction.atomic():
            call(*args, **kwargs)
        self.assertIn(text, str(caught.exception))


def write(model, pk):
    """The row's queryset on the plain base manager. The Django guards are not under test here;
    the triggers are, so the change goes to the database directly."""
    return model._base_manager.filter(pk=pk)


@on_postgres
class EntryTriggerTests(Refuses):
    def setUp(self):
        self.entity = make_entity()
        self.period = make_period(self.entity)
        self.bank = make_account(self.entity, "1020", "asset")
        self.rev = make_account(self.entity, "4010", "revenue")

    def draft(self, on="2026-01-15"):
        return make_draft(self.entity, on, [(self.bank, 100, 0), (self.rev, 0, 100)])

    def post(self, entry, **changes):
        fields = dict(status="posted", period=self.period, total_centavos=100, **POSTED)
        fields.update(changes)
        write(JournalEntry, entry.pk).update(**fields)

    def test_posted_entry_cannot_be_updated_or_deleted(self):
        entry = posted_entry()
        self.refused("a posted entry cannot be changed", write(JournalEntry, entry.pk).update, memo="x")
        self.refused("a posted entry cannot be deleted", self.raw_delete, entry.pk)

    def raw_delete(self, pk):
        # Straight SQL: a queryset delete would remove the lines first and meet the line trigger.
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM ledger_entry WHERE id = %s", [pk])

    def test_a_valid_post_passes(self):
        entry = self.draft()
        self.post(entry)
        self.assertEqual(JournalEntry.objects.get(pk=entry.pk).status, "posted")

    def test_post_needs_period_sequence_posted_at_and_seal(self):
        for change in ({"period": None}, {"posting_seq": None}, {"posted_at": None}, {"seal": ""}):
            with self.subTest(change=change):
                self.refused("a posted entry needs", self.post, self.draft(), **change)

    def test_date_outside_the_period(self):
        self.refused("the entry date is outside the period", self.post, self.draft("2026-02-03"))

    def test_period_of_another_book(self):
        other = make_period(make_entity())
        self.refused("the period belongs to another book", self.post, self.draft(), period=other)

    def test_locked_period_refuses_and_soft_closed_passes(self):
        locked = make_period(self.entity, 2026, 2, "locked")
        soft = make_period(self.entity, 2026, 3, "soft_closed")
        self.refused("the period is locked", self.post, self.draft("2026-02-10"), period=locked)
        entry = self.draft("2026-03-10")
        self.post(entry, period=soft)  # Deviation 2: the writer's rule, not the trigger's
        self.assertEqual(JournalEntry.objects.get(pk=entry.pk).status, "posted")

    def test_books_start(self):
        write(type(self.entity), self.entity.pk).update(books_start_date=None)
        self.refused("before the books start", self.post, self.draft())
        write(type(self.entity), self.entity.pk).update(books_start_date=date(2026, 1, 20))
        self.refused("before the books start", self.post, self.draft())

    def test_draft_update_and_delete_pass(self):
        entry = self.draft()
        write(JournalEntry, entry.pk).update(memo="x")
        entry.refresh_from_db()
        entry.delete()
        self.assertEqual(JournalLine.objects.count(), 0)


@on_postgres
class LineTriggerTests(Refuses):
    def setUp(self):
        self.entity = make_entity()
        self.bank = make_account(self.entity, "1020", "asset")
        self.draft = make_draft(self.entity, "2026-01-15")

    def add(self, account=None, entry=None, line_no=1, **fields):
        base = dict(entry=entry or self.draft, line_no=line_no, account=account or self.bank, debit_centavos=5)
        base.update(fields)
        line = JournalLine(**base)
        models.Model.save(line, force_insert=True)  # past the Django guard, to the trigger
        return line

    def test_posted_entry_lines_are_frozen(self):
        entry = posted_entry()
        line = entry.lines.first()
        self.refused("a line cannot be added to a posted entry", self.add, entry=entry, line_no=9)
        self.refused("cannot be changed", write(JournalLine, line.pk).update, memo="x")
        self.refused("cannot be changed", write(JournalLine, line.pk).delete)

    def test_header_account_takes_no_line(self):
        header = make_account(self.entity, "1000", "asset", is_postable=False)
        self.refused("a header account takes no line", self.add, account=header)

    def test_another_books_account(self):
        foreign = make_account(make_entity(), "1020", "asset")
        self.refused("belongs to another book", self.add, account=foreign)

    def test_effective_dates(self):
        early = make_account(self.entity, "1", "asset", effective_from=date(2026, 2, 1))
        late = make_account(self.entity, "2", "asset", effective_to=date(2026, 1, 10))
        self.refused("effective dates", self.add, account=early)
        self.refused("effective dates", self.add, account=late)
        within = make_account(
            self.entity, "3", "asset", effective_from=date(2026, 1, 1), effective_to=date(2026, 1, 31)
        )
        self.add(account=within)

    def test_opening_entry_lines_need_evidence(self):
        opening = make_draft(self.entity, "2026-01-01", kind="opening", reason="opening")
        self.refused("evidence reference", self.add, entry=opening, evidence_ref="  ")
        self.add(entry=opening, evidence_ref="bank statement")
        self.add(entry=opening, line_no=2, account=make_account(self.entity, "3010", "equity"),
                 debit_centavos=0, credit_centavos=5, evidence_ref="bank statement")

    def test_deleting_a_draft_removes_its_lines(self):
        self.add()
        self.draft.delete()
        self.assertEqual(JournalLine.objects.count(), 0)


@on_postgres
class EventTriggerTests(Refuses):
    def test_update_and_delete_refused_insert_accepted(self):
        entity = make_entity()
        event = LedgerEvent.objects.create(entity=entity, kind="k", target_type="t", target_ref="r")
        self.refused("append-only", write(LedgerEvent, event.pk).update, kind="x")
        self.refused("append-only", write(LedgerEvent, event.pk).delete)
        LedgerEvent.objects.create(entity=entity, kind="k2", target_type="t", target_ref="r")


@on_postgres
class BalanceTriggerTests(Refuses):
    """The deferred trigger does not fire inside a test's transaction, so each case asks for it
    now, after the writes, inside a savepoint."""

    def setUp(self):
        self.entity = make_entity()
        self.period = make_period(self.entity)
        self.bank = make_account(self.entity, "1020", "asset")
        self.rev = make_account(self.entity, "4010", "revenue")

    def post_with(self, lines, total):
        entry = make_draft(self.entity, "2026-01-15", lines)
        write(JournalEntry, entry.pk).update(
            status="posted", period=self.period, total_centavos=total, **POSTED
        )
        return entry

    def check_now(self):
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ledger_entry_balanced IMMEDIATE")
            cursor.execute("SET CONSTRAINTS ledger_entry_balanced DEFERRED")

    def attempt(self, lines, total):
        self.post_with(lines, total)
        self.check_now()

    def test_balanced_entry_passes(self):
        self.attempt([(self.bank, 100, 0), (self.rev, 0, 100)], 100)

    def test_one_line_unequal_sides_and_wrong_total_refused(self):
        cases = [
            ([(self.bank, 100, 0)], 100),
            ([(self.bank, 100, 0), (self.rev, 0, 90)], 100),
            ([(self.bank, 100, 0), (self.rev, 0, 100)], 99),
        ]
        for lines, total in cases:
            with self.subTest(total=total, count=len(lines)):
                self.refused("is not balanced", self.attempt, lines, total)

    def test_a_line_on_an_account_made_a_header_afterwards_is_refused(self):
        self.post_with([(self.bank, 100, 0), (self.rev, 0, 100)], 100)
        write(GLAccount, self.bank.pk).update(is_postable=False)
        self.refused("cannot take it on that date", self.check_now)
        write(GLAccount, self.bank.pk).update(is_postable=True)


@on_postgres
class ReverseTests(TransactionTestCase):
    available_apps = ["apps.ledger", "apps.operators", "django.contrib.contenttypes", "django.contrib.auth"]

    TRIGGERS = ["ledger_entry_balanced", "ledger_entry_guard", "ledger_event_append_only", "ledger_line_guard"]
    FUNCTIONS = ["ledger_entry_balanced", "ledger_entry_guard", "ledger_event_guard", "ledger_line_guard"]

    def found(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return sorted(row[0] for row in cursor.fetchall())

    def triggers(self):
        return self.found("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'ledger\\_%'")

    def functions(self):
        return self.found("SELECT proname FROM pg_proc WHERE proname LIKE 'ledger\\_%'")

    def test_reverse_drops_the_triggers_and_forward_restores_them(self):
        self.addCleanup(call_command, "migrate", "ledger", "0002", verbosity=0)
        self.assertEqual(self.triggers(), self.TRIGGERS)
        self.assertEqual(self.functions(), self.FUNCTIONS)
        call_command("migrate", "ledger", "0001", verbosity=0)
        self.assertEqual(self.triggers(), [])
        self.assertEqual(self.functions(), [])
        call_command("migrate", "ledger", "0002", verbosity=0)
        self.assertEqual(self.triggers(), self.TRIGGERS)
        self.assertEqual(self.functions(), self.FUNCTIONS)
