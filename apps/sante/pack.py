"""What a Santé site's buyers may ask about the Membership Pack: its price, whether it can be
bought, the agreement and the accounts to pay. Reads only; checkout is a later slice.

The price is a code constant, not a catalog product. Money is integer centavos.
"""
from apps.common import private_files
from apps.payments import services as payments
from apps.payments.models import SitePaymentAccount
from apps.sante import agreement

PRICE_CENTAVOS = 148800  # PHP 1,488.00
CURRENCY = "PHP"


def accounts(site):
    """The accounts a buyer may pay to: none while direct transfer is off or none is set up."""
    if not payments.any_method(site):
        return []
    return [payments.account_out(row) for row in payments.active_accounts(site)]


def pack_out(site):
    """The pack as the site's buyers see it. Available only with a payment account to pay to
    and a published agreement version (TWCinc: acceptance.available and the payment method)."""
    pay_to = accounts(site)
    active = agreement.active_version()
    return {
        "price_centavos": PRICE_CENTAVOS,
        "currency": CURRENCY,
        "available": bool(pay_to) and active is not None,
        "agreement": (
            {"id": active.pk, "label": active.version_label, "effective_date": active.effective_date.isoformat()}
            if active else None
        ),
        "payment_accounts": pay_to,
    }


def qr_of(site, account_id):
    """(bytes, content type) of an active account's QR code on this site, or None."""
    row = SitePaymentAccount.objects.filter(
        site=site, public_id=account_id, removed_at__isnull=True
    ).exclude(qr_file_key="").first()
    if row is None or not payments.any_method(site):
        return None
    try:
        data = private_files.read(row.qr_file_key)
    except Exception:  # noqa: BLE001 - an unreadable object reads as missing
        return None
    content_type = private_files.sniff(data[:16])
    return (data, content_type) if content_type.startswith("image/") else None
