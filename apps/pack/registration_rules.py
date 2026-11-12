"""Santé registration details validation and the credential guard (spec 3.4), copied and adapted from
business_sites/services/sante_registration_validation.py (never imported)."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.common.errors import ApiError

MANILA = ZoneInfo("Asia/Manila")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9]+$")
RESERVED_USERNAMES = frozenset(n.lower() for n in (
    "admin", "administrator", "root", "superadmin", "superuser", "owner", "staff", "support", "helpdesk",
    "moderator", "mod", "operator", "manager", "api", "system", "null", "undefined", "true", "false", "test", "demo",
    "guest", "anonymous", "user", "users", "me", "self", "sante", "santeai", "bpract", "sponsor", "upline",
    "downline", "leader"))
DOB_FUTURE = "Date of birth cannot be a future date."
DOB_MINOR = "You must be at least 18 years old to register."
DOB_REQUIRED = "Enter your date of birth."
USERNAME_FORMAT_ERROR = "Use 8–20 letters or numbers only."
USERNAME_RESERVED_ERROR = "This username isn’t allowed on Santé. Choose another."
USERNAME_REQUIRED_ERROR = "Enter your preferred Santé username."
ALTERNATE_REQUIRED_ERROR = "Enter a secondary Santé username, in case your first is already taken."
USERNAME_DUPLICATE_ERROR = "Each username choice must be different."
CREDENTIAL_GUARD_ERROR = "Do not enter passwords or PINs here. Remove the PIN and send again."
_GUARD = (
    re.compile(r"\b(password|passwd|pwd|passcode|pin)\b(?:\s+(?:number|no\.?|code|#))?\s*(?:is|=|:|-)?\s*(?=\S*\d)\S{4,}",
               re.IGNORECASE),
    re.compile(r"\b(password|passwd|pwd|passcode|pin)\b(?:\s+(?:number|no\.?|code|#))?\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(password|passwd|pwd|passcode)\b\s+(?:is|was)\s+\S{4,}", re.IGNORECASE),
)


def manila_today():
    return timezone.now().astimezone(MANILA).date()


def guard(text, field):
    """Never logs, stores or echoes the text (TWC AC-36 rule kept)."""
    if text and any(p.search(text) for p in _GUARD):
        raise ApiError("credential_in_text", CREDENTIAL_GUARD_ERROR, status=422,
                       fields={field: [CREDENTIAL_GUARD_ERROR]})
    return text


def _username_error(value):
    if not (8 <= len(value) <= 20) or not USERNAME_RE.match(value):
        return USERNAME_FORMAT_ERROR
    if value.lower() in RESERVED_USERNAMES:
        return USERNAME_RESERVED_ERROR
    return None


def clean_details(*, date_of_birth, desired_username, alternate_username="", today=None):
    today, errors = today or manila_today(), {}
    dob = None
    try:
        dob = date_of_birth if hasattr(date_of_birth, "year") else datetime.strptime(str(date_of_birth), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        errors["date_of_birth"] = DOB_REQUIRED
    if dob is not None:
        if dob > today:
            errors["date_of_birth"] = DOB_FUTURE
        elif today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)) < 18:
            errors["date_of_birth"] = DOB_MINOR
    desired, alternate = (desired_username or "").strip(), (alternate_username or "").strip()
    if not desired:
        errors["desired_username"] = USERNAME_REQUIRED_ERROR
    elif _username_error(desired):
        errors["desired_username"] = _username_error(desired)
    # Required (Coach Ed 2026-11-01): the first choice is often taken on Santé, so a second one is always asked.
    if not alternate:
        errors["alternate_username"] = ALTERNATE_REQUIRED_ERROR
    elif _username_error(alternate):
        errors["alternate_username"] = _username_error(alternate)
    elif desired and alternate.lower() == desired.lower():
        errors["alternate_username"] = USERNAME_DUPLICATE_ERROR
    if errors:
        raise ApiError("invalid_details", "Check the highlighted fields.", status=422,
                       fields={k: [v] for k, v in errors.items()})
    return {"date_of_birth": dob, "desired_username": desired, "alternate_username": alternate}
