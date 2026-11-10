"""Subscriber sessions: an opaque cookie token whose SHA-256 is the only thing stored."""
import hashlib
import ipaddress
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Session

SESSION_LIFETIME = timedelta(days=30)
TOUCH_INTERVAL = timedelta(minutes=10)  # how stale last_used_at may get before it is rewritten
COOKIE_PATH = "/v1/"

# First match wins, so the more specific tokens come first.
BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("CriOS/", "Chrome"),
    ("FxiOS/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Firefox/", "Firefox"),
    ("Safari/", "Safari"),
)
SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Mac OS X", "Mac"),
    ("Linux", "Linux"),
)


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request):
    """The caller's IP as Railway's proxy reports it, or "" when it is not a valid address."""
    value = (request.headers.get("X-Real-IP") or request.META.get("REMOTE_ADDR") or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def mask_ip(ip):
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if parsed.version == 4:
        return ".".join(str(parsed).split(".")[:3]) + ".x"
    return ":".join(parsed.exploded.split(":")[:3]) + ":x"


def device_label(user_agent):
    user_agent = user_agent or ""
    browser = next((name for token, name in BROWSERS if token in user_agent), "Browser")
    system = next((name for token, name in SYSTEMS if token in user_agent), "unknown device")
    return f"{browser} on {system}"


def start_session(account, request):
    """Create a session and return (session, token). The token is shown once and never stored."""
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    session = Session.objects.create(
        account=account,
        token_hash=_token_hash(token),
        expires_at=now + SESSION_LIFETIME,
        last_used_at=now,
        device_label=device_label(request.headers.get("User-Agent")),
        masked_ip=mask_ip(client_ip(request)),
    )
    return session, token


def find_session(token):
    """The live session for this token, or None. Refreshes last_used_at when it is stale."""
    if not token:
        return None
    now = timezone.now()
    session = (
        Session.objects.select_related("account")
        .filter(
            token_hash=_token_hash(token),
            revoked_at__isnull=True,
            expires_at__gt=now,
            account__closed_at__isnull=True,
        )
        .first()
    )
    if session is not None and now - session.last_used_at > TOUCH_INTERVAL:
        session.last_used_at = now
        session.save(update_fields=["last_used_at"])
    return session


def revoke_session(session):
    Session.objects.filter(pk=session.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())


def revoke_sessions(account, keep=None):
    """Revoke the account's live sessions, except `keep`. Returns how many were revoked."""
    live = Session.objects.filter(account=account, revoked_at__isnull=True)
    if keep is not None:
        live = live.exclude(pk=keep.pk)
    return live.update(revoked_at=timezone.now())


def set_session_cookie(response, token):
    # No domain: the cookie is host-only, so it is never sent to storefront hosts.
    response.set_cookie(
        settings.ACCOUNT_SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path=COOKIE_PATH,
    )


def clear_session_cookie(response):
    response.delete_cookie(settings.ACCOUNT_SESSION_COOKIE, path=COOKIE_PATH, samesite="Lax")
