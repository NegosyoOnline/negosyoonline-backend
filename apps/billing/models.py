"""One subscription per site, its billing cycles, the record of every state change, and the
bank-transfer payments that settle a cycle.

Other apps reach these tables only through apps.billing.services.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.common.ids import new_public_id
from apps.statements.managers import AppendOnlyMixin, AppendOnlyQuerySet


class Subscription(models.Model):
    class State(models.TextChoices):
        TRIAL_ACTIVE = "trial_active"
        ACTIVE = "active"
        PAYMENT_REQUIRES_ATTENTION = "payment_requires_attention"
        SUSPENDED = "suspended"
        CANCELLED = "cancelled"
        CANCELLED_NO_PAYMENT_METHOD = "cancelled_no_payment_method"

    class MethodAtTrialEnd(models.TextChoices):
        UNDECIDED = "undecided"
        YES = "yes"
        NO = "no"

    site = models.OneToOneField("sites.Site", on_delete=models.PROTECT, related_name="subscription")
    # The state last written by a transition. Service is decided by services.position, which
    # also reads the time; never by this field on its own.
    state = models.CharField(max_length=30, choices=State.choices)
    trial_start_at = models.DateTimeField()
    trial_end_at = models.DateTimeField()
    # Decided once, at the trial-end instant, and never changed.
    method_at_trial_end = models.CharField(
        max_length=10, choices=MethodAtTrialEnd.choices, default=MethodAtTrialEnd.UNDECIDED
    )
    anchor_day = models.PositiveSmallIntegerField(null=True, blank=True)
    attention_started_at = models.DateTimeField(null=True, blank=True)
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    service_ended_at = models.DateTimeField(null=True, blank=True)
    download_until = models.DateTimeField(null=True, blank=True)
    # Also the last date to pay. Recorded only: nothing here deletes.
    deletion_due_at = models.DateTimeField(null=True, blank=True)
    billing_exempt = models.BooleanField(default=False)  # set once, never cleared
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(trial_end_at__gt=F("trial_start_at")),
                name="billing_trial_end_after_start",
            ),
        ]

    def __str__(self):
        return f"Subscription of site {self.site_id}"


class SubscriptionTransition(models.Model):
    """Append-only. One row per state change, written once under its idempotency key."""

    class Actor(models.TextChoices):
        SITE_CREATION = "site_creation"
        BACKFILL = "backfill"
        SWEEP = "sweep"
        OWNER = "owner"
        PAYMENT = "payment"
        OPERATOR = "operator"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="transitions"
    )
    code = models.CharField(max_length=40)
    from_state = models.CharField(max_length=30, blank=True)
    to_state = models.CharField(max_length=30, choices=Subscription.State.choices)
    actor = models.CharField(max_length=20, choices=Actor.choices)
    idempotency_key = models.CharField(max_length=120, unique=True)
    effective_at = models.DateTimeField()  # when the change took effect by rule
    recorded_at = models.DateTimeField(auto_now_add=True)  # when this row was written
    detail = models.JSONField(default=dict, blank=True)  # cycle sequence, our reference

    def __str__(self):
        return f"{self.code} on subscription {self.subscription_id}"


class BillingCycle(models.Model):
    class Kind(models.TextChoices):
        TRIAL = "trial"
        PAID = "paid"

    class Settlement(models.TextChoices):
        NOT_BILLABLE = "not_billable"
        PENDING = "pending"
        PAID = "paid"
        LAPSED = "lapsed"

    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="cycles")
    sequence = models.PositiveIntegerField()  # the trial is 0; paid cycles count from 1
    kind = models.CharField(max_length=10, choices=Kind.choices)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    settlement = models.CharField(max_length=15, choices=Settlement.choices)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=64, blank=True)
    # The plan, price and term this cycle was opened under (Billing Build B3). A cycle is
    # never re-priced from the current plan: its price is read from here. A trial cycle has
    # none. A pending or lapsed cycle holds the terms that were due.
    plan_code = models.CharField(max_length=20, blank=True)
    plan_version = models.PositiveSmallIntegerField(null=True, blank=True)
    price_centavos = models.PositiveIntegerField(null=True, blank=True)
    term_months = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "sequence"], name="billing_cycle_sequence_once"
            ),
            models.CheckConstraint(
                condition=Q(end_at__gt=F("start_at")), name="billing_cycle_end_after_start"
            ),
            # Every cycle but the trial says what it was opened under (Billing Build B3).
            models.CheckConstraint(
                condition=Q(kind="trial") | (
                    ~Q(plan_code="")
                    & Q(plan_version__isnull=False)
                    & Q(price_centavos__isnull=False)
                    & Q(term_months__isnull=False)
                ),
                name="billing_paid_cycle_has_snapshot",
            ),
            models.UniqueConstraint(
                fields=["subscription"],
                condition=Q(settlement="pending"),
                name="billing_one_pending_cycle",
            ),
        ]

    def __str__(self):
        return f"Cycle {self.sequence} of subscription {self.subscription_id}"


def new_transfer_id():
    return new_public_id("btp")


class BankTransferPayment(models.Model):
    """One proof of a bank transfer for a subscription, and what an operator decided about it.

    Its plan and amount are frozen when it is sent. Only a confirmation settles a cycle. Rows
    are kept: nothing deletes or blanks them. Write only through apps.billing.services.
    """

    class Status(models.TextChoices):
        SUBMITTED = "submitted"  # waiting for an operator
        CONFIRMED = "confirmed"
        REJECTED = "rejected"
        REPLACED = "replaced"  # a newer proof took its place before a decision

    class RejectReason(models.TextChoices):
        UNREADABLE = "unreadable"
        AMOUNT_MISMATCH = "amount_mismatch"
        NOT_RECEIVED = "not_received"
        WRONG_ACCOUNT = "wrong_account"
        DUPLICATE = "duplicate"
        OTHER = "other"

    public_id = models.CharField(
        max_length=40, unique=True, default=new_transfer_id, editable=False
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="transfers"
    )
    # Our own reference: the one the lifecycle's payment functions are keyed on.
    reference = models.CharField(max_length=64, unique=True)
    plan_code = models.CharField(max_length=20)
    plan_version = models.PositiveSmallIntegerField(null=True, blank=True)
    term_months = models.PositiveSmallIntegerField(null=True, blank=True)
    amount_centavos = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="PHP")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUBMITTED)
    transferred_on = models.DateField()  # the day the Subscriber says the transfer was made
    transfer_reference = models.CharField(max_length=64, blank=True)  # the bank's own number
    # What the Subscriber typed about the transfer. Blank or null only on rows written before
    # these were asked for. The amount typed may differ from `amount_centavos`; staff see it.
    sender_bank = models.CharField(max_length=80, blank=True)
    sender_first_name = models.CharField(max_length=80, blank=True)
    sender_last_name = models.CharField(max_length=80, blank=True)
    sender_account_number = models.CharField(max_length=34, blank=True)
    amount_sent_centavos = models.PositiveIntegerField(null=True, blank=True)
    transferred_at = models.DateTimeField(null=True, blank=True)  # `transferred_on` is its Manila date
    proof_key = models.CharField(max_length=64)
    proof_content_type = models.CharField(max_length=20)
    proof_size = models.PositiveIntegerField()
    proof_sha256 = models.CharField(max_length=64)
    submitted_by = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )
    submitted_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    amount_received_centavos = models.PositiveIntegerField(null=True, blank=True)
    reject_reason = models.CharField(max_length=20, choices=RejectReason.choices, blank=True)
    reject_note = models.CharField(max_length=300, blank=True)
    cycle = models.OneToOneField(
        BillingCycle, null=True, blank=True, on_delete=models.PROTECT, related_name="transfer"
    )
    # The overage inside `amount_centavos` and the highest charge line it covers (Billing B4).
    usage_centavos = models.PositiveIntegerField(default=0)
    usage_line_max_id = models.PositiveBigIntegerField(default=0)
    # Subscription Credit taken off `amount_centavos` when the payment was sent (Billing B6).
    # The Subscriber transfers `amount_centavos - credit_centavos`. Zero while the referral
    # switch is off, and on every payment written before it existed.
    credit_centavos = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription"],
                condition=Q(status="submitted"),
                name="billing_one_transfer_under_review",
            ),
            models.CheckConstraint(
                condition=Q(credit_centavos__lte=F("amount_centavos")),
                name="billing_transfer_credit_within_amount",
            ),
            # A confirmed payment settled a cycle (a usage-only one settles charge lines
            # instead), was decided by an operator (a bill the credit covers in full has none:
            # no money moved, so no one confirmed it), and the amount received is the amount due
            # after the credit.
            models.CheckConstraint(
                condition=~Q(status="confirmed") | (
                    (Q(cycle__isnull=False) | Q(plan_code="usage_only"))
                    & (Q(decided_by__isnull=False) | Q(credit_centavos=F("amount_centavos")))
                    & Q(amount_received_centavos=F("amount_centavos") - F("credit_centavos"))
                ),
                name="billing_transfer_confirmed_in_full",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self.public_id
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("A payment record is kept and cannot be deleted.")

    def __str__(self):
        return f"Bank transfer {self.public_id}"


class BankTransferEvent(models.Model):
    """Append-only. One row per state change of a payment, written once under its key."""

    class Actor(models.TextChoices):
        OWNER = "owner"
        OPERATOR = "operator"

    payment = models.ForeignKey(
        BankTransferPayment, on_delete=models.PROTECT, related_name="events"
    )
    code = models.CharField(max_length=20)
    actor = models.CharField(max_length=10, choices=Actor.choices)
    idempotency_key = models.CharField(max_length=120, unique=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    detail = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.code} on payment {self.payment_id}"


class BillingNotice(models.Model):
    """An email about billing that was sent, so that it is sent once. Nothing writes this
    before the emails are built."""

    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="notices"
    )
    code = models.CharField(max_length=40)
    idempotency_key = models.CharField(max_length=120, unique=True)
    sent_at = models.DateTimeField()

    def __str__(self):
        return f"{self.code} for subscription {self.subscription_id}"


class RenewalConsent(models.Model):
    """What a buyer of the Launch Promo was told about the price after its six months, and
    that they accepted it. One row per proof, written with it, never changed or deleted.

    It is a record, not a debit mandate: no payment gateway exists, and nothing is charged
    on the strength of it. Kept five years with the payment it belongs to.
    """

    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="consents"
    )
    payment = models.OneToOneField(
        BankTransferPayment, on_delete=models.PROTECT, related_name="consent"
    )
    plan_code = models.CharField(max_length=20)
    plan_version = models.PositiveSmallIntegerField()
    then_plan_code = models.CharField(max_length=20)
    then_price_centavos = models.PositiveIntegerField()
    # The first date after the term that the buyer was shown; empty when the term starts
    # at the confirmation (an ended or suspended site).
    shown_first_due_at = models.DateTimeField(null=True, blank=True)
    starts_at_confirmation = models.BooleanField(default=False)
    text_version = models.CharField(max_length=40)
    accepted_by = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="+"
    )
    accepted_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("A consent record is kept as it was and cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("A consent record is kept and cannot be deleted.")

    def __str__(self):
        return f"Consent for payment {self.payment_id}"


class UsageEntry(models.Model):
    """One storage snapshot or one month's order count the ledger has decided about. Never deleted.

    A snapshot or count at or below the allowance, or never measured, has no row. Write only through
    apps.billing.usage; a row changes only along the routes recorded as UsageEvent rows.
    """

    class Kind(models.TextChoices):
        STORAGE = "storage"
        ORDERS = "orders"  # OV-9: SaaS plan with 500 orders included; a flat fee per extra order;
        # never a share of sales. NegosyoOnline does not meter video.

    class Status(models.TextChoices):
        COUNTED = "counted"
        HELD = "held"  # the cycle is pending (L36)
        NEEDS_REVIEW = "needs_review"
        NOT_BILLABLE = "not_billable"
        WRITTEN_OFF = "written_off"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="usage_entries"
    )
    cycle = models.ForeignKey(
        BillingCycle, null=True, blank=True, on_delete=models.PROTECT, related_name="usage_entries"
    )
    period_index = models.PositiveSmallIntegerField(null=True, blank=True)  # 1-based
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.STORAGE)
    source_reference = models.CharField(max_length=64, unique=True)
    quantity = models.PositiveBigIntegerField()  # the measured bytes, or the orders counted
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField()
    status = models.CharField(max_length=15, choices=Status.choices)
    reason = models.CharField(max_length=30, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            # One snapshot per period, ever: a second is a replay and nothing recomputes it.
            models.UniqueConstraint(
                fields=["cycle", "period_index", "kind"], name="billing_usage_one_per_period"
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="billing_usage_quantity_positive"),
        ]

    def delete(self, *args, **kwargs):
        raise RuntimeError("A usage record is kept and cannot be deleted.")

    def __str__(self):
        return f"Usage {self.source_reference}"


class ChargeLine(models.Model):
    """One overage charge. Its amount and source never change; it is closed once, settled by a
    confirmed payment or written off with its cause, and never reopened."""

    class Kind(models.TextChoices):
        OVERAGE_STORAGE = "overage_storage"
        OVERAGE_ORDERS = "overage_orders"

    class Status(models.TextChoices):
        OPEN = "open"
        SETTLED = "settled"
        WRITTEN_OFF = "written_off"

    FROZEN = (
        "subscription_id", "cycle_id", "period_index", "entry_id", "kind", "quantity",
        "amount_centavos", "currency", "labels",
    )

    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="charges")
    cycle = models.ForeignKey(BillingCycle, on_delete=models.PROTECT, related_name="charges")
    period_index = models.PositiveSmallIntegerField()
    entry = models.OneToOneField(UsageEntry, on_delete=models.PROTECT, related_name="charge")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OVERAGE_STORAGE)
    quantity = models.PositiveIntegerField()  # the started GB, or the orders over 500, charged
    amount_centavos = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="PHP")
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)
    settled_payment = models.ForeignKey(
        BankTransferPayment, null=True, blank=True, on_delete=models.PROTECT,
        related_name="charges",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=40, blank=True)
    labels = models.JSONField(default=list, blank=True)  # for example ["late_admitted"]
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount_centavos__gt=0), name="billing_charge_positive"),
            models.CheckConstraint(
                condition=~Q(status="settled") | Q(settled_payment__isnull=False),
                name="billing_charge_settled_has_payment",
            ),
            models.CheckConstraint(
                condition=~Q(status="written_off") | ~Q(closed_reason=""),
                name="billing_charge_written_off_has_cause",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old = ChargeLine.objects.get(pk=self.pk)
            for name in self.FROZEN:
                if getattr(old, name) != getattr(self, name):
                    raise RuntimeError(f"A charge line cannot change its {name}.")
            if old.status != self.Status.OPEN and (
                old.status != self.status or old.settled_payment_id != self.settled_payment_id
                or old.closed_reason != self.closed_reason
            ):
                raise RuntimeError("A closed charge line cannot be changed or reopened.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("A charge line is kept and cannot be deleted.")

    def __str__(self):
        return f"Charge {self.pk} of subscription {self.subscription_id}"


class UsageEvent(models.Model):
    """Append-only. One row per usage decision, written once under its idempotency key."""

    class Actor(models.TextChoices):
        INTAKE = "intake"
        SWEEP = "sweep"
        OPERATOR = "operator"

    entry = models.ForeignKey(UsageEntry, on_delete=models.PROTECT, related_name="events")
    code = models.CharField(max_length=40)
    actor = models.CharField(max_length=10, choices=Actor.choices)
    idempotency_key = models.CharField(max_length=120, unique=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    detail = models.JSONField(default=dict, blank=True)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("A usage event cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("A usage event is kept and cannot be deleted.")

    def __str__(self):
        return f"{self.code} on usage {self.entry_id}"


def new_receiving_account_id():
    return new_public_id("rca")


class ReceivingAccount(models.Model):
    """An account NegosyoOnline's Subscribers are told to pay into by bank transfer.

    Set by staff in HQ Finance (no migration, seed or fixture creates one). Only active rows
    reach a Subscriber or count for `plan.collection_live()`. Rows are kept: nothing deletes
    one, so an old proof can still be reconciled. Write only through apps.billing.accounts.
    """

    class Kind(models.TextChoices):
        BANK = "bank"
        EWALLET = "ewallet"

    public_id = models.CharField(
        max_length=40, unique=True, default=new_receiving_account_id, editable=False
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    provider_name = models.CharField(max_length=60)
    account_name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=40)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=["bank", "ewallet"]), name="billing_receiving_account_kind"
            ),
            models.CheckConstraint(
                condition=~Q(provider_name="") & ~Q(account_name="") & ~Q(account_number=""),
                name="billing_receiving_account_not_blank",
            ),
            models.UniqueConstraint(
                fields=["provider_name", "account_number"],
                name="billing_receiving_account_unique",
            ),
        ]

    def delete(self, *args, **kwargs):
        raise RuntimeError("A receiving account is kept and cannot be deleted.")

    def __str__(self):
        return f"Receiving account {self.public_id}"


class CreditEntry(AppendOnlyMixin, models.Model):
    """One movement of an account's Subscription Credit (Billing B6). Insert-only: nothing
    updates or deletes a row (Django refuses, and on PostgreSQL a trigger does).

    The balance is the sum of `amount_centavos`, and it is what can be spent: a `reserve`
    takes credit out of it while a payment waits, `release` puts it back, and `apply` records
    that the reserve was spent (it moves nothing: the reserve already did). Credit is in
    centavos and is not cash. Write only through apps.billing.credits.
    """

    REFUSAL_TARGET_TYPE = "credit_entry"
    REFUSAL_ID_PATH = "idempotency_key"

    class Kind(models.TextChoices):
        EARN = "earn"
        RESERVE = "reserve"
        RELEASE = "release"
        APPLY = "apply"
        REVERSE = "reverse"
        CORRECTION = "correction"

    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount_centavos = models.BigIntegerField()  # signed; see the class text
    payment = models.ForeignKey(
        BankTransferPayment, null=True, blank=True, on_delete=models.PROTECT,
        related_name="credit_entries",
    )
    idempotency_key = models.CharField(max_length=120, unique=True)
    reason = models.CharField(max_length=300, blank=True)  # a correction's reason
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    detail = models.JSONField(default=dict, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["account", "kind"], name="billing_credit_account_kind")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(kind="earn", amount_centavos__gt=0)
                    | Q(kind="release", amount_centavos__gt=0)
                    | Q(kind="reserve", amount_centavos__lt=0)
                    | Q(kind="reverse", amount_centavos__lt=0)
                    | Q(kind="apply", amount_centavos=0)
                    | Q(kind="correction", amount_centavos__lt=0)
                    | Q(kind="correction", amount_centavos__gt=0)
                ),
                name="billing_credit_amount_sign",
            ),
            models.CheckConstraint(
                condition=~Q(kind="correction") | (~Q(reason="") & Q(operator__isnull=False)),
                name="billing_credit_correction_has_reason",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["earn", "reserve", "release", "apply"], payment__isnull=False)
                | Q(kind__in=["reverse", "correction"]),
                name="billing_credit_payment_kinds_have_payment",
            ),
        ]

    def __str__(self):
        return f"{self.kind} {self.amount_centavos} on account {self.account_id}"
