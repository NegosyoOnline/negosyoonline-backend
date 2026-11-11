"""The books of the Financial Accounting System (design FAS-03, section 2.3).

Money is whole centavos. Nothing here writes a journal: the posting writer is a later piece.
The Django-level guards refuse what the books never allow; migration 0002 repeats the
important ones as PostgreSQL triggers.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.ids import new_public_id
from apps.ledger.managers import (
    AppendOnlyQuerySet,
    JournalEntryQuerySet,
    JournalLineQuerySet,
    LedgerRefusal,
    ProtectedQuerySet,
)

USER = settings.AUTH_USER_MODEL


def new_entity_id():
    return new_public_id("ent")


def new_gl_account_id():
    return new_public_id("gla")


def new_period_id():
    return new_public_id("per")


def new_journal_id():
    return new_public_id("jrn")


def new_event_id():
    return new_public_id("lev")


def _plain_user_fk(**kwargs):
    return models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+", **kwargs)


class AccountingEntity(models.Model):
    class Kind(models.TextChoices):
        LIVE = "live", "Live"
        REHEARSAL = "rehearsal", "Rehearsal"

    public_id = models.CharField(max_length=40, unique=True, default=new_entity_id, editable=False)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    books_start_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="PHP")
    first_fiscal_month = models.SmallIntegerField(default=1)
    next_posting_seq = models.BigIntegerField(default=1)
    last_seal = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = _plain_user_fk()

    objects = ProtectedQuerySet.as_manager()

    class Meta:
        db_table = "ledger_entity"
        constraints = [
            models.UniqueConstraint(fields=["name"], name="ledger_entity_name_unique"),
            models.UniqueConstraint(fields=["kind"], condition=Q(kind="live"), name="ledger_entity_one_live"),
            models.CheckConstraint(condition=Q(kind__in=["live", "rehearsal"]), name="ledger_entity_kind_valid"),
            models.CheckConstraint(condition=Q(currency="PHP"), name="ledger_entity_currency_php"),
            models.CheckConstraint(
                condition=Q(first_fiscal_month__gte=1, first_fiscal_month__lte=12),
                name="ledger_entity_first_month_range",
            ),
        ]

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_kind = instance.__dict__.get("kind")
        return instance

    def save(self, *args, **kwargs):
        if not self._state.adding and getattr(self, "_loaded_kind", self.kind) != self.kind:
            raise LedgerRefusal("a book's kind cannot change")
        super().save(*args, **kwargs)
        self._loaded_kind = self.kind

    def delete(self, *args, **kwargs):
        raise LedgerRefusal("ledger rows are never deleted")

    def __str__(self):
        return self.name


class GLAccount(models.Model):
    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        REVENUE = "revenue", "Revenue"
        EXPENSE = "expense", "Expense"

    class Side(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    public_id = models.CharField(max_length=40, unique=True, default=new_gl_account_id, editable=False)
    entity = models.ForeignKey(AccountingEntity, on_delete=models.PROTECT, related_name="accounts")
    system_key = models.CharField(max_length=40, blank=True, default="")
    code = models.CharField(max_length=16)
    name = models.CharField(max_length=160)
    type = models.CharField(max_length=10, choices=Type.choices)
    normal_side = models.CharField(max_length=6, choices=Side.choices)
    is_contra = models.BooleanField(default=False)
    reporting_group = models.CharField(max_length=60, blank=True, default="")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    is_postable = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default="active")

    objects = ProtectedQuerySet.as_manager()

    class Meta:
        db_table = "ledger_account"
        constraints = [
            models.UniqueConstraint(fields=["entity", "code"], name="ledger_account_code_unique"),
            models.UniqueConstraint(
                fields=["entity", "system_key"],
                condition=~Q(system_key=""),
                name="ledger_account_system_key_unique",
            ),
            models.CheckConstraint(
                condition=Q(type__in=["asset", "liability", "equity", "revenue", "expense"]),
                name="ledger_account_type_valid",
            ),
            models.CheckConstraint(condition=Q(status__in=["active", "retired"]), name="ledger_account_status_valid"),
            models.CheckConstraint(
                condition=(
                    (Q(type__in=["asset", "expense"], is_contra=False, normal_side="debit"))
                    | (Q(type__in=["asset", "expense"], is_contra=True, normal_side="credit"))
                    | (Q(type__in=["liability", "equity", "revenue"], is_contra=False, normal_side="credit"))
                    | (Q(type__in=["liability", "equity", "revenue"], is_contra=True, normal_side="debit"))
                ),
                name="ledger_account_side_valid",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True)
                | Q(effective_from__isnull=True)
                | Q(effective_to__gte=models.F("effective_from")),
                name="ledger_account_effective_order",
            ),
        ]

    def delete(self, *args, **kwargs):
        raise LedgerRefusal("ledger rows are never deleted")

    def __str__(self):
        return f"{self.code} {self.name}"


class FiscalPeriod(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        SOFT_CLOSED = "soft_closed", "Soft closed"
        LOCKED = "locked", "Locked"

    public_id = models.CharField(max_length=40, unique=True, default=new_period_id, editable=False)
    entity = models.ForeignKey(AccountingEntity, on_delete=models.PROTECT, related_name="periods")
    year = models.SmallIntegerField()
    month = models.SmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=11, choices=Status.choices, default="open")
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = _plain_user_fk(null=True, blank=True)

    objects = ProtectedQuerySet.as_manager()

    class Meta:
        db_table = "ledger_period"
        constraints = [
            models.UniqueConstraint(fields=["entity", "year", "month"], name="ledger_period_unique"),
            models.CheckConstraint(condition=Q(month__gte=1, month__lte=12), name="ledger_period_month_range"),
            models.CheckConstraint(
                condition=Q(end_date__gte=models.F("start_date")), name="ledger_period_dates_order"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["open", "soft_closed", "locked"]), name="ledger_period_status_valid"
            ),
        ]

    def delete(self, *args, **kwargs):
        raise LedgerRefusal("ledger rows are never deleted")

    def __str__(self):
        return f"{self.year}-{self.month:02d}"


class JournalEntry(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    class Kind(models.TextChoices):
        NORMAL = "normal", "Normal"
        OPENING = "opening", "Opening"
        REVERSAL = "reversal", "Reversal"
        ADJUSTMENT = "adjustment", "Adjustment"

    public_id = models.CharField(max_length=40, unique=True, default=new_journal_id, editable=False)
    entity = models.ForeignKey(AccountingEntity, on_delete=models.PROTECT, related_name="entries")
    status = models.CharField(max_length=8, choices=Status.choices, default="draft")
    kind = models.CharField(max_length=10, choices=Kind.choices, default="normal")
    reason = models.TextField(blank=True, default="")
    rule_version = models.CharField(max_length=8, blank=True, default="")
    source_detail = models.JSONField(default=dict)
    entry_date = models.DateField()
    memo = models.CharField(max_length=240, blank=True, default="")
    period = models.ForeignKey(FiscalPeriod, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    posting_seq = models.BigIntegerField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = _plain_user_fk(null=True, blank=True)
    created_by = _plain_user_fk(null=True, blank=True)
    idempotency_key = models.CharField(max_length=160)
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    source_type = models.CharField(max_length=40, blank=True, default="")
    source_ref = models.CharField(max_length=64, blank=True, default="")
    source_event = models.CharField(max_length=40, blank=True, default="")
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by"
    )
    total_centavos = models.BigIntegerField(default=0)
    seal = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = JournalEntryQuerySet.as_manager()

    class Meta:
        db_table = "ledger_entry"
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["draft", "posted"]), name="ledger_entry_status_valid"),
            models.CheckConstraint(
                condition=Q(kind__in=["normal", "opening", "reversal", "adjustment"]),
                name="ledger_entry_kind_valid",
            ),
            models.UniqueConstraint(fields=["entity", "idempotency_key"], name="ledger_entry_idem_unique"),
            models.CheckConstraint(condition=~Q(idempotency_key=""), name="ledger_entry_idem_not_blank"),
            models.UniqueConstraint(
                fields=["entity", "source_type", "source_ref", "source_event"],
                condition=~Q(source_type=""),
                name="ledger_entry_source_unique",
            ),
            models.UniqueConstraint(
                fields=["entity", "posting_seq"],
                condition=Q(posting_seq__isnull=False),
                name="ledger_entry_seq_unique",
            ),
            models.UniqueConstraint(
                fields=["entity"], condition=Q(kind="opening"), name="ledger_entry_one_opening"
            ),
            models.CheckConstraint(condition=Q(total_centavos__gte=0), name="ledger_entry_total_nonneg"),
            models.CheckConstraint(
                condition=~Q(reason="") | (Q(kind="normal") & ~Q(source_type="")),
                name="ledger_entry_reason_required",
            ),
            models.CheckConstraint(
                condition=(Q(kind="reversal") & Q(reverses__isnull=False))
                | (~Q(kind="reversal") & Q(reverses__isnull=True)),
                name="ledger_entry_reversal_link",
            ),
            models.CheckConstraint(
                condition=Q(status="draft")
                | (
                    Q(period__isnull=False, posting_seq__isnull=False, posted_at__isnull=False)
                    & ~Q(seal="")
                    & Q(total_centavos__gt=0)
                ),
                name="ledger_entry_posted_complete",
            ),
        ]
        indexes = [
            models.Index(fields=["entity", "entry_date"], name="ledger_entry_date_idx"),
            models.Index(fields=["entity", "status"], name="ledger_entry_status_idx"),
            models.Index(fields=["period"], name="ledger_entry_period_idx"),
        ]

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_status = instance.__dict__.get("status")
        return instance

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        self._loaded_status = self.__dict__.get("status")

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.status != "draft":
                raise LedgerRefusal("an entry is created as a draft")
        elif getattr(self, "_loaded_status", None) == "posted":
            raise LedgerRefusal("a posted entry cannot be changed")
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    def delete(self, *args, **kwargs):
        if getattr(self, "_loaded_status", None) != "draft":
            raise LedgerRefusal("only a draft entry can be deleted")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.public_id


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    line_no = models.SmallIntegerField()
    account = models.ForeignKey(GLAccount, on_delete=models.PROTECT, related_name="lines")
    debit_centavos = models.BigIntegerField(default=0)
    credit_centavos = models.BigIntegerField(default=0)
    memo = models.CharField(max_length=240, blank=True, default="")
    evidence_ref = models.CharField(max_length=120, blank=True, default="")

    objects = JournalLineQuerySet.as_manager()

    class Meta:
        db_table = "ledger_line"
        constraints = [
            models.UniqueConstraint(fields=["entry", "line_no"], name="ledger_line_no_unique"),
            models.CheckConstraint(condition=Q(line_no__gte=1), name="ledger_line_no_positive"),
            models.CheckConstraint(
                condition=(Q(debit_centavos__gt=0, credit_centavos=0) | Q(credit_centavos__gt=0, debit_centavos=0)),
                name="ledger_line_one_side",
            ),
        ]
        indexes = [models.Index(fields=["account", "entry"], name="ledger_line_acct_entry_idx")]

    def _parent_status(self):
        return JournalEntry.objects.filter(pk=self.entry_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if self._parent_status() != "draft":
            raise LedgerRefusal("the lines of a posted entry cannot be changed")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._parent_status() != "draft":
            raise LedgerRefusal("the lines of a posted entry cannot be changed")
        return super().delete(*args, **kwargs)


class RecognitionSchedule(models.Model):
    entity = models.ForeignKey(AccountingEntity, on_delete=models.PROTECT, related_name="recognitions")
    payment_ref = models.CharField(max_length=64)
    period_month = models.CharField(max_length=7)
    service_start = models.DateField()
    service_end = models.DateField()
    amount_to_release_centavos = models.BigIntegerField()
    payment_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="recognitions")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "ledger_recognition"
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "payment_ref", "period_month"], name="ledger_recognition_unique"
            ),
            models.CheckConstraint(
                condition=Q(amount_to_release_centavos__gt=0), name="ledger_recognition_amount_positive"
            ),
            models.CheckConstraint(
                condition=Q(service_end__gt=models.F("service_start")), name="ledger_recognition_service_order"
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise LedgerRefusal("ledger rows are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise LedgerRefusal("ledger rows are append-only")


class LedgerEvent(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_event_id, editable=False)
    entity = models.ForeignKey(AccountingEntity, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=40)
    target_type = models.CharField(max_length=24)
    target_ref = models.CharField(max_length=64)
    actor = _plain_user_fk(null=True, blank=True)
    detail = models.JSONField(default=dict)
    at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "ledger_event"
        indexes = [models.Index(fields=["at"], name="ledger_event_at_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise LedgerRefusal("ledger rows are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise LedgerRefusal("ledger rows are append-only")


class LedgerAccess(models.Model):
    class Level(models.TextChoices):
        OWNER = "owner", "Owner"
        VIEWER = "viewer", "Viewer"

    operator = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="ledger_access")
    entity = models.ForeignKey(AccountingEntity, on_delete=models.PROTECT, related_name="access_rows")
    level = models.CharField(max_length=8, choices=Level.choices)
    granted_by = _plain_user_fk()
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = _plain_user_fk(null=True, blank=True)

    class Meta:
        db_table = "ledger_access"
        constraints = [
            models.UniqueConstraint(fields=["operator", "entity"], name="ledger_access_one_per_user_book"),
            models.CheckConstraint(condition=Q(level__in=["owner", "viewer"]), name="ledger_access_level_valid"),
        ]
