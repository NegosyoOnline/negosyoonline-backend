"""The manual BIR invoice register (Billing Build B5).

Coach Ed handwrites a BIR-registered Non-VAT invoice for each paid sale. This app holds the
admin-only record of that: a pending item per confirmed payment, the invoice as written on
paper, its scan and an append-only event trail. The system never issues an official invoice,
never assigns or advances a printed serial and never backdates. Rows are kept five years
(L76) and nothing here has a delete path. Write only through the services of this app.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.common.ids import new_public_id
from apps.statements.managers import (
    AppendOnlyMixin, AppendOnlyQuerySet, FrozenFieldsMixin, GuardedQuerySet, Immutable,
    KeptQuerySet,
)


def new_item_id():
    return new_public_id("mbi")


def new_invoice_id():
    return new_public_id("min")


def new_scan_id():
    return new_public_id("scn")


class InvoiceBook(FrozenFieldsMixin, models.Model):
    """One paper book of preprinted invoices, copied once by staff as evidence. The system
    flags a serial outside the range and shows the next expected number as a hint only."""

    issuer = models.ForeignKey(
        "statements.IssuerProfile", on_delete=models.PROTECT, related_name="books"
    )
    # Booklets under one ATP may share a series, or have none, with consecutive serial ranges.
    # So a series is not unique; two books of one series may not overlap (see `save`).
    series = models.CharField(max_length=20, blank=True)
    serial_from = models.PositiveIntegerField()
    serial_to = models.PositiveIntegerField()
    serial_width = models.PositiveSmallIntegerField(default=1)
    atp_reference = models.CharField(max_length=40)
    atp_date = models.DateField()
    printer_name = models.CharField(max_length=160, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    MOVES = ("active",)  # everything else is evidence, entered once

    objects = GuardedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(serial_to__gte=F("serial_from")), name="invoices_book_range_ordered"
            ),
        ]

    def overlapping(self):
        """Other books of the same series whose serial range crosses this one's."""
        return InvoiceBook.objects.filter(
            series=self.series, serial_from__lte=self.serial_to, serial_to__gte=self.serial_from
        ).exclude(pk=self.pk)

    def save(self, *args, **kwargs):
        # A service-level rule, not a database constraint: an exclusion constraint needs a
        # PostgreSQL extension and does not exist on SQLite. The staff service that records a
        # book locks the issuer row first, so two requests cannot both pass this check.
        if self._state.adding and self.overlapping().exists():
            raise Immutable("That serial range overlaps another book of the same series.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invoice book {self.series or '(no series)'}"


class ManualInvoiceItem(FrozenFieldsMixin, models.Model):
    """The "BIR invoice pending manual issuance" item of one confirmed payment. Created in
    the confirmation's transaction. Its snapshot never changes; only `status` and
    `statement` move."""

    MOVES = ("status", "statement")

    class Status(models.TextChoices):
        PENDING = "pending"
        RECORDED = "recorded"
        UPLOADED = "uploaded"
        VERIFIED = "verified"

    # Where the sale came from. The gateway (B2) adds its own value.
    class SourceKind(models.TextChoices):
        BANK_TRANSFER = "bank_transfer"

    _FLOW = {
        "pending": {"recorded"},
        "recorded": {"uploaded", "pending"},  # a void returns the item to pending
        "uploaded": {"verified", "recorded", "pending"},  # a rejected scan returns to recorded
        "verified": {"pending"},  # only a void of the verified invoice
    }

    public_id = models.CharField(max_length=40, unique=True, default=new_item_id, editable=False)
    subscription = models.ForeignKey(
        "billing.Subscription", on_delete=models.PROTECT, related_name="invoice_items"
    )
    # Null only for a usage-only payment, which settles charge lines and has no cycle.
    cycle = models.ForeignKey(
        "billing.BillingCycle", null=True, blank=True, on_delete=models.PROTECT,
        related_name="invoice_items",
    )
    site_public_id = models.CharField(max_length=40)
    site_name = models.CharField(max_length=160)
    source_kind = models.CharField(max_length=20, choices=SourceKind.choices)
    source_ref = models.CharField(max_length=64)  # the confirmed payment's public id
    plan_code = models.CharField(max_length=40)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    total_centavos = models.BigIntegerField()
    lines = models.JSONField()  # frozen: description, quantity, unit, amount
    paid_at = models.DateTimeField()
    sale_date = models.DateField()  # the Manila date of paid_at
    tax_year = models.PositiveSmallIntegerField()
    statement = models.ForeignKey(
        "statements.Statement", null=True, blank=True, on_delete=models.PROTECT,
        related_name="invoice_items",
    )
    is_test = models.BooleanField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = GuardedQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]
        constraints = [
            # Exactly one pending item per confirmed payment.
            models.UniqueConstraint(
                fields=["source_kind", "source_ref"], name="invoices_item_once_per_payment"
            ),
            models.CheckConstraint(
                condition=Q(total_centavos__gt=0), name="invoices_item_total_positive"
            ),
        ]

    def allowed(self, old):
        if old.statement_id is not None and self.statement_id != old.statement_id:
            return False  # the statement is linked once
        if self.status == old.status:
            return True
        return self.status in self._FLOW.get(old.status, set())

    def __str__(self):
        return f"Invoice item {self.public_id}"


class ManualInvoice(FrozenFieldsMixin, models.Model):
    """The invoice as handwritten on paper, typed in by an operator. Once verified, only a
    void may change it."""

    MOVES = ("status", "void_reason", "verified_at", "verified_by")

    class Status(models.TextChoices):
        RECORDED = "recorded"
        UPLOADED = "uploaded"
        VERIFIED = "verified"
        VOIDED = "voided"

    class SaleType(models.TextChoices):
        CASH = "cash"
        CHARGE = "charge"

    class DiscountKind(models.TextChoices):
        SENIOR = "senior"
        PWD = "pwd"
        OTHER = "other"

    _FLOW = {
        "recorded": {"uploaded", "voided"},
        "uploaded": {"verified", "recorded", "voided"},
        "verified": {"voided"},
        "voided": set(),
    }

    public_id = models.CharField(max_length=40, unique=True, default=new_invoice_id, editable=False)
    item = models.ForeignKey(ManualInvoiceItem, on_delete=models.PROTECT, related_name="invoices")
    book = models.ForeignKey(InvoiceBook, on_delete=models.PROTECT, related_name="invoices")
    serial_number = models.PositiveIntegerField()  # typed from the paper, never assigned here
    issued_on = models.DateField()  # the actual issue date: no default
    sale_type = models.CharField(max_length=10, choices=SaleType.choices, default=SaleType.CASH)
    buyer_name = models.CharField(max_length=160)
    buyer_tin = models.CharField(max_length=17, blank=True)
    buyer_address = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=300)
    service_start = models.DateField()
    service_end = models.DateField()
    total_sales_centavos = models.BigIntegerField()
    discount_centavos = models.BigIntegerField(default=0)
    discount_kind = models.CharField(max_length=10, choices=DiscountKind.choices, blank=True)
    withholding_centavos = models.BigIntegerField(default=0)
    amount_due_centavos = models.BigIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECORDED)
    void_reason = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )

    objects = GuardedQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["book", "serial_number"], name="invoices_serial_once_per_book"
            ),
            # At most one live invoice per item; a void frees the item for a replacement.
            models.UniqueConstraint(
                fields=["item"], condition=~Q(status="voided"), name="invoices_one_live_per_item"
            ),
            models.CheckConstraint(
                condition=Q(
                    amount_due_centavos=F("total_sales_centavos")
                    - F("discount_centavos")
                    - F("withholding_centavos")
                ),
                name="invoices_amount_due_arithmetic",
            ),
            models.CheckConstraint(
                condition=Q(discount_centavos__gt=0) | Q(discount_kind=""),
                name="invoices_discount_kind_needs_discount",
            ),
            models.CheckConstraint(
                condition=Q(service_end__gte=F("service_start")),
                name="invoices_service_ordered",
            ),
        ]

    def allowed(self, old):
        if self.status == old.status:
            # No status move, no other move: a verified invoice changes only by a void.
            return all(
                getattr(self, f.attname) == getattr(old, f.attname)
                for f in self._meta.concrete_fields if f.name in self.MOVES
            )
        return self.status in self._FLOW.get(old.status, set())

    def __str__(self):
        return f"Invoice {self.public_id}"


class InvoiceScan(FrozenFieldsMixin, models.Model):
    """The scan of the paper invoice. A rejected scan stays; a new one is uploaded. A
    verified scan is frozen. The file lives in the private store and is never a URL."""

    MOVES = ("status", "reject_reason", "decided_at", "decided_by")

    class Status(models.TextChoices):
        UPLOADED = "uploaded"
        VERIFIED = "verified"
        REJECTED = "rejected"

    class RejectReason(models.TextChoices):
        UNREADABLE = "unreadable"
        WRONG_INVOICE = "wrong_invoice"
        AMOUNT_MISMATCH = "amount_mismatch"
        OTHER = "other"

    public_id = models.CharField(max_length=40, unique=True, default=new_scan_id, editable=False)
    invoice = models.ForeignKey(ManualInvoice, on_delete=models.PROTECT, related_name="scans")
    store_key = models.CharField(max_length=64)
    content_type = models.CharField(max_length=40)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UPLOADED)
    reject_reason = models.CharField(max_length=20, choices=RejectReason.choices, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )

    objects = GuardedQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]
        constraints = [
            # One live (not rejected) scan per invoice.
            models.UniqueConstraint(
                fields=["invoice"], condition=~Q(status="rejected"),
                name="invoices_one_live_scan_per_invoice",
            ),
            # One file cannot back two serials.
            models.UniqueConstraint(
                fields=["sha256"], condition=~Q(status="rejected"),
                name="invoices_scan_hash_once",
            ),
            models.CheckConstraint(
                condition=~Q(status="rejected") | ~Q(reject_reason=""),
                name="invoices_rejected_has_reason",
            ),
        ]

    def allowed(self, old):
        # A scan is decided once; a verified or rejected scan never changes again.
        if old.status != "uploaded":
            return all(
                getattr(self, f.attname) == getattr(old, f.attname)
                for f in self._meta.concrete_fields if f.name in self.MOVES
            )
        return True

    def __str__(self):
        return f"Scan {self.public_id}"


class InvoiceEvent(AppendOnlyMixin, models.Model):
    """The append-only trail of an item. With OperatorAuditEntry it is the immutable audit
    trail after verification."""

    class Code(models.TextChoices):
        OPENED = "opened"
        RECORDED = "recorded"
        SCAN_UPLOADED = "scan_uploaded"
        SCAN_REJECTED = "scan_rejected"
        VERIFIED = "verified"
        VOIDED = "voided"
        STATEMENT_LINKED = "statement_linked"

    item = models.ForeignKey(ManualInvoiceItem, on_delete=models.PROTECT, related_name="events")
    invoice = models.ForeignKey(
        ManualInvoice, null=True, blank=True, on_delete=models.PROTECT, related_name="events"
    )
    code = models.CharField(max_length=20, choices=Code.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    idempotency_key = models.CharField(max_length=120, unique=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    detail = models.JSONField(default=dict, blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.code} on item {self.item_id}"
