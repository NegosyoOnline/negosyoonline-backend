"""A record of each message sent. Bodies are never stored: they can hold sign-in codes."""
from django.db import models

from apps.common.ids import new_public_id


class EmailMessage(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"

    recipient = models.EmailField()
    category = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_reference = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.category} to {self.recipient} ({self.status})"


def new_sms_id():
    return new_public_id("sms")


class SmsDevice(models.Model):
    """A phone that sends texts. Added and managed only from the terminal. Never holds a SIM's
    number, and never the token itself: only its SHA-256."""

    name = models.CharField(max_length=60, unique=True)
    token_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    forwards_incoming = models.BooleanField("forwards incoming texts", default=False)
    last_contact_at = models.DateTimeField(null=True, blank=True)
    app_version = models.CharField(max_length=20, blank=True)
    battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    is_charging = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "SMS phone"

    def __str__(self):
        return self.name


class SmsMessage(models.Model):
    """One row per text. The text itself is never a column here: it waits encrypted in the
    cache until the phone takes it (apps.notifications.sms)."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        DISPATCHED = "dispatched"
        SENT = "sent"
        FAILED = "failed"
        EXPIRED = "expired"
        REFUSED = "refused"

    class Category(models.TextChoices):
        CODE = "code"
        STATUS = "status"

    class Priority(models.IntegerChoices):
        """The sending class. A lower number is handed to the phone first."""

        CODE = 10, "code"
        REPLY = 20, "reply"
        STATUS = 30, "status"
        MARKETING = 40, "marketing"

    public_id = models.CharField(max_length=40, unique=True, default=new_sms_id, editable=False)
    recipient = models.CharField(max_length=13)  # +639XXXXXXXXX
    category = models.CharField(max_length=10, choices=Category.choices)
    purpose = models.CharField(max_length=40)
    priority = models.PositiveSmallIntegerField(choices=Priority.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    reason = models.CharField(max_length=40, blank=True)
    emailed = models.BooleanField(default=False)
    # Sites are closed by marking them, never by deleting the row, so a send record is never
    # removed as a side effect: PROTECT refuses a delete that would take records with it.
    site = models.ForeignKey(
        "sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="sms_messages"
    )
    device = models.ForeignKey(
        SmsDevice, null=True, blank=True, on_delete=models.PROTECT, related_name="messages"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "SMS message"
        indexes = [
            models.Index(fields=["status", "priority", "created_at"], name="sms_handout_order"),
        ]

    def __str__(self):
        return f"{self.purpose} {self.public_id} ({self.status})"


class SmsPolicy(models.Model):
    """The one row of SMS settings: the kill switch and the two daily limits."""

    sending_stopped = models.BooleanField(
        default=False, help_text="Stops every text at once. Texts already waiting expire unsent."
    )
    platform_daily_limit = models.PositiveIntegerField(
        default=4000, help_text="Texts a day (Manila time), all sites and all codes together."
    )
    site_daily_limit = models.PositiveIntegerField(
        default=4000, help_text="Order-status texts a day (Manila time) for one site."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SMS settings"
        verbose_name_plural = "SMS settings"

    def __str__(self):
        return "SMS settings"
