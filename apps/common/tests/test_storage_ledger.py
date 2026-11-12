"""Every upload folder in the code has a row in docs/runbooks/storage.md.

The folders are found by scanning the source, not by importing constants: they are named
differently in each app (FOLDER, QR_FOLDER, PROOF_FOLDER, SEAL_FOLDER) and some are formats
(`site-{}` or `/site-<id>` suffixes), so a scan of the names and of literal folders passed to
`private_files.save` / `new_key` also catches a new folder written inline.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
LEDGER = ROOT / "docs" / "runbooks" / "storage.md"
CONSTANT = re.compile(r'^\s*[A-Z_]*FOLDER\s*=\s*"([^"]+)"', re.M)
INLINE = re.compile(r'(?:private_files\.save|new_key)\(\s*f?"([a-z0-9][^"{]*?)(?:/site-|")')


def _sources():
    for path in sorted((ROOT / "apps").rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        if "tests" in parts or "migrations" in parts or path.name.startswith("test_"):
            continue
        yield path


def _folders():
    found = {}
    for path in _sources():
        text = path.read_text()
        for match in CONSTANT.finditer(text):
            found[re.sub(r"/site-.*$", "", match.group(1).split("{")[0]).rstrip("/")] = path
        for match in INLINE.finditer(text):
            found[match.group(1).rstrip("/")] = path
    return found


class StorageLedgerTests(SimpleTestCase):
    def test_scan_sees_the_known_folders(self):
        # Guards the scan itself: if it finds fewer than these it has stopped working.
        known = {"sante/qr", "sante/agreement", "sante/proofs", "sites/seal", "billing/proofs",
                 "catalog/products"}
        self.assertTrue(known <= set(_folders()), set(_folders()))

    def test_every_folder_has_a_ledger_row(self):
        ledger = LEDGER.read_text()
        rows = [line for line in ledger.splitlines() if line.startswith("|")]
        missing = [
            f"{folder} ({path.relative_to(ROOT)})"
            for folder, path in sorted(_folders().items())
            if not any(f"`{folder}" in row for row in rows)
        ]
        self.assertEqual(missing, [], "Add a row to docs/runbooks/storage.md for each folder.")
