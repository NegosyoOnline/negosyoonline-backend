"""Where a statement's PDF is rendered and stored (Billing Build B5).

Issuing hands the frozen document to `store_document` and takes back the key, SHA-256 and size
it must record. The PDF is rendered once, here, from that frozen document and is never rendered
again from current data. The file goes to the shared private store (`apps.common.private_files`)
under a random key; no URL is ever made for it. Only `read_pdf` hands the bytes back, to a route
that has already checked who is asking.
"""
import hashlib
from typing import NamedTuple

from apps.common import private_files
from apps.statements import pdf

FOLDER = "statements/pdf"
CONTENT_TYPE = "application/pdf"


class StoreUnavailable(Exception):
    """The private store (or the renderer) cannot take the document now."""


class Stored(NamedTuple):
    key: str
    sha256: str
    size: int


def store_document(document):
    """Render `document` once and store it. Returns `Stored`; raises `StoreUnavailable` with
    nothing stored when the store is missing or refuses the file."""
    if not private_files.available():
        raise StoreUnavailable("The private store is not configured.")
    content = pdf.render(document)
    try:
        key = private_files.save(FOLDER, content, CONTENT_TYPE)
    except private_files.StoreError as error:
        raise StoreUnavailable("The private store could not take the statement.") from error
    return Stored(key=key, sha256=hashlib.sha256(content).hexdigest(), size=len(content))


def discard(key):
    """Remove a file stored for a statement that was then not written. Never raises."""
    private_files.discard(key)


def read_pdf(statement):
    """The stored PDF bytes of a statement, checked against the hash recorded at issue.
    Raises `StoreUnavailable` if the store cannot answer or the file is not the one issued.
    The caller has already checked who is asking."""
    try:
        content = private_files.read(statement.pdf_key)
    except (private_files.StoreError, ValueError, OSError) as error:
        raise StoreUnavailable("The statement file could not be read.") from error
    if hashlib.sha256(content).hexdigest() != statement.pdf_sha256:
        raise StoreUnavailable("The statement file does not match its recorded hash.")
    return content
