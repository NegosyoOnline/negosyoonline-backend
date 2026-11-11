import calendar
import itertools
from datetime import date

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.ledger.models import AccountingEntity, FiscalPeriod, GLAccount, JournalEntry, JournalLine

_counter = itertools.count(1)

_DEBIT_TYPES = ("asset", "expense")


def make_user(name="owner", staff=False):
    return get_user_model().objects.create_user(name, password="pw", is_staff=staff)


def make_entity(kind="rehearsal", books_start="2026-01-01", name=None, user=None, **fields):
    user = user or make_user(f"creator{next(_counter)}")
    start = date.fromisoformat(books_start) if isinstance(books_start, str) else books_start
    return AccountingEntity.objects.create(
        name=name or f"Book {next(_counter)}", kind=kind, books_start_date=start, created_by=user, **fields
    )


def make_account(entity, code, type="asset", **fields):
    contra = fields.get("is_contra", False)
    debit_side = (type in _DEBIT_TYPES) != contra
    fields.setdefault("normal_side", "debit" if debit_side else "credit")
    return GLAccount.objects.create(entity=entity, code=code, name=f"Account {code}", type=type, **fields)


def make_period(entity, year=2026, month=1, status="open"):
    last = calendar.monthrange(year, month)[1]
    return FiscalPeriod.objects.create(
        entity=entity, year=year, month=month, start_date=date(year, month, 1),
        end_date=date(year, month, last), status=status,
    )


def make_draft(entity, entry_date, lines=(), **fields):
    """A draft entry with `lines`, a list of (account, debit, credit) in centavos."""
    if isinstance(entry_date, str):
        entry_date = date.fromisoformat(entry_date)
    fields.setdefault("idempotency_key", f"test:{next(_counter)}")
    fields.setdefault("reason", "test")
    entry = JournalEntry.objects.create(entity=entity, entry_date=entry_date, **fields)
    for number, (account, debit, credit) in enumerate(lines, start=1):
        JournalLine.objects.create(
            entry=entry, line_no=number, account=account, debit_centavos=debit, credit_centavos=credit
        )
    return entry


def force_post(entry, period, seq=1):
    """TEST-ONLY. The posting writer is a later piece (S2-A); until then this sets what posting
    sets and saves, so the guards can be tried on a posted entry. S2-A's tests replace it."""
    entry.status = "posted"
    entry.period = period
    entry.posting_seq = seq
    entry.posted_at = timezone.now()
    entry.seal = "x" * 64
    entry.total_centavos = sum(line.debit_centavos for line in entry.lines.all())
    entry.save()
    return entry


def posted_entry(entity=None, seq=1):
    """A balanced two-line posted entry in an open January 2026 period."""
    entity = entity or make_entity()
    period = make_period(entity)
    bank = make_account(entity, "1020", "asset")
    revenue = make_account(entity, "4010", "revenue")
    entry = make_draft(entity, "2026-01-15", [(bank, 100, 0), (revenue, 0, 100)])
    return force_post(entry, period, seq)
