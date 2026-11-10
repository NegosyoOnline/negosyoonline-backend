"""Google sign-in, server-side redirect flow. Google's tokens are used once and never stored."""
import base64
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
TIMEOUT = 10  # seconds


@dataclass(frozen=True)
class GoogleProfile:
    user_id: str
    email: str
    email_verified: bool
    name: str


class GoogleSignInFailed(Exception):
    """Google refused the code, or answered with something we cannot trust."""


def is_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def authorization_url(redirect_uri, state):
    query = urllib.parse.urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return f"{AUTH_URL}?{query}"


def _token_response(code, redirect_uri):
    """Swap the one-time code for Google's answer. The only network call in this module."""
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def _claims(id_token):
    # The signature is not checked: the token came straight from Google's token endpoint over
    # TLS, authenticated with our client secret. Issuer, audience and expiry are checked below.
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def fetch_profile(code, redirect_uri):
    """The Google user behind this one-time code. Raises GoogleSignInFailed."""
    try:
        claims = _claims(_token_response(code, redirect_uri)["id_token"])
        if claims["iss"] not in ISSUERS or claims["aud"] != settings.GOOGLE_CLIENT_ID:
            raise ValueError("not issued by Google for this client")
        if float(claims["exp"]) <= time.time():
            raise ValueError("expired")
        if not claims["sub"]:
            raise ValueError("no subject")
        return GoogleProfile(
            user_id=str(claims["sub"]),
            email=str(claims.get("email", "")),
            email_verified=claims.get("email_verified") is True,
            name=str(claims.get("name", ""))[:120],
        )
    except (OSError, KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        # Only the kind of failure is logged: never the code, a token or Google's answer.
        logger.warning("Google sign-in refused (%s)", type(error).__name__)
        raise GoogleSignInFailed() from None
