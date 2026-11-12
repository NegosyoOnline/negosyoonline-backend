"""Proof-of-payment files. A proof is personal data: it has no URL, and it is read only
through the functions in services.py that check who is asking.

The bytes live in the private file store of `apps.common.private_files` (the private Vercel
Blob store in a shared environment, a local folder while DEBUG is on), under the key prefix
`billing/proofs/`. Billing's other private files use sibling prefixes (Billing Build B5).
"""
from django.conf import settings
from django.core.checks import Error, register

from apps.common import private_files

FOLDER = "billing/proofs"
MAX_BYTES = 5 * 1024 * 1024
CONTENT_TYPES = ("image/jpeg", "image/png", "application/pdf")
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)


def sniff(content):
    """The file's type by its first bytes, or None. The name and the browser's type are not used."""
    for signature, content_type in _SIGNATURES:
        if content[: len(signature)] == signature:
            return content_type
    return None


def available():
    """Whether a private store may be used here."""
    return private_files.available()


def save(content, content_type):
    """Store a checked proof and return its key, or raise private_files.StoreError."""
    return private_files.save(FOLDER, content, content_type)


def read(key):
    """The stored bytes. Raises private_files.StoreError, or ValueError for a key that is
    not a private store key."""
    return private_files.read(key)


def discard(key):
    """Remove a file saved for a row that was not written. Never raises."""
    private_files.discard(key)


@register()
def check_bank_transfer(app_configs, **kwargs):
    """`manage.py check` refuses the switch without an account to pay to or a proof store.
    A running service treats either as the switch being off."""
    from apps.billing import plan

    if not settings.BILLING_BANK_TRANSFER_ENABLED:
        return []
    errors = []
    if not plan.transfer_accounts():
        errors.append(Error(
            "BILLING_BANK_TRANSFER_ENABLED is on but BILLING_TRANSFER_ACCOUNTS holds no complete account.",
            id="billing.E001",
        ))
    if not available():
        errors.append(Error(
            "BILLING_BANK_TRANSFER_ENABLED is on but no private file store is available.",
            id="billing.E002",
        ))
    return errors
