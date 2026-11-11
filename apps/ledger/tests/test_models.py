from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.ledger.models import (
    AccountingEntity,
    FiscalPeriod,
    GLAccount,
    JournalEntry,
    JournalLine,
    LedgerAccess,
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


class ConstraintCase(TestCase):
    def refused(self, make, *args, **fields):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make(*args, **fields)

    def accepted(self, make, *args, **fields):
        with transaction.atomic():
            return make(*args, **fields)


class EntityTests(ConstraintCase):
    def test_name_unique(self):
        make_entity(name="A")
        self.refused(make_entity, name="A")

    def test_one_live_but_many_rehearsal(self):
        make_entity(kind="live")
        self.refused(make_entity, kind="live")
        make_entity(kind="rehearsal")
        self.accepted(make_entity, kind="rehearsal")

    def test_currency_php_only(self):
        self.refused(make_entity, currency="USD")

    def test_first_month_range(self):
        self.refused(make_entity, first_fiscal_month=0)
        self.refused(make_entity, first_fiscal_month=13)
        self.accepted(make_entity, first_fiscal_month=12)

    def test_kind_valid(self):
        self.refused(make_entity, kind="demo")


class AccountTests(ConstraintCase):
    def setUp(self):
        self.entity = make_entity()

    def test_code_unique_per_book_not_across(self):
        make_account(self.entity, "1020")
        self.refused(make_account, self.entity, "1020")
        self.accepted(make_account, make_entity(), "1020")

    def test_system_key_unique_per_book_blank_repeats(self):
        make_account(self.entity, "1020", system_key="cash.x")
        self.refused(make_account, self.entity, "1021", system_key="cash.x")
        self.accepted(make_account, make_entity(), "1020", system_key="cash.x")
        make_account(self.entity, "1030")
        self.accepted(make_account, self.entity, "1031")

    def test_normal_side_for_every_type_and_contra(self):
        for i, type_ in enumerate(("asset", "liability", "equity", "revenue", "expense")):
            for contra in (False, True):
                natural = "debit" if type_ in ("asset", "expense") else "credit"
                other = "credit" if natural == "debit" else "debit"
                good, bad = (other, natural) if contra else (natural, other)
                code = f"{i}{int(contra)}"
                self.accepted(make_account, self.entity, code + "a", type_, is_contra=contra, normal_side=good)
                self.refused(make_account, self.entity, code + "b", type_, is_contra=contra, normal_side=bad)

    def test_effective_order(self):
        self.refused(make_account, self.entity, "1", effective_from=date(2026, 2, 1), effective_to=date(2026, 1, 1))
        self.accepted(make_account, self.entity, "2", effective_from=date(2026, 1, 1), effective_to=date(2026, 1, 1))
        self.accepted(make_account, self.entity, "3", effective_to=date(2026, 1, 1))

    def test_type_and_status_valid(self):
        self.refused(make_account, self.entity, "1", type="income", normal_side="credit")
        self.refused(make_account, self.entity, "2", status="gone")


class PeriodTests(ConstraintCase):
    def setUp(self):
        self.entity = make_entity()
        self.user = make_user("u")

    def make(self, **f):
        base = dict(entity=self.entity, year=2026, month=1, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
        base.update(f)
        return FiscalPeriod.objects.create(**base)

    def test_unique_month_range_dates_status(self):
        self.make()
        self.refused(self.make)
        self.refused(self.make, month=0)
        self.refused(self.make, month=13)
        self.refused(self.make, month=2, end_date=date(2025, 12, 31))
        self.refused(self.make, month=3, status="frozen")
        self.accepted(self.make, month=2, status="soft_closed")


class EntryTests(ConstraintCase):
    def setUp(self):
        self.entity = make_entity()
        self.period = make_period(self.entity)

    def draft(self, **f):
        return make_draft(self.entity, "2026-01-15", **f)

    def test_idempotency_unique_per_book_and_not_blank(self):
        self.draft(idempotency_key="k")
        self.refused(self.draft, idempotency_key="k")
        self.accepted(make_draft, make_entity(), "2026-01-15", idempotency_key="k")
        self.refused(self.draft, idempotency_key="")

    def test_source_triple_unique_only_with_a_source_type(self):
        src = dict(source_type="billing", source_ref="p1", source_event="confirmed")
        self.draft(**src)
        self.refused(self.draft, **src)
        self.accepted(self.draft, **{**src, "source_event": "other"})
        self.draft()
        self.accepted(self.draft)

    def test_posting_seq_unique_per_book_several_nulls(self):
        self.draft()
        self.accepted(self.draft)
        bank = make_account(self.entity, "1", "asset")
        a, b = self.draft(lines=[(bank, 5, 0)]), self.draft(lines=[(bank, 5, 0)])
        force_post(a, self.period, 7)
        with self.assertRaises(IntegrityError), transaction.atomic():
            force_post(b, self.period, 7)

    def test_one_opening_per_book(self):
        self.draft(kind="opening", reason="opening")
        self.refused(self.draft, kind="opening", reason="opening")
        self.accepted(make_draft, make_entity(), "2026-01-15", kind="opening", reason="opening")

    def test_negative_total(self):
        self.refused(self.draft, total_centavos=-1)

    def test_reason_rule(self):
        self.refused(self.draft, reason="")  # manual: no source type, no reason
        self.refused(self.draft, reason="", kind="adjustment")
        self.accepted(self.draft, reason="", source_type="billing", source_ref="x", source_event="e")
        self.refused(self.draft, reason="", kind="opening")
        original = self.draft()
        self.refused(self.draft, reason="", kind="reversal", reverses=original, source_type="billing")

    def test_reversal_link_both_ways_and_one_to_one(self):
        original = self.draft()
        self.refused(self.draft, kind="reversal", reason="r")  # reversal without a link
        self.refused(self.draft, kind="normal", reverses=original)  # link without reversal
        self.accepted(self.draft, kind="reversal", reason="r", reverses=original)
        self.refused(self.draft, kind="reversal", reason="r", reverses=original)

    def test_posted_needs_period_seal_seq_and_total(self):
        bank, rev = make_account(self.entity, "1", "asset"), make_account(self.entity, "2", "revenue")
        entry = self.draft(lines=[(bank, 5, 0), (rev, 0, 5)])
        now = timezone.now()
        full = dict(status="posted", period=self.period, posting_seq=1, posted_at=now, seal="s" * 64, total_centavos=5)
        for missing in ("period", "posting_seq", "posted_at", "seal", "total_centavos"):
            fields = dict(full)
            fields[missing] = {"period": None, "posting_seq": None, "posted_at": None, "seal": "", "total_centavos": 0}[missing]
            fresh = JournalEntry.objects.get(pk=entry.pk)
            for name, value in fields.items():
                setattr(fresh, name, value)
            with self.assertRaises(IntegrityError), transaction.atomic():
                fresh.save()

    def test_status_and_kind_valid(self):
        entry = self.draft()
        entry.kind = "weird"
        with self.assertRaises(IntegrityError), transaction.atomic():
            entry.save()


class LineTests(ConstraintCase):
    def setUp(self):
        self.entity = make_entity()
        self.account = make_account(self.entity, "1020")
        self.entry = make_draft(self.entity, "2026-01-15")

    def line(self, **f):
        base = dict(entry=self.entry, line_no=1, account=self.account, debit_centavos=5, credit_centavos=0)
        base.update(f)
        return JournalLine.objects.create(**base)

    def test_one_side_only(self):
        self.refused(self.line, debit_centavos=0, credit_centavos=0)
        self.refused(self.line, debit_centavos=5, credit_centavos=5)
        self.refused(self.line, debit_centavos=-5)
        self.refused(self.line, debit_centavos=0, credit_centavos=-5)
        self.accepted(self.line, debit_centavos=0, credit_centavos=5)

    def test_line_no_positive_and_unique(self):
        self.refused(self.line, line_no=0)
        self.line()
        self.refused(self.line)
        self.accepted(self.line, line_no=2)


class RecognitionTests(ConstraintCase):
    def setUp(self):
        self.entry = posted_entry()

    def make(self, **f):
        base = dict(
            entity=self.entry.entity, payment_ref="p", period_month="2026-01", service_start=date(2026, 1, 1),
            service_end=date(2026, 2, 1), amount_to_release_centavos=10, payment_entry=self.entry,
        )
        base.update(f)
        return RecognitionSchedule.objects.create(**base)

    def test_constraints(self):
        self.make()
        self.refused(self.make)
        self.refused(self.make, period_month="2026-02", amount_to_release_centavos=0)
        self.refused(self.make, period_month="2026-03", service_end=date(2026, 1, 1))
        self.refused(self.make, period_month="2026-04", service_end=date(2026, 1, 1))
        self.accepted(self.make, period_month="2026-05")


class AccessTests(ConstraintCase):
    def test_one_row_per_user_per_book_and_level(self):
        entity, user, admin = make_entity(), make_user("a"), make_user("b")
        make = lambda **f: LedgerAccess.objects.create(operator=user, entity=entity, granted_by=admin, **{"level": "owner", **f})
        make()
        self.refused(make)
        other = make_entity()
        self.accepted(lambda: LedgerAccess.objects.create(operator=user, entity=other, level="viewer", granted_by=admin))
        self.refused(lambda: LedgerAccess.objects.create(operator=admin, entity=entity, level="boss", granted_by=admin))
