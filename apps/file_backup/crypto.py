"""Client-side encryption of the backup: AES-256-GCM, one random 12-byte nonce per object, and
the object's name as associated data, so a copy cannot be swapped under another name.

Stored form: version byte | nonce | ciphertext + 16-byte tag (29 bytes more than the plaintext).
The key is 32 random bytes, base64 in FILE_BACKUP_ENCRYPTION_KEY. Lose it and the backup cannot
be read. No message here ever holds the key, a name or content.
"""
import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = b"\x01"
NONCE_BYTES = 12
OVERHEAD = len(VERSION) + NONCE_BYTES + 16


class CryptoError(Exception):
    """The key is unusable, or a stored object failed its authentication check."""


def load_key(encoded):
    """The 32 key bytes from their base64 text, or CryptoError."""
    try:
        key = base64.b64decode(encoded or "", validate=True)
    except (binascii.Error, ValueError):
        raise CryptoError("The backup encryption key is not base64.") from None
    if len(key) != 32:
        raise CryptoError("The backup encryption key is not 32 bytes.")
    return key


def encrypt(key, name, plaintext):
    nonce = os.urandom(NONCE_BYTES)
    return VERSION + nonce + AESGCM(key).encrypt(nonce, plaintext, name.encode())


def decrypt(key, name, stored):
    if len(stored) < OVERHEAD or stored[:1] != VERSION:
        raise CryptoError("A backup object is not in a known format.")
    nonce = stored[1 : 1 + NONCE_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, stored[1 + NONCE_BYTES :], name.encode())
    except InvalidTag:
        raise CryptoError("A backup object failed its authentication check.") from None
