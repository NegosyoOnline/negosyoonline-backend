"""Internal Billing Statements and what they are issued from (Billing Build B5).

A statement is a record NegosyoOnline makes for itself and its Subscriber. It is never an
official BIR receipt or invoice. Statements, their lines and their numbers are insert-only:
a correction is a separate document that points at the original. Rows are kept five years
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

# The exact label on every system-generated document (task, "Settled rules").
LABEL = "Internal Billing Statement — Not an Official BIR Receipt or BIR Electronic Invoice."


def new_issuer_id():
    return new_public_id("iss")


def new_statement_id():
    return new_public_id("stm")


class IssuerProfile(FrozenFieldsMixin, models.Model):
    """Who issues the documents, as the certificate of registration (COR) shows it.

    Versioned: a change is a new row, so a statement's snapshot never depends on a row that
    was edited later. A row can be verified once; the newest verified row is the current
    issuer. The values are entered by staff, never put in code, a seed or a migration.
    """

    MOVES = ("verified_at", "verified_by", "verification_note")

    class TaxType(models.TextChoices):
        NON_VAT = "non_vat"

    public_id = models.CharField(
        max_length=40, unique=True, default=new_issuer_id, editable=False
    )
    legal_name = models.CharField(max_length=160)
    trade_name = models.CharField(max_length=160, blank=True)
    tin = models.CharField(max_length=17)
    branch_code = models.CharField(max_length=10)
    registered_address = models.CharField(max_length=255)
    tax_type = models.CharField(max_length=10, choices=TaxType.choices, default=TaxType.NON_VAT)
    cor_number = models.CharField(max_length=40)
    cor_date = models.DateField()
    contact_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    verification_note = models.CharField(max_length=300, blank=True)

    objects = GuardedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(verified_at__isnull=True) | Q(verified_by__isnull=False),
                name="statements_issuer_verified_by",
            ),
        ]

    def allowed(self, old):
        # Verified once, never unverified or re-verified.
        return old.verified_at is None

    def __str__(self):
        return f"Issuer profile {self.public_id}"


class BillingProfile(models.Model):
    """The business details a Subscriber wants on their statements. Optional: with none the
    statement falls back to the account's name and email. Changing it changes no issued
    statement, which keeps the snapshot it was issued with."""

    site = models.OneToOneField(
        "sites.Site", on_delete=models.PROTECT, related_name="billing_profile"
    )
    company_name = models.CharField(max_length=160)
    billing_email = models.EmailField()
    address = models.CharField(max_length=255, blank=True)
    tin = models.CharField(max_length=17, blank=True)  # only when the Subscriber supplied it
    updated_at = models.DateTimeField(auto_now=True)

    objects = KeptQuerySet.as_manager()

    def delete(self, *args, **kwargs):
        raise Immutable("A billing record is kept and cannot be deleted.")

    def __str__(self):
        return f"Billing profile of site {self.site_id}"


class StatementNumberSeries(models.Model):
    """The last number taken in one prefix and year. Locked with `select_for_update` inside
    the issuing transaction, so a number and its statement commit together or not at all."""

    prefix = models.CharField(max_length=4)  # NOBS for a statement, NOCN for an adjustment
    year = models.PositiveSmallIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    objects = KeptQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["prefix", "year"], name="statements_series_once"),
        ]

    def delete(self, *args, **kwargs):
        raise Immutable("A billing record is kept and cannot be deleted.")

    def __str__(self):
        return f"{self.prefix}-{self.year}"


class Statement(AppendOnlyMixin, models.Model):
    """One issued document. Insert-only."""

    class DocumentType(models.TextChoices):
        STATEMENT = "statement"
        ADJUSTMENT = "adjustment"  # a reduction; an increase is a new statement

    class Kind(models.TextChoices):
        MONTHLY = "monthly"  # the base with prior-cycle usage
        ANNUAL = "annual"
        LAUNCH_PROMO = "launch_promo"
        USAGE_ONLY = "usage_only"
        FINAL_USAGE = "final_usage"

    public_id = models.CharField(
        max_length=40, unique=True, default=new_statement_id, editable=False
    )
    number = models.CharField(max_length=20, unique=True)  # NOBS-YYYY-######, NOCN-YYYY-######
    document_type = models.CharField(max_length=10, choices=DocumentType.choices)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    subscription = models.ForeignKey(
        "billing.Subscription", on_delete=models.PROTECT, related_name="statements"
    )
    # Null for a usage-only or final-usage statement, which settles charge lines, not a cycle.
    cycle = models.ForeignKey(
        "billing.BillingCycle", null=True, blank=True, on_delete=models.PROTECT,
        related_name="statements",
    )
    # What the statement was issued for: payment:<id>:<kind>, adjustment:<original>:<n>.
    # Unique, so a second issue for the same event returns the first.
    source_key = models.CharField(max_length=120, unique=True)
    original = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrections"
    )
    adjustment_reason = models.CharField(max_length=40, blank=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    currency = models.CharField(max_length=3, default="PHP")
    total_centavos = models.BigIntegerField()  # signed: an adjustment is negative
    is_test = models.BooleanField()
    issuer = models.JSONField()  # the snapshot of the verified issuer profile
    bill_to = models.JSONField()  # name, billing email, address, TIN only when supplied
    payment_reference = models.CharField(max_length=64, blank=True)
    label = models.CharField(max_length=200, default=LABEL)
    pdf_key = models.CharField(max_length=64)
    pdf_sha256 = models.CharField(max_length=64)
    pdf_size = models.PositiveIntegerField()
    issued_at = models.DateTimeField()

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(document_type="adjustment", original__isnull=False)
                | Q(document_type="statement"),
                name="statements_adjustment_has_original",
            ),
            # A statement for a paid term points at its cycle.
            models.CheckConstraint(
                condition=Q(document_type="adjustment")
                | ~Q(kind__in=["monthly", "annual", "launch_promo"])
                | Q(cycle__isnull=False),
                name="statements_cycle_kind_has_cycle",
            ),
            models.CheckConstraint(
                condition=Q(period_end__gte=F("period_start")),
                name="statements_period_ordered",
            ),
            models.CheckConstraint(
                condition=Q(document_type="adjustment", total_centavos__lt=0)
                | Q(document_type="statement", total_centavos__gte=0),
                name="statements_total_sign",
            ),
        ]

    def __str__(self):
        return self.number


class StatementLine(AppendOnlyMixin, models.Model):
    """One line of a statement, frozen from the charge sources. Insert-only."""

    class Kind(models.TextChoices):
        BASE = "base"
        USAGE = "usage"
        CREDIT = "credit"  # the Subscription Credit; always zero until Billing Build B6
        ADJUSTMENT = "adjustment"

    statement = models.ForeignKey(Statement, on_delete=models.PROTECT, related_name="lines")
    position = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    description = models.CharField(max_length=300)
    quantity = models.PositiveIntegerField(default=1)
    unit_centavos = models.BigIntegerField()
    amount_centavos = models.BigIntegerField()  # signed
    source_ref = models.CharField(max_length=64, blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["statement_id", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["statement", "position"], name="statements_line_position_once"
            ),
        ]

    def __str__(self):
        return f"Line {self.position} of statement {self.statement_id}"


class StatementDelivery(models.Model):
    """One attempt to deliver a statement. The only statement table that changes: delivery is
    not part of the document. It is kept like any billing record."""

    class Channel(models.TextChoices):
        EMAIL = "email"

    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"

    statement = models.ForeignKey(Statement, on_delete=models.PROTECT, related_name="deliveries")
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.EMAIL)
    to_email = models.EmailField()  # a snapshot of the billing email at the attempt
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempt = models.PositiveSmallIntegerField()
    error_code = models.CharField(max_length=40, blank=True)  # a code, never a message
    idempotency_key = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    objects = KeptQuerySet.as_manager()

    def delete(self, *args, **kwargs):
        raise Immutable("A billing record is kept and cannot be deleted.")

    def __str__(self):
        return f"Delivery {self.attempt} of statement {self.statement_id}"
