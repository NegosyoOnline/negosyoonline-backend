"""The scans of the paper invoices. A scan has no URL: it is read only through the services
that check who is asking.

The bytes live in the private file store of `apps.common.private_files`, under the key
prefix `billing/invoice-scans/`. The type is decided by the first bytes, as for a proof.
"""
from apps.billing import proofs
from apps.common import private_files

FOLDER = "billing/invoice-scans"
MAX_BYTES = 8 * 1024 * 1024  # choice C8
CONTENT_TYPES = proofs.CONTENT_TYPES  # JPEG, PNG, PDF
FILE_NAMES = {"image/jpeg": "copy.jpg", "image/png": "copy.png", "application/pdf": "copy.pdf"}


def sniff(content):
    return proofs.sniff(content)


def save(content, content_type):
    """Store a checked scan and return its key, or raise private_files.StoreError."""
    return private_files.save(FOLDER, content, content_type)


def read(key):
    """The stored bytes. Raises private_files.StoreError, or ValueError for a bad key."""
    return private_files.read(key)


def discard(key):
    """Remove a file saved for a row that was not written. Never raises."""
    private_files.discard(key)
