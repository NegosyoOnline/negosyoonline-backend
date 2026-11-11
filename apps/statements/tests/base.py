"""Builders for the B5 tests. Rows are made directly: the services come in later PRs."""
from datetime import date, datetime, timezone

from django.contrib.auth import get_user_model

from apps.billing.tests.base import make_active
from apps.invoices.models import InvoiceBook, ManualInvoice, ManualInvoiceItem
from apps.statements.models import IssuerProfile, Statement, StatementLine

User = get_user_model()
WHEN = datetime(2026, 11, 25, 2, 0, tzinfo=timezone.utc)
_counter = [0]


def new_subscription():
    """A paid subscription of its own: a new account and site each time."""
    _counter[0] += 1
    n = _counter[0]
    return make_active(email=f"owner{n}@example.test", name=f"store-{n}")


def make_staff(username=None):
    _counter[0] += 1
    return User.objects.create(username=f"{username or 'staff'}{_counter[0]}", is_staff=True)


def make_issuer(staff=None, verified=False, **fields):
    staff = staff or make_staff(f"issuer{_counter[0]}")
    _counter[0] += 1
    values = dict(
        legal_name="Test Issuer", tin="000-000-000-000", branch_code="000",
        registered_address="Test address", cor_number="COR-TEST", cor_date=date(2026, 1, 1),
        contact_email="issuer@example.test", created_by=staff,
    )
    values.update(fields)
    issuer = IssuerProfile.objects.create(**values)
    if verified:
        issuer.verified_at, issuer.verified_by = WHEN, staff
        issuer.save()
    return issuer


def make_book(issuer=None, series="A", **fields):
    values = dict(
        issuer=issuer or make_issuer(), series=series, serial_from=1, serial_to=50,
        atp_reference="ATP-TEST", atp_date=date(2026, 1, 1),
    )
    values.update(fields)
    return InvoiceBook.objects.create(**values)


def make_item(subscription=None, ref=None, total=39900, **fields):
    subscription = subscription or new_subscription()
    cycle = subscription.cycles.order_by("-sequence").first()
    _counter[0] += 1
    values = dict(
        subscription=subscription, cycle=cycle, site_public_id="site_test", site_name="Test",
        source_kind="bank_transfer", source_ref=ref or f"btp_test{_counter[0]}",
        plan_code="monthly", period_start=cycle.start_at, period_end=cycle.end_at,
        total_centavos=total, lines=[], paid_at=WHEN, sale_date=date(2026, 11, 25),
        tax_year=2026, is_test=True,
    )
    values.update(fields)
    return ManualInvoiceItem.objects.create(**values)


def make_invoice(item=None, book=None, serial=1, staff=None, **fields):
    item = item or make_item()
    values = dict(
        item=item, book=book or make_book(series=f"B{serial}{_counter[0]}"), serial_number=serial,
        issued_on=date(2026, 11, 26), buyer_name="Buyer", description="d",
        service_start=date(2026, 11, 25), service_end=date(2026, 12, 25),
        total_sales_centavos=item.total_centavos, amount_due_centavos=item.total_centavos,
        recorded_by=staff or make_staff(f"rec{_counter[0]}"),
    )
    values.update(fields)
    return ManualInvoice.objects.create(**values)


def make_statement(subscription=None, key=None, number=None, **fields):
    subscription = subscription or new_subscription()
    cycle = subscription.cycles.order_by("-sequence").first()
    _counter[0] += 1
    values = dict(
        number=number or f"NOBS-2026-{_counter[0]:06d}", document_type="statement",
        kind="monthly", subscription=subscription, cycle=cycle,
        source_key=key or f"payment:btp_test{_counter[0]}",
        period_start=cycle.start_at, period_end=cycle.end_at, total_centavos=39900,
        is_test=True, issuer={}, bill_to={}, pdf_key="k", pdf_sha256="0" * 64, pdf_size=1,
        issued_at=WHEN,
    )
    values.update(fields)
    return Statement.objects.create(**values)


def make_line(statement, position=1, **fields):
    values = dict(
        statement=statement, position=position, kind="base", description="d",
        unit_centavos=39900, amount_centavos=39900,
    )
    values.update(fields)
    return StatementLine.objects.create(**values)
