"""Proof-of-payment files. A proof is personal data: it has no URL, and it is read only
through the functions in services.py that check who is asking.

The bytes live in the private file store of `apps.common.private_files` (the private Vercel
Blob store in a shared environment, a local folder while DEBUG is on), under the key prefix
`billing/proofs/`. Billing's other private files use sibling prefixes (Billing Build B5).
"""
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

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
    """`manage.py check` refuses the switch without a proof store. A running service treats it
    as the switch being off. This reads settings only, so it is safe at `migrate` time."""
    if not settings.BILLING_BANK_TRANSFER_ENABLED or available():
        return []
    return [Error(
        "BILLING_BANK_TRANSFER_ENABLED is on but no private file store is available.",
        id="billing.E002",
    )]


@register(Tags.database)
def check_receiving_account(app_configs, databases=None, **kwargs):
    """The switch is on but no active receiving account is saved: a warning, never an error.
    It reads the database, so it is a database-tagged check. In Django 5.2 `migrate` and
    `check --database <name>` pass the database names; a plain `check`, `runserver` and
    `check --deploy` pass `databases=None`. `migrate` must never fail on this (a fresh
    database has no account yet), so it is a Warning, and it returns nothing while the
    `billing_receivingaccount` table does not exist. Payment collection fails closed at
    runtime (`plan.collection_live()`); `check --database default --fail-level WARNING`
    makes this warning fail."""
    from django.db import connections

    from apps.billing import plan

    if not databases or "default" not in databases or not settings.BILLING_BANK_TRANSFER_ENABLED:
        return []
    if "billing_receivingaccount" not in connections["default"].introspection.table_names():
        return []
    if plan.has_receiving_account():
        return []
    return [Warning(
        "BILLING_BANK_TRANSFER_ENABLED is on but no active receiving account is saved.",
        hint="Add one in HQ, Finance, Accounts.",
        id="billing.W001",
    )]
