"""Gunicorn access-log masking. Pure and importable without gunicorn running.

Meta's webhook verify GET carries `hub.verify_token` in the query string, and the Messenger
OAuth callback carries `code` and `state`; gunicorn's access log prints the whole request
line. `mask_query` replaces those values with `***`, on every path. `code` and `state` match
only as whole parameter names (after `?` or `&`, or at the start of a bare query string), so
`zipcode=`, `promo_code=` and `country_state=` are left alone.
"""
import re

MASK = "***"
# hub.verify_token, hub_verify_token and hub%2Everify_token (any case), up to the next & or space.
_VERIFY_TOKEN = re.compile(r"(hub(?:\.|_|%2e)verify_token=)[^&\s#]*", re.IGNORECASE)
# code= and state= as whole parameter names only: start of string, or right after ? or &.
_OAUTH_PARAM = re.compile(r"((?:^|(?<=[?&]))(?:code|state)=)[^&\s#]*", re.IGNORECASE)
_NEEDLES = ("verify_token", "code=", "state=")


def mask_query(value):
    """Mask verify-token, code and state values in a query string or request line."""
    if not isinstance(value, str):
        return value
    lowered = value.lower()
    if not any(needle in lowered for needle in _NEEDLES):
        return value
    value = _VERIFY_TOKEN.sub(lambda m: m.group(1) + MASK, value)
    return _OAUTH_PARAM.sub(lambda m: m.group(1) + MASK, value)


# Atoms that carry the raw query string or URI.
_MASKED_ATOMS = ("r", "q", "{raw_uri}e", "{query_string}e", "{request_uri}e")

try:
    from gunicorn.glogging import Logger
except ImportError:  # pragma: no cover - gunicorn is always installed in deployments
    Logger = object


class MaskingLogger(Logger):
    """Gunicorn logger that masks the verify token, code and state in every access-log atom."""

    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key in _MASKED_ATOMS:
            if key in atoms:
                atoms[key] = mask_query(atoms[key])
        return atoms
