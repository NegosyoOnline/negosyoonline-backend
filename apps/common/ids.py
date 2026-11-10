"""Opaque public ids such as ``site_3k9x...``. Database primary keys never leave the API."""
import re
import secrets

# Crockford base32, lowercase: no i, l, o or u, so ids are hard to misread.
ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
RANDOM_LENGTH = 20  # 20 characters of 5 bits each: 100 bits
_PREFIX = re.compile(r"^[a-z]{2,8}$")


def new_public_id(prefix: str) -> str:
    if not _PREFIX.match(prefix):
        raise ValueError("prefix must be 2 to 8 lowercase letters")
    return prefix + "_" + "".join(secrets.choice(ALPHABET) for _ in range(RANDOM_LENGTH))


def is_public_id(value: str, prefix: str) -> bool:
    pattern = rf"^{re.escape(prefix)}_[{ALPHABET}]{{{RANDOM_LENGTH}}}$"
    return isinstance(value, str) and re.match(pattern, value) is not None
