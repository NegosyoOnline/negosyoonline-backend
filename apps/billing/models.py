"""One subscription per site, its billing cycles, the record of every state change, and the
bank-transfer payments that settle a cycle.

Other apps reach these tables only through apps.billing.services.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.common.ids import new_public_id


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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "sequence"], name="billing_cycle_sequence_once"
            ),
            models.CheckConstraint(
                condition=Q(end_at__gt=F("start_at")), name="billing_cycle_end_after_start"
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
    amount_centavos = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="PHP")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUBMITTED)
    transferred_on = models.DateField()  # the day the Subscriber says the transfer was made
    transfer_reference = models.CharField(max_length=64, blank=True)  # the bank's own number
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription"],
                condition=Q(status="submitted"),
                name="billing_one_transfer_under_review",
            ),
            # A confirmed payment settled a cycle, was decided by an operator, and the amount
            # received is the amount due.
            models.CheckConstraint(
                condition=~Q(status="confirmed") | (
                    Q(cycle__isnull=False)
                    & Q(decided_by__isnull=False)
                    & Q(amount_received_centavos=F("amount_centavos"))
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
