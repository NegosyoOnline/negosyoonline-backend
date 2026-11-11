"""The inbox's three records. `body` is the only column in the database that holds message
text. No record here has a Django admin screen."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.common.ids import new_public_id
from apps.inbox.switch import SMS


_OWNER = (
    Q(owner_kind="hq", site__isnull=True, account__isnull=True)
    | Q(owner_kind="site", site__isnull=False, account__isnull=True)
    | Q(owner_kind="account", site__isnull=True, account__isnull=False)
)


def new_conversation_id():
    return new_public_id("conv")


def new_message_id():
    return new_public_id("msg")


class Platform(models.TextChoices):
    SMS = SMS, "SMS"


class Conversation(models.Model):
    class Owner(models.TextChoices):
        HQ = "hq", "NegosyoOnline"
        SITE = "site", "A Site"
        ACCOUNT = "account", "A Subscriber"

    public_id = models.CharField(max_length=40, unique=True, default=new_conversation_id, editable=False)
    owner_kind = models.CharField(max_length=8, choices=Owner.choices)
    site = models.ForeignKey("sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    platform = models.CharField(max_length=16, choices=Platform.choices)
    line = models.ForeignKey("notifications.SmsDevice", on_delete=models.PROTECT, related_name="+")
    contact = models.CharField(max_length=32)
    can_reply = models.BooleanField(default=False)
    unread_count = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["-last_message_at"], name="inbox_conv_latest")]
        constraints = [
            models.CheckConstraint(
                name="inbox_conversation_owner",
                condition=_OWNER,
            ),
            models.CheckConstraint(name="inbox_conversation_platform", condition=Q(platform=SMS)),
            models.UniqueConstraint(
                name="inbox_conversation_one_per_hq_contact",
                fields=["platform", "line", "contact"], condition=Q(owner_kind="hq"),
            ),
            models.UniqueConstraint(
                name="inbox_conversation_one_per_site_contact",
                fields=["site", "platform", "line", "contact"], condition=Q(owner_kind="site"),
            ),
            models.UniqueConstraint(
                name="inbox_conversation_one_per_account_contact",
                fields=["account", "platform", "line", "contact"], condition=Q(owner_kind="account"),
            ),
        ]

    def __str__(self):
        return self.public_id


class ConversationMessage(models.Model):
    class Direction(models.TextChoices):
        IN = "in", "Received"
        OUT = "out", "Sent by staff"

    class Kind(models.TextChoices):
        REPLY = "reply", "Reply"
        COMPOSE = "compose", "Composed"

    public_id = models.CharField(max_length=40, unique=True, default=new_message_id, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    platform = models.CharField(max_length=16, choices=Platform.choices)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    body = models.TextField()
    clipped = models.BooleanField(default=False)
    gateway_ref = models.CharField(max_length=100, blank=True)
    device = models.ForeignKey("notifications.SmsDevice", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    sim_slot = models.PositiveSmallIntegerField(null=True, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=8, blank=True, choices=Kind.choices)
    occurred_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="inbox_message_platform", condition=Q(platform=SMS)),
            models.UniqueConstraint(
                name="inbox_message_once_per_phone",
                fields=["device", "gateway_ref"], condition=Q(direction="in"),
            ),
        ]

    def __str__(self):
        return self.public_id


class InboxAccess(models.Model):
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inbox_access")
    platform = models.CharField(max_length=16, choices=Platform.choices)
    can_send = models.BooleanField(default=False)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(name="inbox_access_one_per_operator_platform", fields=["operator", "platform"]),
        ]


def new_contact_id():
    return new_public_id("cont")


def new_group_id():
    return new_public_id("grp")


class ContactGroup(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_group_id, editable=False)
    owner_kind = models.CharField(max_length=8, choices=Conversation.Owner.choices)
    site = models.ForeignKey("sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="inbox_group_owner", condition=_OWNER),
            models.UniqueConstraint(
                Lower("name"), name="inbox_group_one_name_per_hq", condition=Q(owner_kind="hq"),
            ),
        ]

    def __str__(self):
        return self.public_id


class Contact(models.Model):
    """A name kept with a mobile number, until HQ staff delete it. No job removes it."""

    public_id = models.CharField(max_length=40, unique=True, default=new_contact_id, editable=False)
    owner_kind = models.CharField(max_length=8, choices=Conversation.Owner.choices)
    site = models.ForeignKey("sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    mobile = models.CharField(max_length=13)
    groups = models.ManyToManyField(ContactGroup, blank=True, related_name="contacts")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="inbox_contact_owner", condition=_OWNER),
            models.UniqueConstraint(
                name="inbox_contact_one_number_per_hq", fields=["mobile"], condition=Q(owner_kind="hq"),
            ),
        ]

    def __str__(self):
        return self.public_id
