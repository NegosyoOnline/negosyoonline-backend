"""A record of each message sent. Bodies are never stored: they can hold sign-in codes."""
import time

from django.conf import settings
from django.db import connection, models
from django.utils import timezone

from apps.common.ids import new_public_id

_SITE_COLUMN = {"found": False, "checked": 0.0}
_RECHECK_AFTER = 30  # seconds before a missing column is looked for again


def site_column_exists():
    """Whether notifications.0005 is applied (SmsDevice.site_id exists). Until it is, every
    SmsDevice query leaves the column out and behaves as before the migration. A found column
    is remembered for the life of the process; a missing one is looked for again every 30 s,
    so a process notices the migration without a restart."""
    if _SITE_COLUMN["found"]:
        return True
    now = time.monotonic()
    if _SITE_COLUMN["checked"] and now - _SITE_COLUMN["checked"] < _RECHECK_AFTER:
        return False
    table = SmsDevice._meta.db_table
    with connection.cursor() as cursor:
        columns = {c.name for c in connection.introspection.get_table_description(cursor, table)}
    _SITE_COLUMN.update(found="site_id" in columns, checked=now)
    return _SITE_COLUMN["found"]


def forget_site_column_check():
    _SITE_COLUMN.update(found=False, checked=0.0)


_STORE_LIMITS = {"found": False, "checked": 0.0}


def store_limit_columns_exist():
    """Whether notifications.0006 is applied (SmsPolicy.store_daily_limit and
    all_stores_daily_limit exist). Until it is, every SmsPolicy query leaves the two columns out
    and the rest of the SMS code behaves as before: the code is released before its migration and
    must not stop a sign-in code. Remembered once found; a missing one is looked for again every 30 s."""
    if _STORE_LIMITS["found"]:
        return True
    now = time.monotonic()
    if _STORE_LIMITS["checked"] and now - _STORE_LIMITS["checked"] < _RECHECK_AFTER:
        return False
    table = SmsPolicy._meta.db_table
    with connection.cursor() as cursor:
        columns = {c.name for c in connection.introspection.get_table_description(cursor, table)}
    _STORE_LIMITS.update(
        found={"store_daily_limit", "all_stores_daily_limit"} <= columns, checked=now
    )
    return _STORE_LIMITS["found"]


def forget_store_limit_check():
    _STORE_LIMITS.update(found=False, checked=0.0)


_STORE_BULK_LIMIT = {"found": False, "checked": 0.0}
STORE_BULK_COLUMN = "store_bulk_daily_limit"


def store_bulk_limit_column_exists():
    """Whether notifications.0008 (SmsPolicy.store_bulk_daily_limit, and the store bulk tables) is
    applied. Until it is, every SmsPolicy query leaves the column out and the rest of the SMS code
    behaves as before: the code is released before its migration and must not stop a sign-in code.
    Remembered once found; a missing one is looked for again every 30 s."""
    if _STORE_BULK_LIMIT["found"]:
        return True
    now = time.monotonic()
    if _STORE_BULK_LIMIT["checked"] and now - _STORE_BULK_LIMIT["checked"] < _RECHECK_AFTER:
        return False
    table = SmsPolicy._meta.db_table
    with connection.cursor() as cursor:
        columns = {c.name for c in connection.introspection.get_table_description(cursor, table)}
    _STORE_BULK_LIMIT.update(found=STORE_BULK_COLUMN in columns, checked=now)
    return _STORE_BULK_LIMIT["found"]


def forget_store_bulk_limit_check():
    _STORE_BULK_LIMIT.update(found=False, checked=0.0)


class SmsDeviceManager(models.Manager):
    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset if site_column_exists() else queryset.defer("site")


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
    # Empty: the platform phone (every phone so far). Set: this phone is that Site's own line.
    # A Site phone is handed texts only through `sms.line_for` (switch on and the Site entitled).
    site = models.ForeignKey(
        "sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="sms_devices"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    forwards_incoming = models.BooleanField("forwards incoming texts", default=False)
    last_contact_at = models.DateTimeField(null=True, blank=True)
    app_version = models.CharField(max_length=20, blank=True)
    battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    is_charging = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    objects = SmsDeviceManager()

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Before notifications.0005 the column is not read: no phone has a Site.
        instance.__dict__.setdefault("site_id", None)
        return instance

    class Meta:
        base_manager_name = "objects"
        verbose_name = "SMS phone"
        constraints = [
            models.UniqueConstraint(
                fields=["site"],
                condition=models.Q(is_active=True, site__isnull=False),
                name="sms_device_one_active_per_site",
            )
        ]

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
        INBOX = "inbox"
        STORE = "store"  # a Santé store's text to its own member, from the store's dash Inbox
        STORE_BULK = "storebulk"  # a store's bulk text to its own members (counted apart from STORE)

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


class SmsLineDisable(models.Model):
    """HQ's audited Disable of a Site's own phone line (SMS-INCLUDED, Coach Ed 2026-11-21). A
    Site with no row, or only rows with `reenabled_at` set, is not disabled. Disabling writes a
    new row; re-enabling stamps `reenabled_at`, so the history is kept. Read only through
    `sms.site_has_line`; the HQ Disable and Re-enable screens come later."""

    site = models.ForeignKey(
        "sites.Site", on_delete=models.PROTECT, related_name="sms_line_disables"
    )
    disabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    reason = models.CharField(max_length=300)
    disabled_at = models.DateTimeField(default=timezone.now)
    reenabled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "SMS line disable"
        constraints = [
            models.UniqueConstraint(
                fields=["site"],
                condition=models.Q(reenabled_at__isnull=True),
                name="sms_line_one_active_disable_per_site",
            )
        ]

    def __str__(self):
        return f"{self.site_id} disabled {self.disabled_at:%Y-%m-%d}"


STORE_DAILY_LIMIT_DEFAULT = 50  # Coach Ed, 2026-11-21 (SO4): texts a store may send a day
ALL_STORES_DAILY_LIMIT_DEFAULT = 1000  # ... and all stores together
STORE_BULK_DAILY_LIMIT_DEFAULT = 2000  # Coach Ed, 2026-11-23 (STORE-BULK D3): bulk texts a store may send a day


class SmsPolicyManager(models.Manager):
    def get_queryset(self):
        queryset = super().get_queryset()
        missing = []
        if not store_limit_columns_exist():
            missing += ["store_daily_limit", "all_stores_daily_limit"]
        if not store_bulk_limit_column_exists():
            missing.append(STORE_BULK_COLUMN)
        return queryset.defer(*missing) if missing else queryset


class SmsPolicy(models.Model):
    """The one row of SMS settings: the kill switch and the daily limits."""

    sending_stopped = models.BooleanField(
        default=False, help_text="Stops every text at once. Texts already waiting expire unsent."
    )
    platform_daily_limit = models.PositiveIntegerField(
        default=2000, help_text="Texts a day (Manila time), all sites and all codes together."
    )
    site_daily_limit = models.PositiveIntegerField(
        default=2000, help_text="Order-status texts a day (Manila time) for one site."
    )
    store_daily_limit = models.PositiveIntegerField(
        default=STORE_DAILY_LIMIT_DEFAULT,
        help_text="Texts a day (Manila time) one store may send to its own members.",
    )
    all_stores_daily_limit = models.PositiveIntegerField(
        default=ALL_STORES_DAILY_LIMIT_DEFAULT,
        help_text="Texts a day (Manila time) all stores together may send to their members.",
    )
    store_bulk_daily_limit = models.PositiveIntegerField(
        default=STORE_BULK_DAILY_LIMIT_DEFAULT,
        help_text="Bulk texts a day (Manila time) one store may send to its own members, apart from its one-to-one texts.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects = SmsPolicyManager()

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Before notifications.0006 the two columns are not read: the defaults stand in.
        instance.__dict__.setdefault("store_daily_limit", STORE_DAILY_LIMIT_DEFAULT)
        instance.__dict__.setdefault("all_stores_daily_limit", ALL_STORES_DAILY_LIMIT_DEFAULT)
        instance.__dict__.setdefault(STORE_BULK_COLUMN, STORE_BULK_DAILY_LIMIT_DEFAULT)
        return instance

    class Meta:
        base_manager_name = "objects"
        verbose_name = "SMS settings"
        verbose_name_plural = "SMS settings"

    def __str__(self):
        return "SMS settings"


def new_store_bulk_id():
    return new_public_id("sbk")


class StoreBulkBatch(models.Model):
    """One store's bulk text to its own members (STORE-BULK, ClickUp z8uq9mbreu). It holds the text as
    sent (the store's name in front) for 180 days, then the whole batch is deleted. Never logged or
    audited: the audit entries carry the batch id and counts only. One active batch per store."""

    WAITING, SENDING, FINISHED, STOPPED, ENDED = "waiting", "sending", "finished", "stopped", "ended"
    STATES = [(WAITING, "Waiting"), (SENDING, "Sending"), (FINISHED, "Finished"), (STOPPED, "Stopped"), (ENDED, "Ended")]
    ACTIVE = (WAITING, SENDING)
    ALL, PROMO = "all", "promo"
    AUDIENCES = [(ALL, "All my members"), (PROMO, "Members who agreed to promo texts")]

    public_id = models.CharField(max_length=40, unique=True, default=new_store_bulk_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="store_bulk_batches")
    created_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    request_id = models.CharField(max_length=64)
    text = models.CharField(max_length=160)  # as it arrives: the store's name first
    promotional = models.BooleanField(default=False)  # a label only
    audience = models.CharField(max_length=5, choices=AUDIENCES, default=ALL)
    state = models.CharField(max_length=10, choices=STATES, default=WAITING)
    end_reason = models.CharField(max_length=30, blank=True)  # stopped, site_held, billing_only, line_disabled, ...
    size = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    not_sent = models.PositiveIntegerField(default=0)
    # Why members were left out when the batch was confirmed: {reason: count}.
    left_out = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "store bulk batch"
        constraints = [
            models.UniqueConstraint(fields=["site", "request_id"], name="store_bulk_site_request_uniq"),
            models.UniqueConstraint(
                fields=["site"], condition=models.Q(state__in=["waiting", "sending"]),
                name="store_bulk_one_active_per_site",
            ),
        ]

    def __str__(self):  # never the text
        return f"Store bulk batch {self.public_id}"


class StoreBulkRecipient(models.Model):
    """One member of a batch. The number is held in full only while the batch needs it; when the
    batch is over it is replaced by its masked form."""

    WAITING, QUEUED, SENT, FAILED, NOT_SENT = "waiting", "queued", "sent", "failed", "not_sent"

    batch = models.ForeignKey(StoreBulkBatch, on_delete=models.CASCADE, related_name="recipients")
    position = models.PositiveIntegerField()
    member = models.ForeignKey("members.Member", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=120, blank=True)
    mobile = models.CharField(max_length=13)  # +639XXXXXXXXX, or its masked form once the batch is over
    state = models.CharField(max_length=10, default=WAITING)
    reason = models.CharField(max_length=40, blank=True)
    send_record = models.CharField(max_length=40, blank=True)  # the gateway's send record id

    class Meta:
        verbose_name = "store bulk recipient"
        constraints = [models.UniqueConstraint(fields=["batch", "position"], name="store_bulk_recipient_position_uniq")]

    def __str__(self):  # never the number or the name
        return f"Recipient {self.pk} of batch {self.batch_id}"
