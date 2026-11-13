"""Inquiry tables: a site's provider connection, a thin handle for each conversation the owner
opened or answered, and one audit row per outgoing reply attempt.

No table holds an inbound message, a reply's text, a token in the clear, a message from the
provider or a URL with a query. The provider stays the source of the conversation history."""
from django.db import models
from django.utils import timezone

from apps.inquiries import outcomes


class InquiryConnection(models.Model):
    """One row per site and provider: the page id and the encrypted page token. The ciphertext
    is never serialised, logged or printed."""

    PANCAKE = "pancake"
    PROVIDERS = [(PANCAKE, "Pancake")]
    UNSET, ACTIVE, INVALID, DISABLED = "unset", "active", "invalid", "disabled"
    STATUSES = [(UNSET, "Not set"), (ACTIVE, "Active"), (INVALID, "Invalid"), (DISABLED, "Disabled")]

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_connections")
    provider = models.CharField(max_length=16, choices=PROVIDERS, default=PANCAKE)
    status = models.CharField(max_length=12, choices=STATUSES, default=UNSET)
    token_encrypted = models.TextField(blank=True)  # Fernet ciphertext; empty = not set
    external_page_id = models.CharField(max_length=64, blank=True)
    label = models.CharField(max_length=60, blank=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_outcome = models.CharField(max_length=24, blank=True)  # an outcome code, never provider text
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "provider"], name="inquiry_connection_site_provider_uniq"),
        ]

    def __repr__(self):
        return f"<InquiryConnection site={self.site_id} provider={self.provider}>"

    __str__ = __repr__


class InquiryConversation(models.Model):
    """A handle, created lazily when the owner opens or answers a conversation, so a reply has
    something to point at. The list and the messages are fetched from the provider on demand."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_conversations")
    connection = models.ForeignKey(
        InquiryConnection, null=True, blank=True, on_delete=models.PROTECT, related_name="conversations",
    )  # null for channels with no external connection (a later slice)
    provider = models.CharField(max_length=16, choices=InquiryConnection.PROVIDERS)
    external_id = models.CharField(max_length=128)
    stores_history = models.BooleanField(default=False)  # True only for channels with no external history
    display_name = models.CharField(max_length=120, blank=True)  # the customer's name; blanked on disposal
    last_message_at = models.DateTimeField(null=True, blank=True)
    reply_open = models.BooleanField(null=True, blank=True)  # the provider's last answer; a cache only
    reply_checked_at = models.DateTimeField(null=True, blank=True)
    provider_url = models.CharField(max_length=300, blank=True)  # only a verified pattern; no query
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "provider", "external_id"], name="inquiry_conversation_site_ref_uniq",
            ),
        ]

    def __repr__(self):
        return f"<InquiryConversation site={self.site_id} pk={self.pk}>"

    __str__ = __repr__


class InquiryReply(models.Model):
    """One outgoing reply attempt. Holds the reply's length, never its text."""

    SENDING, SENT, FAILED = "sending", "sent", "failed"
    BLOCKED_WINDOW, BLOCKED_RATE = "blocked_window", "blocked_rate"
    STATES = [
        (SENDING, "Sending"), (SENT, "Sent"), (FAILED, "Failed"),
        (BLOCKED_WINDOW, "Blocked, reply window closed"), (BLOCKED_RATE, "Blocked, rate limit"),
    ]

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_replies")
    conversation = models.ForeignKey(InquiryConversation, on_delete=models.PROTECT, related_name="replies")
    author = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="inquiry_replies")
    state = models.CharField(max_length=16, choices=STATES, default=SENDING)
    error_code = models.CharField(max_length=24, blank=True, choices=[(c, c) for c in outcomes.CODES])
    idempotency_key = models.CharField(max_length=64)
    body_length = models.PositiveSmallIntegerField()
    provider_message_ref = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["site", "idempotency_key"], name="inquiry_reply_site_key_uniq"),
        ]
        indexes = [models.Index(fields=["site", "-created_at"], name="inquiry_reply_site_recent")]

    def __repr__(self):
        return f"<InquiryReply site={self.site_id} pk={self.pk} state={self.state}>"

    __str__ = __repr__
