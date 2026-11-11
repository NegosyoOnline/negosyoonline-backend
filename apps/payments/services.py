"""A site's payment methods. Other apps call these functions, never the tables.

Everything here is behind PAYMENT_METHODS_ENABLED. While it is off the routes answer 404, the
admin lists are closed and the two tables are not read or written: the code is released
before its tables are created.

A method is usable at checkout only while it is switched on and set up. The store's own
receiving account numbers go back in full to its owner.

Direct transfer (bank and e-wallet accounts) is the only method. Xendit is hidden (Coach Ed,
2026-11-11): no key is stored, the read always shows it as not set up and off, and it cannot
be switched on. The read keeps the `xendit` keys because the Payments page reads them.

Lock order, as in apps.sante.services: the account's Onboarding row, then the Site row, then
a payment row.
"""
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import record_event
from apps.common.errors import ApiError
from apps.payments.models import (
    BANK, CHECKING, EWALLET, SAVINGS, SitePaymentAccount, SitePaymentSettings,
)
from apps.sites.models import Onboarding, Site
from apps.sites.services import site_for

NUMBER_RE = re.compile(r"^[0-9 -]{1,40}$")
QR_NOT_YET = "QR code upload is not available yet. Save the account without a QR code."
XENDIT_HIDDEN = "Xendit is not available yet."
FIELD_ERRORS = {
    "kind": "Choose bank or e-wallet.",
    "provider_name": "Enter the bank or e-wallet name.",
    "account_name": "Enter the account name.",
    "account_number": "Enter the account number.",
    "account_type": "Choose Savings or Checking.",
}
ACCOUNT_FIELDS = ("kind", "account_type", "provider_name", "account_name", "account_number")
# What the read says of Xendit while it is hidden: not set up.
XENDIT_NOT_SET_UP = {
    "configured": False,
    "key_mode": "",
    "webhook_url": "",
    "key_failed": False,
    "key_failed_at": None,
}


def enabled():
    return settings.PAYMENT_METHODS_ENABLED


def not_found():
    return ApiError("not_found", "Not found.", status=404)


def _invalid(fields):
    return ApiError(
        "invalid_request",
        "Some values are missing or invalid.",
        fields={name: [message] for name, message in fields.items()},
    )


def _locked_site(account, site):
    """The site again, under the lock every site write of this account takes (a disposal takes
    it too). Call it inside a transaction."""
    Onboarding.objects.select_for_update().filter(account=account).first()
    checked = site_for(account, site.public_id)
    return Site.objects.select_for_update().get(pk=checked.pk)


# ---- which methods are on, and which are usable ----

def active_accounts(site):
    return SitePaymentAccount.objects.filter(site=site, removed_at__isnull=True).order_by(
        "created_at", "pk"
    )


def methods_enabled(site):
    """The owner's switches. No row means direct transfer is on. Xendit is always off."""
    row = SitePaymentSettings.objects.filter(site=site).first()
    return {"direct": True if row is None else row.direct_enabled, "xendit": False}


def methods_available(site):
    """What a buyer may choose at checkout: switched on and set up. Direct transfer is set up
    once the site has an account."""
    direct = methods_enabled(site)["direct"] and active_accounts(site).exists()
    return {"direct": direct, "xendit": False}


def any_method(site):
    return methods_available(site)["direct"]


@transaction.atomic
def set_enabled(account, site, *, direct=None, xendit=None):
    """Switch direct transfer on or off; None leaves it as it is. Off hides the method from
    new checkouts only: its accounts stay saved. Xendit cannot be switched on: True is
    refused and nothing is written; False changes nothing, since it is always off."""
    if xendit:
        raise _invalid({"xendit_enabled": XENDIT_HIDDEN})
    if direct is None:
        return None
    site = _locked_site(account, site)
    row, _ = SitePaymentSettings.objects.select_for_update().get_or_create(site=site)
    row.direct_enabled = direct
    row.save()
    record_event(account, "payment_methods_switched", site=site.public_id, direct_enabled=direct)
    return row


# ---- bank and e-wallet accounts ----

def clean_account(data, files=None):
    """The account fields of a submitted form, or ApiError naming each bad field. A QR file
    is refused: this slice stores no file."""
    data, cleaned, errors = data or {}, {}, {}
    kind = (data.get("kind") or "").strip()
    if kind not in (BANK, EWALLET):
        errors["kind"] = FIELD_ERRORS["kind"]
    cleaned["kind"] = kind
    # An e-wallet is always type "ewallet"; a bank needs Savings or Checking.
    account_type = (data.get("account_type") or "").strip().lower()
    if kind == EWALLET:
        account_type = EWALLET
    elif kind == BANK and account_type not in (SAVINGS, CHECKING):
        errors["account_type"] = FIELD_ERRORS["account_type"]
    cleaned["account_type"] = account_type
    for field, limit in (("provider_name", 60), ("account_name", 120)):
        value = (data.get(field) or "").strip()
        if not 1 <= len(value) <= limit:
            errors[field] = FIELD_ERRORS[field]
        cleaned[field] = value
    number = (data.get("account_number") or "").strip()
    if not NUMBER_RE.fullmatch(number) or not re.search(r"\d", number):
        errors["account_number"] = FIELD_ERRORS["account_number"]
    cleaned["account_number"] = number
    if "qr" in data or (files is not None and "qr" in files):
        errors["qr"] = QR_NOT_YET
    if errors:
        raise _invalid(errors)
    return cleaned


def _account_event(account, event, site, row):
    # Which account and what kind only: no name or number reaches an audit record.
    record_event(account, event, site=site.public_id, payment_account=row.public_id, kind=row.kind)


@transaction.atomic
def add_account(account, site, data, files=None):
    cleaned = clean_account(data, files)
    site = _locked_site(account, site)
    row = SitePaymentAccount.objects.create(site=site, **cleaned)
    _account_event(account, "payment_account_added", site, row)
    return row


@transaction.atomic
def update_account(account, site, account_id, data, files=None):
    """Change an account that is still listed. A removed one, or another site's, is not found.
    The 404 comes before the fields are looked at."""
    site = _locked_site(account, site)
    row = (
        SitePaymentAccount.objects.select_for_update()
        .filter(site=site, public_id=account_id, removed_at__isnull=True)
        .first()
    )
    if row is None:
        raise not_found()
    for field, value in clean_account(data, files).items():
        setattr(row, field, value)
    row.save()
    _account_event(account, "payment_account_changed", site, row)
    return row


@transaction.atomic
def remove_account(account, site, account_id):
    site = _locked_site(account, site)
    row = (
        SitePaymentAccount.objects.select_for_update()
        .filter(site=site, public_id=account_id, removed_at__isnull=True)
        .first()
    )
    if row is None:
        raise not_found()
    row.removed_at = timezone.now()
    row.save(update_fields=["removed_at", "updated_at"])
    _account_event(account, "payment_account_removed", site, row)


def account_out(row):
    return {
        "id": row.public_id,
        "kind": row.kind,
        "account_type": row.account_type,
        "provider_name": row.provider_name,
        "account_name": row.account_name,
        "account_number": row.account_number,
        "account_number_last4": row.account_number[-4:],
        "has_qr": False,  # no QR is stored until private file storage exists
    }


def settings_out(site):
    """What the Subscriber's Payments page reads."""
    return {
        "enabled": methods_enabled(site),
        "available": methods_available(site),
        "accounts": [account_out(account) for account in active_accounts(site)],
        "xendit": dict(XENDIT_NOT_SET_UP),
    }
