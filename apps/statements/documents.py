"""Where a statement's PDF is rendered and stored (Billing Build B5).

PR 5 only fixes the seam: issuing hands the frozen document to `store_document` and takes back
the key, SHA-256 and size it must record. The renderer and the private store arrive in PR 6;
until then the store is unavailable, so nothing is issued and the safety net keeps trying.
"""
from typing import NamedTuple


class StoreUnavailable(Exception):
    """The private store (or the renderer) cannot take the document now."""


class Stored(NamedTuple):
    key: str
    sha256: str
    size: int


def store_document(document):
    """Render `document` once and store it. Returns `Stored`. Replaced in PR 6."""
    raise StoreUnavailable("The private store is not configured.")
