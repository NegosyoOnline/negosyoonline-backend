"""The manual BIR invoice register (Billing Build B5, PR 3).

Coach Ed handwrites a BIR-registered Non-VAT invoice for each paid sale; the system only keeps
the record. It never issues an official invoice, never assigns or advances a printed serial
and never backdates: every serial and the issue date are typed from the paper. Each change
takes the item's row lock, writes an InvoiceEvent under its idempotency key and an audit entry.
"""
import hashlib
import logging
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.billing import clock, usage
from apps.billing.models import BankTransferPayment, ChargeLine
from apps.common.errors import ApiError
from apps.common.private_files import StoreError
from apps.invoices import scans
from apps.invoices.models import (
    InvoiceBook, InvoiceEvent, InvoiceScan, ManualInvoice, ManualInvoiceItem,
)
from apps.operators import audit
from apps.statements import services as issuer_services
from apps.statements.managers import Immutable
from apps.statements.models import BillingProfile

logger = logging.getLogger(__name__)
Item = ManualInvoiceItem
Code = InvoiceEvent.Code

NOTICE = (
    "DO NOT issue invoices in a single delayed Monday batch. Write each invoice when its "
    "payment arrives; Monday is for scanning and reconciling."
)
# The paper wording per plan, from the task (parent task, 2026-11-08).
WORDING = {
    "monthly": "NegosyoOnline SaaS subscription — Monthly Plan, {start} to {end}",
    "annual": "NegosyoOnline SaaS subscription — Annual Plan, {start} to {end}",
    "launch_promo": "NegosyoOnline SaaS Subscription — Launch Promo (6 Months), {start} to {end}",
}


def _invalid(field, message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={field: [message]}
    )


def _conflict(code, message):
    return ApiError(code, message, status=409)


def _not_found():
    return ApiError("not_found", "Not found.", status=404, event="object_not_found", reason="missing")


def manila_date(instant):
    return instant.astimezone(clock.MANILA).date()


def paper_date(value):
    """25 Nov 2026"""
    return f"{value.day} {value:%b %Y}"


# ---------------------------------------------------------------- opening an item


def _lines(payment):
    """The frozen lines of a confirmed payment: its base fee, then each overage charge it
    settled. They add up to the amount received; nothing here reads usage or a price."""
    lines = []
    # Subscription Credit (Billing B6) reduces the base first: the item shows what was paid. A
    # payment with no credit is unchanged. No credit or discount line is added.
    base = payment.amount_centavos - payment.usage_centavos
    credit_left = payment.credit_centavos
    taken = min(credit_left, base)
    base -= taken
    credit_left -= taken
    cycle = payment.cycle
    if payment.plan_code != "usage_only" and (base > 0 or not payment.credit_centavos):
        wording = WORDING.get(payment.plan_code)
        if wording is not None:
            description = wording.format(
                start=paper_date(manila_date(cycle.start_at)), end=paper_date(manila_date(cycle.end_at))
            )
        else:
            description = (
                f"NegosyoOnline SaaS subscription — {payment.plan_code}, "
                f"{paper_date(manila_date(cycle.start_at))} to {paper_date(manila_date(cycle.end_at))}"
            )
        lines.append({
            "kind": "base", "description": description, "quantity": 1,
            "unit_centavos": base, "amount_centavos": base, "source_ref": payment.public_id,
        })
    for charge in ChargeLine.objects.filter(settled_payment=payment).select_related("cycle").order_by("pk"):
        start, end = manila_date(charge.cycle.start_at), manila_date(charge.cycle.end_at)
        if charge.kind == ChargeLine.Kind.OVERAGE_ORDERS:  # the month the orders were counted for
            first, last = usage.period_bounds(
                charge.cycle, charge.period_index, charge.subscription.anchor_day
            )
            description = (
                f"{usage.line_label(charge.kind, charge.quantity, charge.amount_centavos)}, "
                f"{paper_date(manila_date(first))} to {paper_date(manila_date(last))}"
            )
        else:
            description = (
                f"NegosyoOnline storage overage — {charge.quantity} GB, "
                f"{paper_date(start)} to {paper_date(end)}"
            )
        exact = charge.amount_centavos % charge.quantity == 0
        lines.append({
            "kind": "usage",
            "description": description,
            "quantity": charge.quantity if exact else 1,
            "unit_centavos": charge.amount_centavos // charge.quantity if exact else charge.amount_centavos,
            "amount_centavos": charge.amount_centavos,
            "source_ref": f"charge:{charge.pk}",
        })
    # Credit beyond the base (a payment that carried overage): it comes off the usage lines in
    # order, so the lines still add up to the amount received.
    for line in lines:
        if credit_left <= 0:
            break
        if line["kind"] == "usage":
            cut = min(credit_left, line["amount_centavos"])
            credit_left -= cut
            line["amount_centavos"] -= cut
            line["quantity"], line["unit_centavos"] = 1, line["amount_centavos"]
    return [line for line in lines if line["amount_centavos"] > 0 or not payment.credit_centavos]


def open_item(payment, *, now=None):
    """The pending invoice item of one confirmed payment. Idempotent: the unique payment key
    returns the existing row. Called from the confirmation's transaction and by the safety net.
    Returns None while the register is off."""
    from apps.statements import switch  # noqa: PLC0415 - billing must not import this at load

    if not switch.register_enabled():
        return None
    if payment.status != BankTransferPayment.Status.CONFIRMED:
        raise ValueError("Only a confirmed payment opens an invoice item.")
    existing = Item.objects.filter(source_kind="bank_transfer", source_ref=payment.public_id).first()
    if existing is not None:
        return existing
    if payment.amount_received_centavos == 0:
        # A bill that Subscription Credit covered in full: nothing was received, so there is
        # no sale to invoice. Not an error; the safety net skips it too.
        logger.info("no invoice item for payment %s: covered by credit", payment.public_id)
        return None
    paid_at = payment.decided_at or now or timezone.now()
    lines = _lines(payment)
    total = sum(line["amount_centavos"] for line in lines)
    if total != payment.amount_received_centavos:
        raise ValueError("The lines of a payment must add up to the amount received.")
    subscription, cycle = payment.subscription, payment.cycle
    site = subscription.site
    if cycle is not None:
        start, end = cycle.start_at, cycle.end_at
    else:  # a usage-only payment: the periods its overage was charged for
        charges = list(
            ChargeLine.objects.filter(settled_payment=payment).select_related("cycle")
        )
        start = min((c.cycle.start_at for c in charges), default=paid_at)
        end = max((c.cycle.end_at for c in charges), default=paid_at)
    sale_date = manila_date(paid_at)
    item = Item.objects.create(
        subscription=subscription, cycle=cycle, site_public_id=site.public_id,
        site_name=site.display_name, source_kind="bank_transfer", source_ref=payment.public_id,
        plan_code=payment.plan_code, period_start=start, period_end=end,
        total_centavos=total, lines=lines, paid_at=paid_at, sale_date=sale_date,
        tax_year=sale_date.year, is_test=not settings.BILLING_LIVE_DOCUMENTS,
    )
    InvoiceEvent.objects.create(
        item=item, code=Code.OPENED, idempotency_key=f"opened:{payment.public_id}",
    )
    return item


def open_item_safely(payment, *, now=None):
    """`open_item` inside its own savepoint, for the confirmation: an error here is logged,
    with no personal value, and rolls back only the item. The safety net then creates it, so
    a payment is never lost to the register."""
    try:
        with transaction.atomic():
            return open_item(payment, now=now)
    except Exception:  # noqa: BLE001 - the confirmation must never fail because of the register
        logger.exception("invoice item not opened for payment %s", payment.public_id)
        return None


def ensure_items():
    """The safety net: an item for each confirmed payment that has none, including payments
    confirmed before the switch was on. Returns how many it opened."""
    from apps.statements import switch  # noqa: PLC0415

    if not switch.register_enabled():
        return 0
    have = Item.objects.filter(source_kind="bank_transfer").values("source_ref")
    opened = 0
    rows = (
        BankTransferPayment.objects.filter(status=BankTransferPayment.Status.CONFIRMED)
        .exclude(public_id__in=have).exclude(amount_received_centavos=0)
        .select_related("subscription__site", "cycle").order_by("pk")
    )
    for payment in rows:
        if open_item_safely(payment) is not None:
            opened += 1
    return opened


def missing_items():
    """How many confirmed payments have no item (the dry run of the backfill)."""
    have = Item.objects.filter(source_kind="bank_transfer").values("source_ref")
    return (
        BankTransferPayment.objects.filter(status=BankTransferPayment.Status.CONFIRMED)
        .exclude(public_id__in=have).exclude(amount_received_centavos=0).count()
    )


# ---------------------------------------------------------------- reading


def live_invoice(item):
    return item.invoices.exclude(status=ManualInvoice.Status.VOIDED).first()


def serial_text(invoice):
    book = invoice.book
    return f"{book.series}{str(invoice.serial_number).zfill(book.serial_width)}"


def exceptions(item, invoice=None, *, today=None):
    """The facts the operator must see about a sale and its invoice. None refuses anything."""
    today = today or manila_date(timezone.now())
    found = []
    if item.plan_code not in WORDING and item.plan_code != "usage_only":
        found.append({"code": "unknown_plan_wording"})
    if invoice is None:
        age = (today - item.sale_date).days
        if age > 0:
            found.append({"code": "overdue", "days": age})
        return found
    book = invoice.book
    if not book.serial_from <= invoice.serial_number <= book.serial_to:
        found.append({"code": "serial_out_of_range"})
    others = ManualInvoice.objects.filter(book=book).exclude(pk=invoice.pk)
    if invoice.serial_number > book.serial_from and not others.filter(
        serial_number=invoice.serial_number - 1
    ).exists():
        found.append({"code": "serial_gap"})
    if others.filter(serial_number__lt=invoice.serial_number, issued_on__gt=invoice.issued_on).exists() \
            or others.filter(serial_number__gt=invoice.serial_number, issued_on__lt=invoice.issued_on).exists():
        found.append({"code": "serial_out_of_order"})
    if invoice.issued_on < item.sale_date:
        found.append({"code": "issued_before_payment"})
    elif invoice.issued_on > item.sale_date:
        found.append({"code": "issued_late", "days": (invoice.issued_on - item.sale_date).days})
    if (
        invoice.service_start != manila_date(item.period_start)
        or invoice.service_end != manila_date(item.period_end)
    ):
        found.append({"code": "service_dates_differ"})
    if invoice.discount_centavos or invoice.withholding_centavos:
        found.append({"code": "discount_or_withholding"})
    return found


def suggested_text(item):
    """What to write on the paper, in the paper form's order. Computed from the frozen item
    and the current verified issuer and the book; nothing is stored, and the actual issue date
    and the serial are never filled in."""
    issuer = issuer_services.current_issuer()
    profile = BillingProfile.objects.filter(site__public_id=item.site_public_id).first()
    account = item.subscription.site.account
    return {
        "label": "WRITE THIS ON PAPER",
        "header": None if issuer is None else {
            "legal_name": issuer.legal_name, "trade_name": issuer.trade_name, "tin": issuer.tin,
            "branch_code": issuer.branch_code, "address": issuer.registered_address,
            "tax_type": issuer.tax_type,
        },
        "number": "Type the preprinted serial from the paper. The system never assigns one.",
        "date": {
            "payment_date": item.sale_date.isoformat(),
            "issue_date": "Write the day you actually write it. There is no default.",
        },
        "sale_type": "cash",
        "sold_to": {
            "name": profile.company_name if profile else (account.name or account.email),
            "address": profile.address if profile else "",
            "tin": profile.tin if profile else "",
        },
        "rows": [
            {"description": l["description"], "quantity": l["quantity"],
             "unit_centavos": l["unit_centavos"], "amount_centavos": l["amount_centavos"]}
            for l in item.lines
        ],
        "total_sales_centavos": item.total_centavos,
        "deductions": "Leave the discount and withholding boxes empty unless the sale really has one. "
                      "Never write a payment processor's fee, its VAT or a remittance withholding there.",
        "signature": "Not filled by the system.",
    }


def next_expected(book):
    """A hint only: one after the highest recorded serial, else the first."""
    top = book.invoices.order_by("-serial_number").values_list("serial_number", flat=True).first()
    return book.serial_from if top is None else top + 1


# ---------------------------------------------------------------- changing


def _event(item, code, operator, key, invoice=None, detail=None):
    return InvoiceEvent.objects.create(
        item=item, invoice=invoice, code=code, actor=operator, idempotency_key=key,
        detail=detail or {},
    )


def _item_for_update(public_id):
    if not isinstance(public_id, str) or not public_id:
        raise _invalid("item", "Name the invoice item.")
    try:
        return Item.objects.select_for_update().get(public_id=public_id)
    except Item.DoesNotExist:
        raise _not_found() from None


def _text(body, field, limit, *, required=True):
    value = body.get(field, "")
    if not isinstance(value, str):
        raise _invalid(field, "Send text.")
    value = value.strip()
    if required and not value:
        raise _invalid(field, "This is required.")
    if len(value) > limit:
        raise _invalid(field, f"At most {limit} characters.")
    return value


def _day(body, field):
    try:
        return date.fromisoformat(body.get(field))
    except (TypeError, ValueError):
        raise _invalid(field, "Send a date as YYYY-MM-DD.") from None


def _amount(body, field, *, default=None):
    value = body.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid(field, "Send centavos as a whole number, zero or more.")
    return value


def record_invoice(operator, body):
    """Record the paper invoice the operator wrote: `pending -> recorded`. A repeat of the same
    item, book and serial returns the invoice already recorded."""
    series = _text(body, "series", 20, required=False)
    serial_from = body.get("serial_from")
    serial = body.get("serial_number")
    for field, value in (("serial_from", serial_from), ("serial_number", serial)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _invalid(field, "Send a whole number.")
    issued_on = _day(body, "issued_on")
    sale_type = body.get("sale_type", "cash")
    if sale_type not in ManualInvoice.SaleType.values:
        raise _invalid("sale_type", "Choose cash or charge.")
    values = {
        "buyer_name": _text(body, "buyer_name", 160),
        "buyer_tin": _text(body, "buyer_tin", 17, required=False),
        "buyer_address": _text(body, "buyer_address", 255, required=False),
        "description": _text(body, "description", 300),
        "service_start": _day(body, "service_start"),
        "service_end": _day(body, "service_end"),
        "total_sales_centavos": _amount(body, "total_sales_centavos"),
        "discount_centavos": _amount(body, "discount_centavos", default=0),
        "withholding_centavos": _amount(body, "withholding_centavos", default=0),
    }
    discount_kind = body.get("discount_kind", "")
    if values["discount_centavos"]:
        if discount_kind not in ManualInvoice.DiscountKind.values:
            raise _invalid("discount_kind", "Name the kind of discount.")
    elif discount_kind:
        raise _invalid("discount_kind", "There is no discount.")
    if values["service_end"] < values["service_start"]:
        raise _invalid("service_end", "The service cannot end before it starts.")
    due = values["total_sales_centavos"] - values["discount_centavos"] - values["withholding_centavos"]
    if due < 0:
        raise _invalid("discount_centavos", "The deductions are more than the total.")
    with transaction.atomic():
        item = _item_for_update(body.get("item"))
        try:
            book = InvoiceBook.objects.select_for_update().get(series=series, serial_from=serial_from)
        except InvoiceBook.DoesNotExist:
            raise _invalid("series", "That invoice book is not recorded.") from None
        existing = live_invoice(item)
        if existing is not None:
            if existing.book_id == book.pk and existing.serial_number == serial:
                return existing  # a repeat
            raise _conflict("already_recorded", "This sale already has an invoice. Void it first.")
        if item.status != Item.Status.PENDING:
            raise _conflict("not_pending", "This sale is not waiting for an invoice.")
        if not book.active:
            raise _conflict("book_inactive", "That invoice book is not active.")
        if values["total_sales_centavos"] != item.total_centavos:
            raise _conflict("amount_mismatch", "The total on the invoice is not the amount paid.")
        if ManualInvoice.objects.filter(book=book, serial_number=serial).exists():
            raise _conflict("duplicate_serial", "That serial is already recorded in this book.")
        invoice = ManualInvoice(
            item=item, book=book, serial_number=serial, issued_on=issued_on, sale_type=sale_type,
            discount_kind=discount_kind, amount_due_centavos=due, recorded_by=operator, **values,
        )
        invoice.save()
        item.status = Item.Status.RECORDED
        item.save()
        _event(item, Code.RECORDED, operator, f"recorded:{invoice.public_id}", invoice)
        audit.record(
            operator, "billing_invoice_recorded", target_type="site", target_id=item.site_public_id,
            detail={"item": item.public_id, "invoice": invoice.public_id, "serial": serial_text(invoice)},
        )
    return invoice


def void_invoice(operator, public_id, reason):
    """Void an invoice, keeping the row and its events. The item returns to `pending`, so a
    replacement can be recorded. A voided invoice answers a repeat as it is."""
    if not isinstance(public_id, str) or not public_id:
        raise _invalid("invoice", "Name the invoice.")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason or len(reason) > 300:
        raise _invalid("reason", "Give a reason of at most 300 characters.")
    with transaction.atomic():
        try:
            invoice = ManualInvoice.objects.select_related("item", "book").get(public_id=public_id)
        except ManualInvoice.DoesNotExist:
            raise _not_found() from None
        item = _item_for_update(invoice.item.public_id)
        invoice = ManualInvoice.objects.select_related("book").get(pk=invoice.pk)
        if invoice.status == ManualInvoice.Status.VOIDED:
            return invoice
        invoice.status, invoice.void_reason = ManualInvoice.Status.VOIDED, reason
        invoice.save()
        item.status = Item.Status.PENDING
        item.save()
        _event(item, Code.VOIDED, operator, f"voided:{invoice.public_id}", invoice)
        audit.record(
            operator, "billing_invoice_voided", target_type="site", target_id=item.site_public_id,
            reason=reason, detail={"item": item.public_id, "invoice": invoice.public_id},
        )
    return invoice


# ---------------------------------------------------------------- scans


def _scan_store_unavailable():
    return ApiError(
        "scan_store_unavailable", "The scan could not be stored. Try again in a moment.", status=503
    )


def _locked_scan(public_id):
    """(item locked, invoice, scan) for a scan id, or 404. The item row lock comes first, then
    the invoice and the scan are read again under it."""
    if not isinstance(public_id, str) or not public_id:
        raise _invalid("scan", "Name the scan.")
    try:
        scan = InvoiceScan.objects.select_related("invoice__item").get(public_id=public_id)
    except InvoiceScan.DoesNotExist:
        raise _not_found() from None
    item = _item_for_update(scan.invoice.item.public_id)
    scan = InvoiceScan.objects.select_related("invoice__book").get(pk=scan.pk)
    return item, scan.invoice, scan


def upload_scan(operator, invoice_public_id, content):
    """Store the scan of a recorded invoice: `recorded -> uploaded`. JPEG, PNG or PDF by its
    first bytes, at most 8 MB, never a URL. A file already held by another live scan is refused."""
    if not isinstance(invoice_public_id, str) or not invoice_public_id:
        raise _invalid("invoice", "Name the invoice.")
    content = bytes(content or b"")
    if not content:
        raise _invalid("scan", "A scan is needed.")
    if len(content) > scans.MAX_BYTES:
        raise _invalid("scan", "The file is too large.")
    content_type = scans.sniff(content)
    if content_type is None:
        raise _invalid("scan", "The file must be a JPEG, a PNG or a PDF.")
    sha256 = hashlib.sha256(content).hexdigest()
    saved = []
    try:
        with transaction.atomic():
            try:
                invoice = ManualInvoice.objects.select_related("item").get(public_id=invoice_public_id)
            except ManualInvoice.DoesNotExist:
                raise _not_found() from None
            item = _item_for_update(invoice.item.public_id)
            invoice = ManualInvoice.objects.select_related("book").get(pk=invoice.pk)
            if invoice.status != ManualInvoice.Status.RECORDED:
                raise _conflict("not_recorded", "This invoice is not waiting for a scan.")
            held = InvoiceScan.objects.exclude(status=InvoiceScan.Status.REJECTED)
            if held.filter(sha256=sha256).exists():
                raise _conflict("duplicate_scan", "This file is already attached to an invoice.")
            try:
                key = scans.save(content, content_type)
            except StoreError:
                raise _scan_store_unavailable() from None
            saved.append(key)
            scan = InvoiceScan.objects.create(
                invoice=invoice, store_key=key, content_type=content_type, size=len(content),
                sha256=sha256, uploaded_by=operator,
            )
            invoice.status = ManualInvoice.Status.UPLOADED
            invoice.save()
            item.status = Item.Status.UPLOADED
            item.save()
            _event(item, Code.SCAN_UPLOADED, operator, f"scan_uploaded:{scan.public_id}", invoice,
                   {"scan": scan.public_id, "size": scan.size})
            audit.record(
                operator, "billing_invoice_scan_uploaded", target_type="site",
                target_id=item.site_public_id,
                detail={"item": item.public_id, "invoice": invoice.public_id, "scan": scan.public_id,
                        "size": scan.size, "amount_due_centavos": invoice.amount_due_centavos},
            )
    except BaseException:
        # The row did not commit, so a file saved inside the transaction has no row.
        for key in saved:
            scans.discard(key)
        raise
    return scan


def verify_scan(operator, scan_public_id):
    """The operator confirms the scan is legible and matches serial, date and amount:
    `uploaded -> verified` on the scan, the invoice and the item. A repeat returns the scan."""
    with transaction.atomic():
        item, invoice, scan = _locked_scan(scan_public_id)
        if scan.status == InvoiceScan.Status.VERIFIED:
            return scan
        if scan.status != InvoiceScan.Status.UPLOADED or invoice.status != ManualInvoice.Status.UPLOADED:
            raise _conflict("not_uploaded", "This scan is not waiting for a decision.")
        now = timezone.now()
        scan.status, scan.decided_at, scan.decided_by = InvoiceScan.Status.VERIFIED, now, operator
        scan.save()
        invoice.status, invoice.verified_at, invoice.verified_by = (
            ManualInvoice.Status.VERIFIED, now, operator
        )
        invoice.save()
        item.status = Item.Status.VERIFIED
        item.save()
        _event(item, Code.VERIFIED, operator, f"verified:{scan.public_id}", invoice,
               {"scan": scan.public_id})
        audit.record(
            operator, "billing_invoice_verified", target_type="site", target_id=item.site_public_id,
            detail={"item": item.public_id, "invoice": invoice.public_id, "scan": scan.public_id,
                    "amount_due_centavos": invoice.amount_due_centavos},
        )
    return scan


def reject_scan(operator, scan_public_id, reason):
    """The operator refuses the scan with a reason: it stays, and the invoice and the item
    return to `recorded` for a new upload. A repeat with the same reason returns the scan."""
    if reason not in InvoiceScan.RejectReason.values:
        raise _invalid("reason", "Choose one of the listed reasons.")
    with transaction.atomic():
        item, invoice, scan = _locked_scan(scan_public_id)
        if scan.status == InvoiceScan.Status.REJECTED and scan.reject_reason == reason:
            return scan
        if scan.status != InvoiceScan.Status.UPLOADED or invoice.status != ManualInvoice.Status.UPLOADED:
            raise _conflict("not_uploaded", "This scan is not waiting for a decision.")
        scan.status, scan.reject_reason = InvoiceScan.Status.REJECTED, reason
        scan.decided_at, scan.decided_by = timezone.now(), operator
        scan.save()
        invoice.status = ManualInvoice.Status.RECORDED
        invoice.save()
        item.status = Item.Status.RECORDED
        item.save()
        _event(item, Code.SCAN_REJECTED, operator, f"scan_rejected:{scan.public_id}", invoice,
               {"scan": scan.public_id, "reason": reason})
        audit.record(
            operator, "billing_invoice_scan_rejected", target_type="site",
            target_id=item.site_public_id,
            detail={"item": item.public_id, "invoice": invoice.public_id, "scan": scan.public_id,
                    "reason": reason},
        )
    return scan


def _read_scan(key):
    try:
        return scans.read(key)
    except StoreError:
        raise _scan_store_unavailable() from None
    except ValueError:
        raise _not_found() from None


def scan_for_operator(operator, scan_public_id):
    """The scan file as (content, content type), for staff. Each read is audited."""
    if not isinstance(scan_public_id, str) or not scan_public_id:
        raise _invalid("scan", "Name the scan.")
    try:
        scan = InvoiceScan.objects.select_related("invoice__item").get(public_id=scan_public_id)
    except InvoiceScan.DoesNotExist:
        raise _not_found() from None
    content = _read_scan(scan.store_key)
    item = scan.invoice.item
    audit.record(
        operator, "billing_invoice_scan_viewed", target_type="site", target_id=item.site_public_id,
        detail={"item": item.public_id, "invoice": scan.invoice.public_id, "scan": scan.public_id},
    )
    return content, scan.content_type


# What a Subscriber sees (plan 6.5 and 9.1).
PENDING_TEXT = "Payment received; official invoice copy pending upload"
COPY_LABEL = "Copy of manually issued BIR-registered Non-VAT Invoice"


def invoices_of(subscription, *, limit, cursor_item=None):
    """One row per paid sale of the subscription, newest first. A row is named by the item's
    id: it names the sale, never an invoice or a scan, so it says nothing before verification.
    Returns (items, more)."""
    rows = Item.objects.filter(subscription=subscription).order_by("-pk")
    if cursor_item is not None:
        rows = rows.filter(pk__lt=cursor_item.pk)
    rows = list(rows[: limit + 1])
    return rows[:limit], len(rows) > limit


def item_of(subscription, item_public_id):
    """The subscription's own item, or None for a missing or foreign id."""
    return Item.objects.filter(subscription=subscription, public_id=item_public_id).first()


def copy_for_owner(subscription, item_public_id):
    """The verified scan of the subscription's sale as (content, content type). The same 404
    for every other case: unknown or foreign id, no invoice, a voided one, a scan not verified."""
    item = item_of(subscription, item_public_id)
    if item is None or item.status != Item.Status.VERIFIED:
        raise _not_found()
    invoice = item.invoices.filter(status=ManualInvoice.Status.VERIFIED).first()
    scan = invoice.scans.filter(status=InvoiceScan.Status.VERIFIED).first() if invoice else None
    if scan is None:
        raise _not_found()
    return _read_scan(scan.store_key), scan.content_type


# ---------------------------------------------------------------- the queue and reconcile

FILTERS = {
    "ready": {"status": Item.Status.PENDING},
    "awaiting_upload": {"status": Item.Status.RECORDED},
    "awaiting_verification": {"status": Item.Status.UPLOADED},
    "published": {"status": Item.Status.VERIFIED},
}


def queue(*, filter_name="ready", limit=100, today=None):
    """Items by state, oldest sale first, grouped by paid date, age counted from the sale date."""
    today = today or manila_date(timezone.now())
    if filter_name not in (*FILTERS, "exceptions", "all"):
        raise _invalid("filter", "Choose one of the listed filters.")
    limit = max(1, min(limit if isinstance(limit, int) and not isinstance(limit, bool) else 100, 200))
    rows = Item.objects.order_by("sale_date", "pk")
    if filter_name in FILTERS:
        rows = rows.filter(**FILTERS[filter_name])
    entries = []
    for item in rows.prefetch_related("invoices__book"):
        invoice = next((i for i in item.invoices.all() if i.status != "voided"), None)
        found = exceptions(item, invoice, today=today)
        if filter_name == "exceptions" and not found:
            continue
        entries.append((item, invoice, found))
        if len(entries) == limit:
            break
    groups = {}
    for item, invoice, found in entries:
        groups.setdefault(item.sale_date, []).append({
            "item": item.public_id, "status": item.status, "site": item.site_public_id,
            "site_name": item.site_name, "plan": item.plan_code,
            "total_centavos": item.total_centavos, "age_days": (today - item.sale_date).days,
            "is_test": item.is_test, "serial": serial_text(invoice) if invoice else None,
            "exceptions": found,
        })
    return {
        "notice": NOTICE,
        "filter": filter_name,
        "groups": [{"paid_date": d.isoformat(), "items": groups[d]} for d in sorted(groups)],
        "count": len(entries),
        "pending_count": Item.objects.filter(status=Item.Status.PENDING).count(),
    }


def reconcile(*, issued_from=None, issued_to=None):
    """Each live invoice against its payment, statement and adjustments. Read only."""
    rows = (
        ManualInvoice.objects.exclude(status=ManualInvoice.Status.VOIDED)
        .select_related("item__statement", "book").order_by("book__series", "serial_number")
    )
    if issued_from:
        rows = rows.filter(issued_on__gte=issued_from)
    if issued_to:
        rows = rows.filter(issued_on__lte=issued_to)
    out = []
    for invoice in rows:
        item = invoice.item
        statement = item.statement
        adjustments = (
            [{"number": c.number, "total_centavos": c.total_centavos} for c in statement.corrections.all()]
            if statement is not None else []
        )
        out.append({
            "serial": serial_text(invoice), "status": invoice.status,
            "issued_on": invoice.issued_on.isoformat(), "total_sales_centavos": invoice.total_sales_centavos,
            "amount_due_centavos": invoice.amount_due_centavos,
            "payment_date": item.sale_date.isoformat(), "paid_centavos": item.total_centavos,
            "site": item.site_public_id, "site_name": item.site_name, "item": item.public_id,
            "statement": statement.number if statement else None, "adjustments": adjustments,
            "exceptions": exceptions(item, invoice),
        })
    return {"items": out, "count": len(out)}


def detail(public_id):
    if not isinstance(public_id, str) or not public_id:
        raise _invalid("item", "Name the invoice item.")
    try:
        item = Item.objects.select_related("subscription__site__account").get(public_id=public_id)
    except (Item.DoesNotExist, TypeError, ValueError):
        raise _not_found() from None
    invoices = list(item.invoices.select_related("book").order_by("pk"))
    live = next((i for i in invoices if i.status != "voided"), None)
    books = InvoiceBook.objects.filter(active=True).order_by("series", "serial_from")
    return item, invoices, live, books
