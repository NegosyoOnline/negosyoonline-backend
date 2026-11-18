"""What account disposal and account export do to an account's payment rows.

Called only by apps.accounts.retention. While PAYMENT_METHODS_ENABLED is off every function
here answers empty and reads no payment table.
"""
from apps.common import private_files
from apps.payments import services
from apps.payments.models import SitePaymentAccount, SitePaymentSettings, SiteXenditCredential
from apps.sites.models import Site

ACCOUNTS, SETTINGS = "site_payment_accounts", "site_payment_settings"
QR_FILES = "site_payment_qr_files"
XENDIT_KEYS = "site_xendit_credentials"
ACCOUNT_PERSONAL = ("account_name", "account_number", "qr_file_key")


def _iso(moment):
    return moment.isoformat() if moment else None


def blanked():
    """The field names a disposal blanks, for its audit entry."""
    if not services.enabled():
        return {}
    return {ACCOUNTS: list(ACCOUNT_PERSONAL)}


def blank_payments_of(account, now, dry_run=False):
    """Blank the account name and number of every payment account of the account's sites,
    delete each one's QR code file from the private store and mark each removed. The rows
    stay. Returns the counts; a dry run only counts. Call it inside a transaction, after the
    account's site lock is taken.

    A file that cannot be deleted raises private_files.StoreError, so the disposal is undone
    and can be run again: no file is left behind with no row that names it. Deleting a file
    that is already gone succeeds."""
    return blank_payments(Site.objects.filter(account=account), now, dry_run)


def blank_payments(sites, now, dry_run=False, *, ignore_switch=False):
    """The same for the sites in `sites`. `ignore_switch` reads the table whatever
    PAYMENT_METHODS_ENABLED says (the site erasure, plan D13); the default is unchanged."""
    if not ignore_switch and not services.enabled():
        return {}
    accounts = SitePaymentAccount.objects.filter(site__in=sites)
    keys = list(accounts.exclude(qr_file_key="").values_list("qr_file_key", flat=True))
    credentials = SiteXenditCredential.objects.filter(site__in=sites)
    holding = credentials.exclude(api_key_ciphertext="", webhook_token_ciphertext="")
    counts = {ACCOUNTS: accounts.count(), QR_FILES: len(keys)}
    if credentials.exists():
        counts[XENDIT_KEYS] = holding.count()
    if not dry_run:
        # The owner's own Xendit key and token are wiped on disposal; the row and its webhook key stay.
        holding.update(api_key_ciphertext="", webhook_token_ciphertext="", key_mode="")
        for key in keys:
            private_files.delete(key)
        accounts.filter(removed_at__isnull=True).update(removed_at=now)
        accounts.update(**{name: "" for name in ACCOUNT_PERSONAL})
    return counts


def export_payments_of(account):
    """The payment rows of the account's sites as plain values. Reads only. The Xendit parts appear
    only where a site has them: `xendit_enabled` on a settings row that switched it on, and the
    `site_xendit_credentials` list when a key row exists. Never the key, the token or their ciphertext."""
    if not services.enabled():
        return {}
    public_ids = dict(Site.objects.filter(account=account).values_list("pk", "public_id"))
    settings_rows = []
    for row in SitePaymentSettings.objects.filter(site__account=account).order_by("pk"):
        item = {
            "site": public_ids[row.site_id],
            "direct_enabled": row.direct_enabled,
            "updated_at": _iso(row.updated_at),
        }
        if row.xendit_enabled:
            item["xendit_enabled"] = True
        settings_rows.append(item)
    out = {
        ACCOUNTS: [
            {
                "site": public_ids[row.site_id],
                "id": row.public_id,
                "kind": row.kind,
                "account_type": row.account_type,
                "provider_name": row.provider_name,
                "account_name": row.account_name,
                "account_number": row.account_number,
                "has_qr": bool(row.qr_file_key),
                "created_at": _iso(row.created_at),
                "removed_at": _iso(row.removed_at),
            }
            for row in SitePaymentAccount.objects.filter(site__account=account).order_by("pk")
        ],
        SETTINGS: settings_rows,
    }
    credentials = list(SiteXenditCredential.objects.filter(site__account=account).order_by("pk"))
    if credentials:
        out[XENDIT_KEYS] = [
            {
                "site": public_ids[row.site_id], "configured": row.configured, "key_mode": row.key_mode,
                "key_failed_at": _iso(row.key_failed_at), "updated_at": _iso(row.updated_at),
            }
            for row in credentials
        ]
    return out
