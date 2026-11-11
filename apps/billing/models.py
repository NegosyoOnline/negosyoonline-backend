"""One subscription per site, its billing cycles, and the record of every state change.

Other apps reach these tables only through apps.billing.services.
"""
from django.db import models
from django.db.models import F, Q


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
