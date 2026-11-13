"""The records of the site deletion executor (plan z8uq9mb22v, section 8).

Staff-only tables of ids, dates, counts and codes: none holds a personal value. In PR 1 nothing
writes them; the propose, approve, hold and run routes come with PR 2. They are read only while
SITE_ERASURE_ENABLED is on.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.ids import new_public_id


def new_batch_id():
    return new_public_id("ebt")


class ErasureBatch(models.Model):
    class Status(models.TextChoices):
        PROPOSED = "proposed"
        APPROVED = "approved"
        RUNNING = "running"
        DONE = "done"
        NEEDS_ATTENTION = "needs_attention"
        CANCELLED = "cancelled"

    public_id = models.CharField(max_length=40, unique=True, default=new_batch_id, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED)
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    proposed_at = models.DateTimeField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_reason = models.CharField(max_length=300, blank=True)
    list_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=[
                    "proposed", "approved", "running", "done", "needs_attention", "cancelled",
                ]),
                name="lifecycle_batch_status_known",
            ),
        ]

    def __str__(self):
        return self.public_id


class ErasureItem(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        EXCLUDED = "excluded"
        SKIPPED = "skipped"
        ERASED = "erased"
        FAILED = "failed"

    batch = models.ForeignKey(ErasureBatch, on_delete=models.PROTECT, related_name="items")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="+")
    subscription_id = models.BigIntegerField()  # a snapshot, not a link
    deletion_due_at = models.DateTimeField()  # a snapshot
    planned_counts = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reason_code = models.CharField(max_length=40, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    counts = models.JSONField(default=dict)
    erased_at = models.DateTimeField(null=True, blank=True)
    account_disposed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            # A site can be erased once, by construction. This is the marker.
            models.UniqueConstraint(
                fields=["site"], condition=Q(status="erased"), name="lifecycle_one_erased_per_site"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["pending", "excluded", "skipped", "erased", "failed"]),
                name="lifecycle_item_status_known",
            ),
        ]

    def __str__(self):
        return f"Item of {self.batch_id} for site {self.site_id}"


class ErasureHold(models.Model):
    """A reason, set by a person, why a site must not be erased yet. At most one is open."""

    class Reason(models.TextChoices):
        LEGAL = "legal"
        FRAUD = "fraud"
        DISPUTE = "dispute"
        REGULATOR = "regulator"
        OTHER = "other"

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="+")
    reason_code = models.CharField(max_length=20, choices=Reason.choices)
    # A number or ticket reference, never a name or an address.
    reference = models.CharField(max_length=100, blank=True)
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    placed_at = models.DateTimeField()
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site"], condition=Q(released_at__isnull=True),
                name="lifecycle_one_open_hold_per_site",
            ),
            models.CheckConstraint(
                condition=Q(reason_code__in=["legal", "fraud", "dispute", "regulator", "other"]),
                name="lifecycle_hold_reason_known",
            ),
        ]

    def __str__(self):
        return f"Hold on site {self.site_id}"
