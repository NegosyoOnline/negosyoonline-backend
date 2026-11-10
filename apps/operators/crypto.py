"""Encryption of authenticator secrets at rest, under ADMIN_SECRETS_KEY (a Fernet key)."""
import binascii

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretsKeyError(Exception):
    """The key is empty or invalid, or the value was not encrypted with it."""


def _fernet():
    key = settings.ADMIN_SECRETS_KEY
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
        raise SecretsKeyError()
    return fernet.encrypt(text.encode()).decode()


def decrypt(token):
    fernet = _fernet()
    if fernet is None or not token:
        raise SecretsKeyError()
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        raise SecretsKeyError() from None
