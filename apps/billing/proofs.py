"""Proof-of-payment files. A proof is personal data: it has no URL, and it is read only
through the functions in services.py that check who is asking.

The store behind `store()` is the approved private store. Until that is built the only one is
a local folder, and only while DEBUG is on.
"""
import re
import secrets
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, register

MAX_BYTES = 5 * 1024 * 1024
CONTENT_TYPES = ("image/jpeg", "image/png", "application/pdf")
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)
_KEY = re.compile(r"^[0-9a-f]{32}$")


def sniff(content):
    """The file's type by its first bytes, or None. The name and the browser's type are not used."""
    for signature, content_type in _SIGNATURES:
        if content[: len(signature)] == signature:
            return content_type
    return None


def new_key():
    """A random name for a stored file. It holds nothing about the person or the site."""
    return secrets.token_hex(16)


class LocalProofStore:
    """Files in one local folder. For a developer's machine only."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, key):
        if not _KEY.fullmatch(key):
            raise ValueError("Not a proof key.")
        return self.directory / key

    def put(self, key, content):
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(key).write_bytes(content)

    def get(self, key):
        return self._path(key).read_bytes()


def available():
    """Whether a store may be used here."""
    return bool(settings.DEBUG and settings.BILLING_PROOF_DIR)


def store():
    if not available():
        raise RuntimeError("No proof store is available.")
    return LocalProofStore(settings.BILLING_PROOF_DIR)


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
            "BILLING_BANK_TRANSFER_ENABLED is on but no proof store is available.",
            id="billing.E002",
        ))
    return errors
