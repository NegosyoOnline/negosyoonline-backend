"""Facebook sign-in (server-side redirect flow) and Meta's signed requests.

Facebook's tokens are used once and never stored.
"""
import base64
import hashlib
import hmac
import json
import logging
import urllib.parse
import time
import urllib.request
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

# Unversioned on purpose: Meta serves these with the app's default Graph API version.
DIALOG_URL = "https://www.facebook.com/dialog/oauth"
TOKEN_URL = "https://graph.facebook.com/oauth/access_token"
ME_URL = "https://graph.facebook.com/me"
TIMEOUT = 10  # seconds


@dataclass(frozen=True)
class FacebookProfile:
    user_id: str
    name: str
    email: str  # "" when the person shared none; never trusted until confirmed by code


class FacebookSignInFailed(Exception):
    """Facebook refused the code, or answered with something we cannot use."""


class BadSignedRequest(Exception):
    """A request that claims to come from Meta but is not signed with our app secret."""


class StaleSignedRequest(BadSignedRequest):
    """A correctly signed request whose `issued_at` is too old or too far ahead."""


def is_configured():
    return bool(settings.FACEBOOK_APP_ID and settings.FACEBOOK_APP_SECRET)


def authorization_url(redirect_uri, state):
    params = {
        "client_id": settings.FACEBOOK_APP_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
    }
    if settings.FACEBOOK_LOGIN_CONFIG_ID:  # Login for Business: the configuration carries the permissions
        params["config_id"] = settings.FACEBOOK_LOGIN_CONFIG_ID
    else:
        params["scope"] = "public_profile,email"
    query = urllib.parse.urlencode(params)
    return f"{DIALOG_URL}?{query}"


def _get_json(url, params):
    """The only network call in this module."""
    with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=TIMEOUT) as response:
        return json.load(response)


def _sign(message, secret=None):
    return hmac.new((secret or settings.FACEBOOK_APP_SECRET).encode(), message.encode(), hashlib.sha256)


def fetch_profile(code, redirect_uri):
    """The Facebook user behind this one-time code. Raises FacebookSignInFailed."""
    try:
        token = _get_json(
            TOKEN_URL,
            {
                "client_id": settings.FACEBOOK_APP_ID,
                "client_secret": settings.FACEBOOK_APP_SECRET,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )["access_token"]
        person = _get_json(
            ME_URL,
            {
                "fields": "id,name,email",
                "access_token": token,
                "appsecret_proof": _sign(token).hexdigest(),
            },
        )
        if not person["id"]:
            raise ValueError("no user id")
        return FacebookProfile(
            user_id=str(person["id"]),
            name=str(person.get("name", ""))[:120],
            email=str(person.get("email", "")),
        )
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        # Only the kind of failure is logged: the request URLs carry the secret and the token.
        logger.warning("Facebook sign-in refused (%s)", type(error).__name__)
        raise FacebookSignInFailed() from None


def _b64decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# A signed request is accepted for a day after Meta issued it, and not from the future (clock skew).
MAX_AGE_SECONDS = 24 * 3600
MAX_FUTURE_SECONDS = 5 * 60
# The fixed words a refusal is labelled with in the log, so one failed callback shows which check refused it.
_REFUSAL_REASONS = frozenset({"malformed", "signature", "algorithm", "user_id", "issued_at"})


def parse_signed_request(signed_request, secret=None):
    """The Facebook user id in a request Meta signed with our app secret.

    `secret` is the app secret to check against (default: the sign-in app's; the Messenger app
    passes its own). Raises BadSignedRequest, or StaleSignedRequest when `issued_at` is over 24 hours old or more
    than 5 minutes ahead. A request without `issued_at` is refused as BadSignedRequest.
    """
    try:
        try:
            signature, payload = signed_request.split(".", 1)
        except ValueError:
            raise ValueError("malformed") from None
        if not hmac.compare_digest(_b64decode(signature), _sign(payload, secret).digest()):
            raise ValueError("signature")
        data = json.loads(_b64decode(payload))
        if str(data["algorithm"]).upper() != "HMAC-SHA256":
            raise ValueError("algorithm")
        if not data["user_id"]:
            raise ValueError("user_id")
        issued_at = data["issued_at"]
        if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)):
            raise ValueError("issued_at")
        age = time.time() - issued_at
        if age > MAX_AGE_SECONDS or age < -MAX_FUTURE_SECONDS:
            raise StaleSignedRequest()
        return str(data["user_id"])
    except StaleSignedRequest:
        logger.warning("Facebook signed request refused (stale)")
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        # Only the kind of failure is logged, plus which of our own fixed checks refused it (never
        # the request, the signature or the secret). A missing field logs the KeyError class.
        reason = str(error) if isinstance(error, ValueError) and str(error) in _REFUSAL_REASONS else ""
        logger.warning("Facebook signed request refused (%s%s)", type(error).__name__, f": {reason}" if reason else "")
        raise BadSignedRequest() from None
