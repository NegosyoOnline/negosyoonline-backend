"""Encryption of a site's Xendit key and webhook token at rest, under PAYMENT_CREDENTIALS_KEY (a
Fernet key). The key is the platform's existing per-site credential key; no new variable. Same
shape as apps/inquiries/crypto.py. Nothing here logs a plaintext or a ciphertext."""
import binascii

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretKeyError(Exception):
    """The key is empty or invalid, or the value was not encrypted with it."""


def _fernet():
    key = settings.PAYMENT_CREDENTIALS_KEY
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except (ValueError, binascii.Error):
        return None


def key_ready():
    return _fernet() is not None


def encrypt(text):
    fernet = _fernet()
    if fernet is None:
        raise SecretKeyError()
    return fernet.encrypt(text.encode()).decode()


def decrypt(token):
    fernet = _fernet()
    if fernet is None or not token:
        raise SecretKeyError()
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        raise SecretKeyError() from None
