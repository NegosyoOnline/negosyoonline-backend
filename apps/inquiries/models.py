"""Inquiry tables: a site's provider connection, the conversations, and one audit row per outgoing
reply attempt.

For a provider channel (none is connected yet) no table holds an inbound message, a reply's text, a
token in the clear or a URL with a query: the provider stays the source of the conversation
history. A stored channel (website chat, the contact form as email) has no provider to hold its
history, so InquiryMessage keeps the text, for MESSAGE_RETENTION (apps/inquiries/retention.py).
The reply audit row never holds text."""
import secrets

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.inquiries import outcomes


class InquiryConnection(models.Model):
    """One row per site and provider: the page id and the encrypted page token. The ciphertext
    is never serialised, logged or printed."""

    UNSET, ACTIVE, INVALID, DISABLED = "unset", "active", "invalid", "disabled"
    STATUSES = [(UNSET, "Not set"), (ACTIVE, "Active"), (INVALID, "Invalid"), (DISABLED, "Disabled")]

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_connections")
    provider = models.CharField(max_length=16)  # "messenger" (the direct Messenger inbox)
    status = models.CharField(max_length=12, choices=STATUSES, default=UNSET)
    token_encrypted = models.TextField(blank=True)  # Fernet ciphertext; empty = not set
    external_page_id = models.CharField(max_length=64, blank=True)
    label = models.CharField(max_length=60, blank=True)  # messenger: the Page's name
    meta_user_id = models.CharField(max_length=64, blank=True, db_default="")  # messenger: the Facebook user who connected it
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_outcome = models.CharField(max_length=24, blank=True)  # an outcome code, never provider text
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "provider"], name="inquiry_connection_site_provider_uniq"),
            # D5: a Facebook Page is connected to one site at a time.
            models.UniqueConstraint(
                fields=["external_page_id"], condition=Q(provider="messenger") & ~Q(external_page_id=""),
                name="inquiry_connection_messenger_page_uniq",
            ),
        ]

    def __repr__(self):
        return f"<InquiryConnection site={self.site_id} provider={self.provider}>"

    __str__ = __repr__


def route_token():
    """A random, unguessable token that is not the site id (used by the email channel, PR2)."""
    return secrets.token_urlsafe(14)[:18]


class InquiryConversation(models.Model):
    """For a provider channel, a handle created lazily when the owner opens or answers a
    conversation, so a reply has something to point at; the list and the messages are fetched
    from the provider on demand. For a stored channel (`stores_history`), the conversation
    itself: made by the visitor's first message, with its messages in InquiryMessage."""

    WEBCHAT, EMAIL, MESSENGER = "webchat", "email", "messenger"
    PROVIDERS = [(WEBCHAT, "Website chat"), (EMAIL, "Email"), (MESSENGER, "Messenger")]
    STORED_PROVIDERS = (WEBCHAT, EMAIL, MESSENGER)
    REF_PREFIX = {WEBCHAT: "wc_", EMAIL: "em_", MESSENGER: "fm_"}  # a stored channel's refs are recognised by prefix
    OPEN, CLOSED = "open", "closed"
    STATUSES = [(OPEN, "Open"), (CLOSED, "Closed")]
    UNVERIFIED_SENDER = "unverified_sender"
    AUTH_FLAGS = [("", "None"), (UNVERIFIED_SENDER, "Unverified sender")]

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_conversations")
    connection = models.ForeignKey(
        InquiryConnection, null=True, blank=True, on_delete=models.PROTECT, related_name="conversations",
    )  # null for channels with no external connection (a later slice)
    provider = models.CharField(max_length=16, choices=PROVIDERS)
    external_id = models.CharField(max_length=128)  # a stored channel: a server-made random ref
    stores_history = models.BooleanField(default=False)  # True only for channels with no external history
    display_name = models.CharField(max_length=120, blank=True)  # the customer's name; blanked on disposal
    status = models.CharField(max_length=8, choices=STATUSES, default=OPEN, db_default=OPEN)
    contact_name = models.CharField(max_length=120, blank=True, db_default="")  # stored channels; personal, blanked
    contact_email = models.EmailField(max_length=254, blank=True, db_default="")  # only if the person gave it; blanked
    visitor_hash = models.CharField(max_length=64, blank=True, db_default="")  # SHA-256 of the chat visitor's secret
    thread_token = models.CharField(max_length=24, blank=True, db_default="")  # the email reply-thread token (PR2)
    psid = models.CharField(max_length=64, blank=True, db_default="")  # messenger: the customer's page-scoped id; personal, blanked
    page_id = models.CharField(max_length=64, blank=True, db_default="")  # messenger: the Page this conversation arrived on; a reply needs the connection to still be that Page
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    unread = models.BooleanField(default=False, db_default=False)
    auth_flag = models.CharField(max_length=24, blank=True, choices=AUTH_FLAGS, db_default="")
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
            models.UniqueConstraint(
                fields=["site", "visitor_hash"], condition=Q(status="open") & ~Q(visitor_hash=""),
                name="inquiry_conversation_one_open_per_visitor",
            ),
            models.UniqueConstraint(
                fields=["connection", "psid"], condition=~Q(psid=""), name="inquiry_conversation_connection_psid_uniq",
            ),
            models.UniqueConstraint(
                fields=["thread_token"], condition=~Q(thread_token=""), name="inquiry_conversation_thread_token_uniq",
            ),
        ]
        indexes = [models.Index(fields=["site", "visitor_hash"], name="inquiry_conv_site_visitor")]

    def __repr__(self):
        return f"<InquiryConversation site={self.site_id} pk={self.pk}>"

    __str__ = __repr__


class InquiryReply(models.Model):
    """One outgoing reply attempt. Holds the reply's length, never its text."""

    SENDING, SENT, FAILED = "sending", "sent", "failed"
    BLOCKED_WINDOW, BLOCKED_RATE = "blocked_window", "blocked_rate"
    DELIVERED, BOUNCED = "delivered", "bounced"  # email only, from the provider's events (PR2)
    STATES = [
        (SENDING, "Sending"), (SENT, "Sent"), (FAILED, "Failed"),
        (BLOCKED_WINDOW, "Blocked, reply window closed"), (BLOCKED_RATE, "Blocked, rate limit"),
        (DELIVERED, "Delivered"), (BOUNCED, "Bounced"),
    ]

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_replies")
    conversation = models.ForeignKey(InquiryConversation, on_delete=models.PROTECT, related_name="replies")
    author = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="inquiry_replies")
    state = models.CharField(max_length=16, choices=STATES, default=SENDING)
    error_code = models.CharField(max_length=24, blank=True, choices=[(c, c) for c in outcomes.CODES])
    idempotency_key = models.CharField(max_length=64)
    body_length = models.PositiveSmallIntegerField()
    provider_message_ref = models.CharField(max_length=128, blank=True)
    message = models.ForeignKey(  # a stored channel's outgoing message (its text lives there, not here)
        "inquiries.InquiryMessage", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies",
    )
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


class InquiryMessage(models.Model):
    """One message of a stored channel. The text is untrusted: it is shown as plain text only.
    Deleted MESSAGE_RETENTION after the conversation's last message, and with the site's data."""

    IN, OUT = "in", "out"
    DIRECTIONS = [(IN, "From the customer"), (OUT, "From the store")]
    CUSTOMER, OWNER = "customer", "owner"
    SENDER_KINDS = [(CUSTOMER, "Customer"), (OWNER, "Store owner")]
    DELIVERY_STATES = [
        ("", "None"), ("sent", "Sent"), ("failed", "Failed"), ("delivered", "Delivered"), ("bounced", "Bounced"),
        ("seen", "Seen"),
    ]

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_messages")
    conversation = models.ForeignKey(InquiryConversation, on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=3, choices=DIRECTIONS)
    sender_kind = models.CharField(max_length=8, choices=SENDER_KINDS)
    body = models.TextField(max_length=4000)
    external_id = models.CharField(max_length=128, blank=True)  # the provider's message id (email, PR2)
    delivery_state = models.CharField(max_length=12, blank=True, choices=DELIVERY_STATES)  # outgoing only
    error_code = models.CharField(max_length=24, blank=True)
    attachment_types = models.CharField(max_length=60, blank=True, db_default="")  # messenger: types only, comma-separated
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "external_id"], condition=~Q(external_id=""), name="inquiry_message_site_external_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["conversation", "created_at"], name="inquiry_msg_conv_time"),
            models.Index(fields=["site", "created_at"], name="inquiry_msg_site_time"),
        ]

    def __repr__(self):
        return f"<InquiryMessage site={self.site_id} pk={self.pk} {self.direction}>"

    __str__ = __repr__


class InquiryChannelSettings(models.Model):
    """One row per site: which stored channels it has switched on. Nothing here is a credential."""

    site = models.OneToOneField("sites.Site", on_delete=models.PROTECT, related_name="inquiry_channel_settings")
    webchat_enabled = models.BooleanField(default=False)
    email_enabled = models.BooleanField(default=False)  # PR2
    email_route_token = models.CharField(max_length=24, blank=True, default=route_token)  # PR2; blank = none
    paused_until = models.DateTimeField(null=True, blank=True)
    chat_limit_at = models.DateTimeField(null=True, blank=True)  # when the site's daily chat cap last tripped
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["email_route_token"], condition=~Q(email_route_token=""),
                name="inquiry_channel_route_token_uniq",
            ),
        ]

    def __repr__(self):
        return f"<InquiryChannelSettings site={self.site_id}>"

    __str__ = __repr__


def comment_ref():
    """The server-made id of a comment, the only id the browser sees (Meta's ids stay in the table)."""
    return "cm_" + secrets.token_hex(12)


class InquiryCommentSettings(models.Model):
    """One row per site, written when the owner connects a Page with the comments permissions.
    It is a table of its own (not columns on InquiryConnection) so that code released before its
    migration still reads the connection table. Nothing here is personal or a credential."""

    site = models.OneToOneField("sites.Site", on_delete=models.PROTECT, related_name="inquiry_comment_settings")
    feed_subscribed = models.BooleanField(default=False)  # the Page's `feed` field was subscribed with the permissions
    page_id = models.CharField(max_length=64, blank=True)  # the Page that subscription is for
    subscribed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __repr__(self):
        return f"<InquiryCommentSettings site={self.site_id}>"

    __str__ = __repr__


class InquiryPost(models.Model):
    """A Page post or ad that has comments: its id, a verified permalink pattern and a short caption."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_posts")
    connection = models.ForeignKey(InquiryConnection, on_delete=models.CASCADE, related_name="posts")
    external_id = models.CharField(max_length=64)  # Meta's post id
    is_ad = models.BooleanField(default=False)
    permalink = models.CharField(max_length=300, blank=True)  # only a verified pattern; no query
    caption = models.CharField(max_length=140, blank=True)  # a short snippet, never the full post
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "external_id"], name="inquiry_post_site_external_uniq"),
        ]

    def __repr__(self):
        return f"<InquiryPost site={self.site_id} pk={self.pk}>"

    __str__ = __repr__


class InquiryComment(models.Model):
    """One comment on a post or ad. The text and the author are personal and untrusted (plain text
    only). Deleted COMMENT_RETENTION after the comment time. A `remove` event keeps the row as a
    tombstone (no text, no author) so a late retry of the same comment is not stored again."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_comments")
    connection = models.ForeignKey(InquiryConnection, on_delete=models.CASCADE, related_name="comments")
    post = models.ForeignKey(InquiryPost, on_delete=models.CASCADE, related_name="comments")
    ref = models.CharField(max_length=32, default=comment_ref)
    external_id = models.CharField(max_length=64)  # Meta's comment id; the idempotency key
    parent_external_id = models.CharField(max_length=64, blank=True)  # the post id, or the comment replied to
    author_psid = models.CharField(max_length=64, blank=True)  # page-scoped id; personal, blanked on removal
    author_name = models.CharField(max_length=120, blank=True)  # personal, blanked on removal
    is_page = models.BooleanField(default=False)  # the Page's own comment
    text = models.TextField(max_length=2000, blank=True)  # personal, untrusted; blanked on removal
    unread = models.BooleanField(default=True)
    is_hidden = models.BooleanField(default=False)
    removed = models.BooleanField(default=False)
    public_reply_at = models.DateTimeField(null=True, blank=True)  # our send, or the Page's own reply seen on Facebook
    private_reply_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)  # Meta's time of the comment
    received_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "external_id"], name="inquiry_comment_site_external_uniq"),
            models.UniqueConstraint(fields=["site", "ref"], name="inquiry_comment_site_ref_uniq"),
        ]
        indexes = [
            models.Index(fields=["site", "-created_at", "-id"], name="inquiry_comment_site_time"),
            models.Index(fields=["post", "parent_external_id"], name="inquiry_comment_post_parent"),
        ]

    def __repr__(self):
        return f"<InquiryComment site={self.site_id} pk={self.pk}>"

    __str__ = __repr__


class InquiryCommentAction(models.Model):
    """One outgoing action on a comment (public reply, private reply, hide). Holds the outcome code
    and Meta's id of the result only: never the text, Meta's body, a token or a URL."""

    PUBLIC, PRIVATE, HIDE = "public", "private", "hide"
    KINDS = [(PUBLIC, "Public reply"), (PRIVATE, "Private reply"), (HIDE, "Hide")]
    MANUAL, RULE = "manual", "rule"  # rule: automation, a later task
    SOURCES = [(MANUAL, "Manual"), (RULE, "Rule")]
    QUEUED, SENDING, SENT, FAILED, BLOCKED_RATE, CANCELLED = (
        "queued", "sending", "sent", "failed", "blocked_rate", "cancelled")
    STATES = [
        (QUEUED, "Queued"), (SENDING, "Sending"), (SENT, "Sent"), (FAILED, "Failed"),
        (BLOCKED_RATE, "Blocked, rate limit"), (CANCELLED, "Cancelled"),
    ]

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="inquiry_comment_actions")
    comment = models.ForeignKey(InquiryComment, on_delete=models.CASCADE, related_name="actions")
    kind = models.CharField(max_length=8, choices=KINDS)
    source = models.CharField(max_length=8, choices=SOURCES, default=MANUAL)
    author = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="inquiry_comment_actions",
    )
    state = models.CharField(max_length=14, choices=STATES, default=SENDING)
    error_code = models.CharField(max_length=24, blank=True, choices=[(c, c) for c in outcomes.CODES])
    meta_id = models.CharField(max_length=128, blank=True)  # Meta's id of the reply comment or message
    idempotency_key = models.CharField(max_length=64, blank=True)
    body_length = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "idempotency_key"], condition=~Q(idempotency_key=""),
                name="inquiry_comment_action_site_key_uniq",
            ),
            # Meta: one private reply per comment. A failed attempt does not use it up.
            models.UniqueConstraint(
                fields=["comment"], condition=Q(kind="private") & Q(state__in=["queued", "sending", "sent"]),
                name="inquiry_comment_action_one_private",
            ),
            # An automatic public or private reply happens once per comment, ever (C2).
            models.UniqueConstraint(
                fields=["comment", "kind"], condition=Q(source="rule") & Q(kind__in=["public", "private"]),
                name="inquiry_comment_action_auto_once",
            ),
        ]

    def __repr__(self):
        return f"<InquiryCommentAction site={self.site_id} pk={self.pk} {self.kind} {self.state}>"

    __str__ = __repr__
