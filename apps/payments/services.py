"""A site's payment methods. Other apps call these functions, never the tables.

Everything here is behind PAYMENT_METHODS_ENABLED. While it is off the routes answer 404, the
two tables are not read or written: the code is released
before its tables are created.

A method is usable at checkout only while it is switched on and set up. The store's own
receiving account numbers go back in full to its owner.

Direct transfer (bank and e-wallet accounts) is one method. Xendit is the second, for Santé
sites only and only while the platform switch XENDIT_ENABLED is on (Coach Ed, 2026-11-18,
superseding the 2026-11-11 hiding). While that switch is off, or on a site of another template,
Xendit is not offered: it cannot be saved or switched on, and the read is exactly as it was
(Xendit shown as not set up and off, no `offered` flag). The owner's own key and webhook token are stored encrypted
(apps/payments/crypto.py); the read never returns a secret.

An account may carry a QR code image. It is a private file (apps.common.private_files): the
row keeps its key, the read says only `has_qr`, and no link to it is ever answered. The file
is stored before the row is locked, so no lock is held during an upload, and it is discarded
if the save then fails.

Lock order, as in apps.sante.services: the account's Onboarding row, then the Site row, then
a payment row.
"""
import re
import secrets as pysecrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import record_event
from apps.common import private_files
from apps.common.errors import ApiError
from apps.payments import crypto, xendit_client
from apps.payments.models import (
    BANK, CHECKING, EWALLET, LIVE, SAVINGS, TEST, SitePaymentAccount, SitePaymentSettings,
    SiteXenditCredential,
)
from apps.sites import templates
from apps.sites.models import Onboarding, Site
from apps.sites.services import site_for

NUMBER_RE = re.compile(r"^[0-9 -]{1,40}$")
QR_MAX = 2 * 1024 * 1024
QR_FOLDER = "sante/qr"  # then site-<row id>/<random name>: Santé's own folder in the shared store
QR_ERRORS = {
    private_files.WRONG_TYPE: "Upload a JPEG, PNG or WebP image.",
    private_files.TOO_LARGE: "The file is too large. The limit is 2 MB.",
}
QR_NOT_SAVED = "The QR code could not be saved. Try again in a moment."
XENDIT_HIDDEN = "Xendit is not available yet."
XENDIT_NEEDS_KEY = "Save a working Xendit key first."
KEY_RE = re.compile(r"^xnd_(development|production)_[A-Za-z0-9_+/=-]{1,200}$")  # Xendit keys can be base64
KEY_ERROR = "Enter a valid Xendit secret key."
KEY_PUBLIC = "This is your Xendit public key. Use your secret key, from Xendit Dashboard, Settings, API Keys."
KEY_REJECTED = "Xendit did not accept this key."
KEY_TEST_ONLY = "Use your Xendit test key (starts with xnd_development_) on this test environment."
KEY_LIVE_ONLY = "Use your live Xendit key (starts with xnd_production_)."
TOKEN_ERROR = "Enter the webhook verification token."
XENDIT_UNREACHABLE = "Xendit is not reachable right now. Try again shortly."
PAYMENTS_UNAVAILABLE = "Payment settings are not available yet."
FIELD_ERRORS = {
    "kind": "Choose bank or e-wallet.",
    "provider_name": "Enter the bank or e-wallet name.",
    "account_name": "Enter the account name.",
    "account_number": "Enter the account number.",
    "account_type": "Choose Savings or Checking.",
}
ACCOUNT_FIELDS = ("kind", "account_type", "provider_name", "account_name", "account_number")
# What the read says of Xendit while it is not offered: not set up.
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


def xendit_offered(site):
    """Xendit exists for this site: the platform switch is on and the site is a Santé site."""
    return bool(settings.XENDIT_ENABLED and site.template == templates.SANTE)


def _credential(site):
    return SiteXenditCredential.objects.filter(site=site).first()


def xendit_working(site):
    """A key is saved, it has not failed, and the encryption key can read it."""
    row = _credential(site)
    return bool(row and row.configured and row.key_failed_at is None and crypto.key_ready())


def methods_enabled(site):
    """The owner's switches. No row means direct transfer is on. Xendit is on only when it is
    offered and the owner switched it on."""
    row = SitePaymentSettings.objects.filter(site=site).first()
    xendit = bool(row is not None and row.xendit_enabled and xendit_offered(site))
    return {"direct": True if row is None else row.direct_enabled, "xendit": xendit}


def methods_available(site):
    """What a buyer may choose at checkout: switched on and set up. Direct transfer is set up
    once the site has an account; Xendit once a working key is saved."""
    enabled = methods_enabled(site)
    direct = enabled["direct"] and active_accounts(site).exists()
    return {"direct": direct, "xendit": enabled["xendit"] and xendit_working(site)}


def any_method(site):
    """Direct transfer is usable. (The product shop and the pack's proof path are direct only.)"""
    return methods_available(site)["direct"]


@transaction.atomic
def set_enabled(account, site, *, direct=None, xendit=None):
    """Switch direct transfer and Xendit on or off; None leaves it as it is. Off hides the
    method from new checkouts only: its accounts and key stay saved. Xendit True needs it to be
    offered and a working key saved; otherwise nothing is written."""
    offered = xendit_offered(site)
    if xendit and not offered:
        raise _invalid({"xendit_enabled": XENDIT_HIDDEN})
    if not offered:
        xendit = None  # not offered: always off, nothing to write
    if direct is None and xendit is None:
        return None
    site = _locked_site(account, site)
    if xendit and not xendit_working(site):
        raise _invalid({"xendit_enabled": XENDIT_NEEDS_KEY})
    row, _ = SitePaymentSettings.objects.select_for_update().get_or_create(site=site)
    detail = {}
    if direct is not None:
        row.direct_enabled = detail["direct_enabled"] = direct
    if xendit is not None:
        row.xendit_enabled = detail["xendit_enabled"] = xendit
    row.save()
    record_event(account, "payment_methods_switched", site=site.public_id, **detail)
    return row


# ---- bank and e-wallet accounts ----

def clean_account(data, files=None):
    """(account fields, QR) of a submitted form, or ApiError naming each bad field. The QR is
    None when no file was sent, else its (content, content type), checked: a JPEG, PNG or WebP
    image of at most 2 MB."""
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
    qr, upload = None, (files or {}).get("qr")
    if upload is not None or "qr" in data:  # `qr` sent as text is not a file
        try:
            qr = private_files.check(upload, allowed=private_files.IMAGE_TYPES, max_bytes=QR_MAX)
        except private_files.FileRefused as refused:
            errors["qr"] = QR_ERRORS[refused.reason]
    if errors:
        raise _invalid(errors)
    return cleaned, qr


def _store_qr(site, qr):
    """The key of the stored QR image; "" when none was sent."""
    if qr is None:
        return ""
    content, content_type = qr
    try:
        return private_files.save(f"{QR_FOLDER}/site-{site.pk}", content, content_type)
    except private_files.StoreError:
        raise _invalid({"qr": QR_NOT_SAVED}) from None


def _listed(site, account_id):
    return SitePaymentAccount.objects.filter(
        site=site, public_id=account_id, removed_at__isnull=True
    )


def _account_event(account, event, site, row):
    # Which account and what kind only: no name or number reaches an audit record.
    record_event(account, event, site=site.public_id, payment_account=row.public_id, kind=row.kind)


def add_account(account, site, data, files=None):
    cleaned, qr = clean_account(data, files)
    key = _store_qr(site, qr)
    try:
        with transaction.atomic():
            site = _locked_site(account, site)
            row = SitePaymentAccount.objects.create(site=site, qr_file_key=key, **cleaned)
            _account_event(account, "payment_account_added", site, row)
    except BaseException:
        private_files.discard(key)
        raise
    return row


def update_account(account, site, account_id, data, files=None):
    """Change an account that is still listed. A removed one, or another site's, is not found.
    The 404 comes before the fields are looked at. No new QR keeps the current one; a new QR
    replaces it, and the old file is removed once the change is committed."""
    if not _listed(site, account_id).exists():
        raise not_found()
    cleaned, qr = clean_account(data, files)
    key = _store_qr(site, qr)
    try:
        with transaction.atomic():
            site = _locked_site(account, site)
            row = _listed(site, account_id).select_for_update().first()
            if row is None:
                raise not_found()
            old_key = row.qr_file_key
            for field, value in cleaned.items():
                setattr(row, field, value)
            if key:
                row.qr_file_key = key
            row.save()
            _account_event(account, "payment_account_changed", site, row)
            if key and old_key:
                transaction.on_commit(lambda: private_files.discard(old_key))
    except BaseException:
        private_files.discard(key)
        raise
    return row


@transaction.atomic
def remove_account(account, site, account_id):
    """Mark an account removed. Its QR file is kept with the row, as the row is: a payment
    proof will point at the account it was sent to. Account disposal removes the file."""
    site = _locked_site(account, site)
    row = _listed(site, account_id).select_for_update().first()
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
        "has_qr": bool(row.qr_file_key),  # the file's key and link are never answered
    }


# ---- the owner's own Xendit key ----

def key_mode(api_key):
    """"test" or "live" by the key's prefix, or ApiError naming the field."""
    if api_key.startswith("xnd_public_"):
        raise _invalid({"api_key": KEY_PUBLIC})
    if not KEY_RE.fullmatch(api_key):
        raise _invalid({"api_key": KEY_ERROR})
    return LIVE if api_key.startswith("xnd_production_") else TEST


def environment_allows(mode):
    """Production takes live keys only; staging takes test keys only (Railway names the
    environment). With no environment name (a local run) either mode is allowed only while DEBUG
    is on; any other run without a name refuses both."""
    env = settings.RAILWAY_ENVIRONMENT_NAME
    if env == "production":
        return mode == LIVE
    if env:
        return mode == TEST
    return bool(settings.DEBUG)


def require_offered(site):
    if not xendit_offered(site):
        raise not_found()


def save_xendit(account, site, *, api_key, webhook_token):
    """Check the key with one read at Xendit, then save both secrets encrypted. Saving does not
    switch Xendit on; a key that passes clears key_failed. The secrets reach no log."""
    require_offered(site)
    api_key, webhook_token = (api_key or "").strip(), (webhook_token or "").strip()
    mode = key_mode(api_key)
    if not environment_allows(mode):
        raise _invalid({"api_key": KEY_LIVE_ONLY if mode == TEST else KEY_TEST_ONLY})
    if not 1 <= len(webhook_token) <= 200:
        raise _invalid({"webhook_token": TOKEN_ERROR})
    try:
        key_ct, token_ct = crypto.encrypt(api_key), crypto.encrypt(webhook_token)
    except crypto.SecretKeyError:
        raise ApiError("payments_unavailable", PAYMENTS_UNAVAILABLE, status=503) from None
    try:
        xendit_client.check_key(api_key)
    except xendit_client.XenditError as exc:
        if exc.kind == "unauthorized":
            raise _invalid({"api_key": KEY_REJECTED}) from None
        raise ApiError("xendit_unavailable", XENDIT_UNREACHABLE, status=503) from None
    with transaction.atomic():
        site = _locked_site(account, site)
        row = SiteXenditCredential.objects.select_for_update().filter(site=site).first()
        if row is None:
            row = SiteXenditCredential(site=site)
        row.api_key_ciphertext, row.webhook_token_ciphertext, row.key_mode = key_ct, token_ct, mode
        row.key_failed_at = None
        row.actor_account_id = account.pk
        row.save()
        record_event(account, "xendit_key_saved", site=site.public_id, key_mode=mode)
    return row


def clear_xendit(account, site):
    """Blank the API key and switch Xendit off. The row, its webhook_key and its webhook token
    stay: the webhook keeps applying a payment on an invoice that is still open (a buyer who
    pays after the owner removed the key is still verified), and a re-save keeps the same
    webhook URL. Returns how many orders still wait for a Xendit payment (with no key they are
    not read; each expires on its own when its payment window ends, and a late authenticated
    PAID still applies)."""
    require_offered(site)
    from apps.pack import xendit as pack_xendit
    with transaction.atomic():
        site = _locked_site(account, site)
        SiteXenditCredential.objects.select_for_update().filter(site=site).update(
            api_key_ciphertext="", key_mode="", key_failed_at=None, actor_account_id=account.pk,
        )
        SitePaymentSettings.objects.filter(site=site).update(xendit_enabled=False)
        record_event(account, "xendit_key_removed", site=site.public_id)
    return pack_xendit.awaiting_count(site)


def api_key_for(site):
    """The site's decrypted Xendit key, or None when none is saved or it cannot be read."""
    row = _credential(site)
    if row is None or not row.configured:
        return None
    try:
        return crypto.decrypt(row.api_key_ciphertext)
    except crypto.SecretKeyError:
        return None


def mark_key_failed(site):
    """Xendit answered unauthorized: stop offering Xendit (key_failed_at) and tell the owner by
    email once. Never logs the key."""
    from apps.notifications.services import send_email
    changed = SiteXenditCredential.objects.filter(
        site=site, key_failed_at__isnull=True
    ).update(key_failed_at=timezone.now())
    owner = site.account
    if changed and owner.email and owner.email_verified_at:
        # After the surrounding transaction commits (at once when there is none), so a rollback
        # sends no email and the mail provider is never called while a row lock is held.
        transaction.on_commit(lambda: send_email(
            to=owner.email, category="xendit_key_failed", subject="Your Xendit key no longer works",
            body="Your Xendit key no longer works. Save a working key to accept Xendit payments again.\n"
                 "Until then, buyers do not see the Xendit option. Update the key in your NegosyoOnline "
                 "Dashboard under Payments.",
        ))


def webhook_url(row, api_base):
    if row is None or not api_base:
        return ""
    return f"{api_base.rstrip('/')}/v1/webhooks/xendit/{row.webhook_key}/"


def xendit_out(site, api_base):
    row = _credential(site)
    configured = row is not None and row.configured
    failed_at = row.key_failed_at if configured else None
    return {
        "offered": True, "configured": configured, "key_mode": row.key_mode if configured else "",
        "webhook_url": webhook_url(row, api_base), "key_failed": failed_at is not None,
        "key_failed_at": failed_at.isoformat() if failed_at else None,
    }


def settings_out(site, api_base=""):
    """What the Subscriber's Payments page reads. Xendit not offered: exactly as it was before
    Xendit. Offered: the `xendit` block carries `offered: true` and the real values; a secret is
    never in it."""
    body = {
        "enabled": methods_enabled(site),
        "available": methods_available(site),
        "accounts": [account_out(account) for account in active_accounts(site)],
        "xendit": dict(XENDIT_NOT_SET_UP),
    }
    if xendit_offered(site):
        body["xendit"] = xendit_out(site, api_base)
    return body
