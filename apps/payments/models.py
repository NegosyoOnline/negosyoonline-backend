"""How a site takes payment from its buyers: its bank and e-wallet accounts, and whether
paying to them (direct transfer) is switched on. Write these tables through services.py,
never directly."""
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
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site payment settings"
        verbose_name_plural = "site payment settings"

    def __str__(self):
        return f"Payment settings of {self.site}"
