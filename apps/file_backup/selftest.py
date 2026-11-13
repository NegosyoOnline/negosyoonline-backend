"""The backup-and-restore proof, with synthetic test files only.

It writes three fake files under the folder `backup-selftest/` of the live private store, backs up
only that prefix into the bucket under `selftest/`, deletes the live copies, restores them, and
compares byte for byte. It then checks that a second restore changes nothing, that a tampered
copy and a wrong key are refused, and removes everything it made. No real member file is read,
copied or restored. It returns [(check, passed)] and never names a pathname.
"""
import hashlib
import os

from apps.common import private_files
from apps.file_backup import crypto, restore, service

FOLDER = "backup-selftest"
ROOT = "selftest/"
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9aq\xdf"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
TINY_PDF = b"%PDF-1.4\n% backup self-test file, not real data\n%%EOF\n"


def run(source, dest, key):
    results = []

    def check(name, passed):
        results.append((name, bool(passed)))

    originals = {}
    cfg = service.Config(key=key, root=ROOT, prefix=f"{FOLDER}/", sample=10)
    try:
        for content, content_type in (
            (TINY_PNG, "image/png"),
            (TINY_PDF, "application/pdf"),
            (b"\x89PNG\r\n\x1a\n" + os.urandom(1024 * 1024), "image/png"),
        ):
            pathname = private_files.new_key(FOLDER, content_type)
            source.put(pathname, content, content_type)
            originals[pathname] = content
        status = service.run(source, dest, cfg)
        check("backup run finished ok and copied 3 files", status["ok"] and status["copied"] == 3)
        check(
            "stored sizes are plaintext + 29 bytes and the bytes are not the plaintext",
            all(
                len(dest.get(ROOT + service.DATA + p)) == len(c) + crypto.OVERHEAD
                and dest.get(ROOT + service.DATA + p) != c
                for p, c in originals.items()
            ),
        )
        for pathname in originals:
            source.delete(pathname)
        first = restore.restore(source, dest, cfg, prefix=cfg.prefix, apply=True)
        check("restore wrote 3 files", first["restored"] == 3 and not first["failed"] + first["mismatch"])
        same = True
        for pathname, content in originals.items():
            back, _ = source.fetch(pathname)
            same = same and back == content and hashlib.sha256(back).digest() == hashlib.sha256(content).digest()
        check("restored files equal the originals byte for byte (sha256)", same)
        second = restore.restore(source, dest, cfg, prefix=cfg.prefix, apply=True)
        check("a second restore changes nothing", second["restored"] == 0 and second["exists"] == 3)
        pathname = next(iter(originals))
        tampered = bytearray(dest.get(ROOT + service.DATA + pathname))
        tampered[-1] ^= 1
        try:
            crypto.decrypt(key, pathname, bytes(tampered))
            check("a tampered copy is rejected", False)
        except crypto.CryptoError:
            check("a tampered copy is rejected", True)
        try:
            crypto.decrypt(os.urandom(32), pathname, dest.get(ROOT + service.DATA + pathname))
            check("a wrong key is rejected", False)
        except crypto.CryptoError:
            check("a wrong key is rejected", True)
    finally:
        removed = True
        for pathname in originals:
            try:
                source.delete(pathname)
            except Exception:  # noqa: BLE001 - clean-up goes on; the result below says if it failed
                removed = False
        try:
            for stored, _ in list(dest.list(ROOT)):
                dest.delete(stored)
        except Exception:  # noqa: BLE001
            removed = False
        check("test files and the test copy are removed", removed)
    return results
