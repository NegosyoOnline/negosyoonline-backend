"""How a site takes payment from its buyers: its bank and e-wallet accounts, and whether
paying to them (direct transfer) is switched on. Write these tables through services.py,
never directly."""
import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.ids import new_public_id

BANK, EWALLET = "bank", "ewallet"
PAYMENT_KINDS = [(BANK, "Bank"), (EWALLET, "E-wallet")]
SAVINGS, CHECKING = "savings", "checking"
ACCOUNT_TYPES = [(EWALLET, "E-wallet"), (SAVINGS, "Savings"), (CHECKING, "Checking")]


class ImmutableRecordError(Exception):
    """The row is kept for the record and cannot be deleted."""


def new_payment_account_id():
    return new_public_id("pay")


class SitePaymentAccount(models.Model):
    """A bank or e-wallet account buyers send their payment to. Removed by setting
    removed_at, never deleted: a payment proof will point at the account it was sent to."""

    public_id = models.CharField(
        max_length=40, unique=True, default=new_payment_account_id, editable=False
    )
    site = models.ForeignKey(
        "sites.Site", on_delete=models.PROTECT, related_name="payment_accounts"
    )
    kind = models.CharField(max_length=8, choices=PAYMENT_KINDS)
    provider_name = models.CharField(max_length=60)
    account_type = models.CharField(max_length=8, choices=ACCOUNT_TYPES, blank=True, default="")
    account_name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=40)
    # The key of the account's QR code image in the private file store; empty when it has none.
    qr_file_key = models.CharField(max_length=200, blank=True, default="", editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(fields=["site", "removed_at"], name="payments_acct_site_removed")
        ]
        verbose_name = "site payment account"

    def __str__(self):
        return f"{self.provider_name} account of {self.site}"

    def clean(self):
        if self.kind == EWALLET and self.account_type not in ("", EWALLET):
            raise ValidationError({"account_type": "An e-wallet account is type E-wallet."})
        if self.kind == BANK and self.account_type not in ("", SAVINGS, CHECKING):
            raise ValidationError({"account_type": "A bank account is Savings or Checking."})

    @property
    def type_label(self):
        """"Savings", "Checking" or "E-wallet"; empty while a bank row has no type."""
        return dict(ACCOUNT_TYPES).get(self.account_type, "")

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(
            "Payment accounts are removed by setting removed_at, never deleted."
        )


class SitePaymentSettings(models.Model):
    """The owner's on/off switch for direct transfer. The method shows at checkout only
    while it is switched on and set up. No row means it is on."""

    site = models.OneToOneField(
        "sites.Site", on_delete=models.PROTECT, related_name="payment_settings"
    )
    direct_enabled = models.BooleanField(default=True)
    # The owner's Xendit switch. Off until the owner turns it on, and only with a working key.
    xendit_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site payment settings"
        verbose_name_plural = "site payment settings"

    def __str__(self):
        return f"Payment settings of {self.site}"


TEST, LIVE = "test", "live"
KEY_MODES = [(TEST, "Test"), (LIVE, "Live")]


def new_webhook_key():
    return secrets.token_urlsafe(32)


class SiteXenditCredential(models.Model):
    """The site's own Xendit secret key and webhook verification token (spec v1.12 7.1), Fernet
    ciphertext only, never returned. Owner-entered; no platform key exists. `webhook_key` is the
    random part of the site's webhook URL and stays when the secrets are cleared, so a re-save
    keeps the same URL. Adapted from TWCinc negosyoonline_orders SiteXenditCredential."""

    site = models.OneToOneField("sites.Site", on_delete=models.PROTECT, related_name="xendit_credential")
    api_key_ciphertext = models.TextField(blank=True, default="")
    webhook_token_ciphertext = models.TextField(blank=True, default="")
    key_mode = models.CharField(max_length=4, choices=KEY_MODES, blank=True, default="")
    key_failed_at = models.DateTimeField(null=True, blank=True)  # Xendit answered unauthorized
    webhook_key = models.CharField(max_length=64, unique=True, default=new_webhook_key, editable=False)
    actor_account_id = models.PositiveBigIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site Xendit credential"

    def __str__(self):
        return f"Xendit credential of {self.site}"

    @property
    def configured(self):
        return bool(self.api_key_ciphertext)


class XenditEvent(models.Model):
    """One Xendit event received for a site (webhook or reconcile), append-only. Only whitelisted
    fields, never a body, header or key. Unique per (site, provider_event_id): a repeat is a
    duplicate and changes nothing."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="xendit_events")
    provider_event_id = models.CharField(max_length=128)
    event_type = models.CharField(max_length=64)
    external_id = models.CharField(max_length=64, blank=True)
    invoice_id = models.CharField(max_length=64, blank=True)
    body_sha256 = models.CharField(max_length=64)
    received_at = models.DateTimeField(default=timezone.now, editable=False)
    outcome = models.CharField(max_length=16)
    order_ref = models.PositiveBigIntegerField(null=True, blank=True)
    source = models.CharField(max_length=10, default="webhook")  # webhook | reconcile

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "provider_event_id"], name="payments_xendit_event_uniq")
        ]
        verbose_name = "Xendit event"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ImmutableRecordError("Xendit events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Xendit events are append-only.")
