"""Six-digit email codes, held in the cache with an expiry and an attempt limit."""
import hashlib
import hmac
import secrets

from django.conf import settings
from django.core.cache import cache

from apps.common.errors import ApiError

CODE_TTL = 600  # seconds
MAX_ATTEMPTS = 5
RESEND_SECONDS = 60
PER_EMAIL_PER_HOUR = 5
PER_IP_PER_HOUR = 20
HOUR = 3600


def _key(kind, purpose, value):
    digest = hashlib.sha256(f"{purpose}|{value}".encode()).hexdigest()[:32]
    return f"emailcode:{kind}:{digest}"


def _code_hash(code):
    return hmac.new(settings.SECRET_KEY.encode(), str(code).encode(), hashlib.sha256).hexdigest()


def _count(key, window):
    """Add one to a counter that expires `window` seconds after its first hit."""
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:  # the key expired between add and incr
        cache.set(key, 1, window)
        return 1


def _rate_limited(message):
    return ApiError("rate_limited", message, status=429)


def issue_code(email, ip, purpose="signin"):
    """Return a new code for this email, replacing any earlier one. Raises ApiError 429."""
    if not cache.add(_key("cooldown", purpose, email), 1, RESEND_SECONDS):
        raise _rate_limited("Wait a minute before asking for another code.")
    if _count(_key("email-hour", purpose, email), HOUR) > PER_EMAIL_PER_HOUR:
        raise _rate_limited("Too many codes requested. Try again later.")
    if _count(_key("ip-hour", "any", ip), HOUR) > PER_IP_PER_HOUR:
        raise _rate_limited("Too many codes requested. Try again later.")
    code = f"{secrets.randbelow(10**6):06d}"
    cache.set(_key("code", purpose, email), _code_hash(code), CODE_TTL)
    cache.delete(_key("attempts", purpose, email))
    return code


def check_code(email, code, purpose="signin"):
    """True when the code is right. Does not consume it; call clear_code() once it is used."""
    stored = cache.get(_key("code", purpose, email))
    if stored is None:
        return False
    if _count(_key("attempts", purpose, email), CODE_TTL) > MAX_ATTEMPTS:
        clear_code(email, purpose)
        return False
    return hmac.compare_digest(stored, _code_hash(code))


def clear_code(email, purpose="signin"):
    cache.delete_many([_key("code", purpose, email), _key("attempts", purpose, email)])


def claim_code(email, purpose="signin"):
    """Use the code up. True for the one caller that removed it, so two simultaneous requests
    with the same right code cannot both succeed."""
    won = cache.delete(_key("code", purpose, email))
    cache.delete(_key("attempts", purpose, email))
    return bool(won)
