"""Authenticator-app codes: RFC 6238 with SHA-1, 6 digits and 30-second steps."""
import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

PERIOD = 30
DIGITS = 6
ISSUER = "NegosyoOnline admin"


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def current_step(now):
    return int(now.timestamp()) // PERIOD


def code_at(secret, step):
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10**DIGITS:0{DIGITS}d}"


def matching_step(secret, code, now):
    """The time step this code belongs to (now, or one either side), or None."""
    if not isinstance(code, str) or len(code) != DIGITS or not code.isdigit():
        return None
    current = current_step(now)
    found = None
    for step in (current - 1, current, current + 1):
        if hmac.compare_digest(code_at(secret, step), code) and found is None:
            found = step
    return found


def setup_link(secret, email):
    label = quote(f"{ISSUER}:{email}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )
