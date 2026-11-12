"""Encryption of a site's Meta access token at rest, under META_CREDENTIALS_KEY (a Fernet key)."""
import binascii

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenKeyError(Exception):
    """The key is empty or invalid, or the value was not encrypted with it."""


def _fernet():
    key = settings.META_CREDENTIALS_KEY
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
        raise TokenKeyError()
    return fernet.encrypt(text.encode()).decode()


def decrypt(token):
    fernet = _fernet()
    if fernet is None or not token:
        raise TokenKeyError()
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        raise TokenKeyError() from None
