"""Meta Pixel and Conversions API tables: a site's settings, delivery evidence, and the browser
identifiers kept for an order until its Purchase is sent.

No table holds a payload, a token in the clear, a message from Meta or a URL with a query."""
from django.db import models
from django.utils import timezone


class MetaSettings(models.Model):
    """One row per site: the Pixel ID, the encrypted Conversions API token, a test event code.
    The ciphertext is never serialised, logged or printed."""

    site = models.OneToOneField("sites.Site", on_delete=models.PROTECT, related_name="meta_settings")
    pixel_id = models.CharField(max_length=20, blank=True)
    token_encrypted = models.TextField(blank=True)  # Fernet ciphertext; empty = not set
    test_event_code = models.CharField(max_length=32, blank=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_outcome = models.CharField(max_length=24, blank=True)  # an outcome code, never Meta's text
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __repr__(self):
        return f"<MetaSettings site={self.site_id}>"

    __str__ = __repr__


class MetaDelivery(models.Model):
    """Evidence that an event was queued and what came of it. Carries no payload, no user data."""

    QUEUED, SENT, RETRYING, FAILED, EXPIRED = "queued", "sent", "retrying", "failed", "expired"
    SKIPPED_NO_CLIENT_DATA, SKIPPED_BOT = "skipped_no_client_data", "skipped_bot"
    OUTCOMES = [
        (QUEUED, "Queued"), (SENT, "Sent"), (RETRYING, "Retrying"), (FAILED, "Failed"),
        (EXPIRED, "Expired"), (SKIPPED_NO_CLIENT_DATA, "Skipped, no client data"),
        (SKIPPED_BOT, "Skipped, bot"),
    ]
    OPEN = (QUEUED, RETRYING)

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="meta_deliveries")
    event_name = models.CharField(max_length=32)
    event_id = models.CharField(max_length=64)
    outcome = models.CharField(max_length=24, choices=OUTCOMES, default=QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    meta_error_code = models.IntegerField(null=True, blank=True)  # Meta's numeric code only
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "event_name", "event_id"], name="meta_delivery_event_uniq"
            ),
        ]
        indexes = [models.Index(fields=["site", "-created_at"], name="meta_delivery_site_recent")]


class MetaOrderContext(models.Model):
    """Browser identifiers captured when an order was placed, so its Purchase can be sent later.
    Written only with the visitor's consent, on a site with a Pixel and a token. Deleted when the
    Purchase is sent or 30 days after creation."""

    order = models.OneToOneField("pack.PackOrder", on_delete=models.PROTECT, related_name="meta_context")
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="meta_order_contexts")
    fbp = models.CharField(max_length=200, blank=True)
    fbc = models.CharField(max_length=200, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    source_url = models.CharField(max_length=300, blank=True)  # the checkout address, no query
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    def __repr__(self):
        return f"<MetaOrderContext order={self.order_id}>"

    __str__ = __repr__
