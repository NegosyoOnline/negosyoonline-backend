"""Issuing a statement for a confirmed payment (Billing Build B5, PR 5; plan section 7.1).

One confirmed payment gives at most one statement (`source_key` is `payment:<btp_id>`). The
number, the statement and its lines commit together or not at all, so numbers have no gaps.
Issuing never blocks or fails a payment: a refusal writes nothing and the safety net retries.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.billing import clock
from apps.billing.models import BankTransferPayment, ChargeLine
from apps.statements import documents, services as issuer_services, switch
from apps.statements.models import (
    BillingProfile, Statement, StatementLine, StatementNumberSeries,
)

logger = logging.getLogger(__name__)

PREFIX = "NOBS"
# plan code of a payment -> statement kind
KINDS = {
    "monthly": Statement.Kind.MONTHLY,
    "annual": Statement.Kind.ANNUAL,
    "launch_promo": Statement.Kind.LAUNCH_PROMO,
    "usage_only": Statement.Kind.USAGE_ONLY,
}
WORDING = {
    "monthly": "NegosyoOnline SaaS subscription — Monthly Plan, {start} to {end}",
    "annual": "NegosyoOnline SaaS subscription — Annual Plan, {start} to {end}",
    "launch_promo": "NegosyoOnline SaaS Subscription — Launch Promo (6 Months), {start} to {end}",
}


class Refused(Exception):
    """A statement cannot be issued now. Nothing was written. `code` never holds a value."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def source_key(payment):
    return f"payment:{payment.public_id}"


def _day(instant):
    value = instant.astimezone(clock.MANILA).date()
    return f"{value.day} {value:%b %Y}"


def _lines(payment, cycle):
    """Frozen lines: the base fee of the term, then each overage charge this payment settled.
    No order or product lines exist. A Subscription Credit line is omitted while it is zero."""
    lines = []
    if payment.plan_code != "usage_only":
        base = payment.amount_centavos - payment.usage_centavos
        description = WORDING[payment.plan_code].format(
            start=_day(cycle.start_at), end=_day(cycle.end_at)
        )
        lines.append(("base", description, 1, base, base, payment.public_id))
    charges = ChargeLine.objects.filter(settled_payment=payment).select_related("cycle")
    for charge in charges.order_by("pk"):
        exact = charge.amount_centavos % charge.quantity == 0
        lines.append((
            "usage",
            f"NegosyoOnline storage overage — {charge.quantity} GB, "
            f"{_day(charge.cycle.start_at)} to {_day(charge.cycle.end_at)}",
            charge.quantity if exact else 1,
            charge.amount_centavos // charge.quantity if exact else charge.amount_centavos,
            charge.amount_centavos,
            f"charge:{charge.pk}",
        ))
    return lines, [c.cycle for c in charges]


def _issuer_snapshot():
    issuer = issuer_services.current_issuer()
    if issuer is None:
        raise Refused("no_issuer")
    snapshot = {
        "public_id": issuer.public_id, "legal_name": issuer.legal_name,
        "trade_name": issuer.trade_name, "tin": issuer.tin, "branch_code": issuer.branch_code,
        "registered_address": issuer.registered_address, "tax_type": issuer.tax_type,
        "cor_number": issuer.cor_number, "cor_date": issuer.cor_date.isoformat(),
        "contact_email": issuer.contact_email,
    }
    if not all(snapshot[name] for name in issuer_services.ISSUER_FIELDS):
        raise Refused("issuer_incomplete")
    return snapshot


def _bill_to(subscription):
    """The Subscriber's billing profile, else the account's name and email. The TIN appears
    only when the Subscriber supplied it."""
    site = subscription.site
    profile = BillingProfile.objects.filter(site=site).first()
    if profile is not None:
        bill_to = {
            "name": profile.company_name, "email": profile.billing_email,
            "address": profile.address,
        }
        if profile.tin:
            bill_to["tin"] = profile.tin
        return bill_to
    account = site.account
    return {"name": account.name or account.email, "email": account.email, "address": ""}


def _next_number(prefix, year):
    series, _ = StatementNumberSeries.objects.get_or_create(prefix=prefix, year=year)
    series = StatementNumberSeries.objects.select_for_update().get(pk=series.pk)
    series.last_value += 1
    series.save()
    return f"{prefix}-{year}-{series.last_value:06d}"


def issue_for_payment(payment, *, now=None):
    """The statement of one confirmed payment. Idempotent and safe to run concurrently: the
    first statement is returned to every later call. Returns None while the statements switch
    is off. Raises `Refused` when it cannot issue yet; nothing is written then."""
    if not switch.statements_enabled():
        return None
    key = source_key(payment)
    existing = Statement.objects.filter(source_key=key).first()
    if existing is not None:
        return existing
    with transaction.atomic():
        payment = (
            BankTransferPayment.objects.select_for_update(of=("self",))
            .select_related("subscription__site__account", "cycle").get(pk=payment.pk)
        )
        existing = Statement.objects.filter(source_key=key).first()
        if existing is not None:
            return existing
        if payment.status != BankTransferPayment.Status.CONFIRMED:
            raise Refused("not_confirmed")
        kind = KINDS.get(payment.plan_code)
        if kind is None:
            raise Refused("unknown_plan")
        issuer = _issuer_snapshot()
        cycle = payment.cycle
        lines, charge_cycles = _lines(payment, cycle)
        total = sum(line[4] for line in lines)
        if total != (payment.amount_received_centavos or payment.amount_centavos):
            raise Refused("total_mismatch")
        if cycle is not None:
            start, end = cycle.start_at, cycle.end_at
        else:  # a usage-only payment: the periods its overage was charged for
            issued = now or timezone.now()
            start = min((c.start_at for c in charge_cycles), default=payment.decided_at or issued)
            end = max((c.end_at for c in charge_cycles), default=payment.decided_at or issued)
        issued_at = now or timezone.now()
        number = _next_number(PREFIX, issued_at.astimezone(clock.MANILA).year)
        bill_to = _bill_to(payment.subscription)
        is_test = not settings.BILLING_LIVE_DOCUMENTS
        document = {
            "number": number, "kind": kind, "issuer": issuer, "bill_to": bill_to,
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "total_centavos": total, "is_test": is_test, "payment_reference": payment.reference,
            "issued_at": issued_at.isoformat(),
            "lines": [
                {"kind": k, "description": d, "quantity": q, "unit_centavos": u,
                 "amount_centavos": a}
                for k, d, q, u, a, _ in lines
            ],
        }
        stored = documents.store_document(document)  # raises StoreUnavailable: nothing written
        statement = Statement.objects.create(
            number=number, document_type=Statement.DocumentType.STATEMENT, kind=kind,
            subscription=payment.subscription, cycle=cycle, source_key=key, period_start=start,
            period_end=end, total_centavos=total, is_test=is_test, issuer=issuer,
            bill_to=bill_to, payment_reference=payment.reference, pdf_key=stored.key,
            pdf_sha256=stored.sha256, pdf_size=stored.size, issued_at=issued_at,
        )
        StatementLine.objects.bulk_create([
            StatementLine(
                statement=statement, position=position, kind=k, description=d, quantity=q,
                unit_centavos=u, amount_centavos=a, source_ref=ref,
            )
            for position, (k, d, q, u, a, ref) in enumerate(lines, start=1)
        ])
        _link_item(payment, statement)
        return statement


def _link_item(payment, statement):
    """Point the register's pending item at the statement, once. No item, no link."""
    from apps.invoices.models import ManualInvoiceItem  # noqa: PLC0415 - invoices imports us

    item = (
        ManualInvoiceItem.objects.select_for_update()
        .filter(source_kind="bank_transfer", source_ref=payment.public_id).first()
    )
    if item is not None and item.statement_id is None:
        item.statement = statement
        item.save()


def issue_safely(payment, *, now=None):
    """`issue_for_payment` for jobs: a refusal or an error is logged by code only and returns
    None, so one payment never stops the rest."""
    try:
        return issue_for_payment(payment, now=now)
    except Refused as refused:
        logger.info("statement not issued for payment %s: %s", payment.public_id, refused.code)
    except documents.StoreUnavailable:
        logger.info("statement not issued for payment %s: store_unavailable", payment.public_id)
    except Exception:  # noqa: BLE001 - one payment must never stop the others
        logger.exception("statement not issued for payment %s", payment.public_id)
    return None


def _unissued():
    have = Statement.objects.filter(source_key__startswith="payment:").values_list(
        "source_key", flat=True
    )
    keys = {key.removeprefix("payment:") for key in have}
    rows = (
        BankTransferPayment.objects.filter(status=BankTransferPayment.Status.CONFIRMED)
        .select_related("subscription__site__account", "cycle").order_by("pk")
    )
    return [p for p in rows if p.public_id not in keys]


def ensure_statements():
    """The safety net: issue a statement for each confirmed payment that has none. Returns how
    many it issued. Does nothing while the statements switch is off."""
    if not switch.statements_enabled():
        return 0
    return sum(1 for payment in _unissued() if issue_safely(payment) is not None)


def missing_statements():
    return len(_unissued())
