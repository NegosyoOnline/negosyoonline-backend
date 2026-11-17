"""Gunicorn access-log masking. Pure and importable without gunicorn running.

Meta's webhook verify GET carries `hub.verify_token` in the query string, and gunicorn's
access log prints the whole request line. `mask_query` replaces that value with `***`.
"""
import re

MASK = "***"
# hub.verify_token, hub_verify_token and hub%2Everify_token (any case), up to the next & or space.
_VERIFY_TOKEN = re.compile(r"(hub(?:\.|_|%2e)verify_token=)[^&\s#]*", re.IGNORECASE)


def mask_query(value):
    """Mask the verify-token value in a query string or request line; other text is untouched."""
    if not isinstance(value, str) or "verify_token" not in value.lower():
        return value
    return _VERIFY_TOKEN.sub(lambda m: m.group(1) + MASK, value)


# Atoms that carry the raw query string or URI.
_MASKED_ATOMS = ("r", "q", "{raw_uri}e", "{query_string}e", "{request_uri}e")

try:
    from gunicorn.glogging import Logger
except ImportError:  # pragma: no cover - gunicorn is always installed in deployments
    Logger = object


class MaskingLogger(Logger):
    """Gunicorn logger that masks the verify token in every access-log atom."""

    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key in _MASKED_ATOMS:
            if key in atoms:
                atoms[key] = mask_query(atoms[key])
        return atoms
