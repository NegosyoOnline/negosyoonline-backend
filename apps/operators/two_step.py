"""The second step of admin sign-in: an authenticator code or a recovery code.

hq is the only place that asks for it. The admin lists open from the hq session it makes.
"""
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.notifications.services import send_email
from apps.operators import audit, background, crypto, totp
from apps.operators.models import AdminIdentity, AdminRecoveryCode

logger = logging.getLogger(__name__)

MAX_FAILURES = 5
LOCK_MINUTES = 15
RECOVERY_COUNT = 10
RECOVERY_LENGTH = 10
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I, O, 0 or 1
# "django_admin" is no longer written. Audit rows made before hq became the only door carry it.
SURFACE_HQ, SURFACE_DJANGO_ADMIN = "hq", "django_admin"
STEP_EMAIL, STEP_TOTP, STEP_RECOVERY = "email", "totp", "recovery"

_SIX_DIGITS = re.compile(r"\d{6}")


@dataclass(frozen=True)
class Result:
    ok: bool
    locked: bool = False
    # Read under the same row lock as the check. A session is made from this value only.
    credential_version: int | None = None


def eligible(identity, now):
    """Active, enrolled, not locked, and still linked to an active staff user."""
    return (
        identity.is_active
        and identity.enrolled_at is not None
        and bool(identity.totp_secret_ciphertext)
        and not is_locked(identity, now)
        and identity.user.is_active
        and identity.user.is_staff
    )


def is_locked(identity, now):
    return identity.locked_until is not None and identity.locked_until > now


def new_recovery_codes():
    return [
        "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_LENGTH))
        for _ in range(RECOVERY_COUNT)
    ]


def normalize_recovery(raw):
    return re.sub(r"[\s-]", "", raw or "").upper()


def hash_recovery(code):
    return salted_hmac("apps.operators.recovery", code, algorithm="sha256").hexdigest()


def note_secrets_key_problem():
    """One error line an hour while ADMIN_SECRETS_KEY is empty, invalid or the wrong key."""
    if cache.add("admin-secrets-key-empty", 1, 3600):
        logger.error(
            "admin-secrets-key-empty: ADMIN_SECRETS_KEY is empty, invalid or not the key the "
            "authenticator secrets were stored with. Admin two-step sign-in cannot succeed."
        )


def bump_credentials(identity, now):
    """End every hq session of this identity for good. Does not save."""
    identity.credential_version += 1
    identity.sessions.filter(revoked_at__isnull=True).update(revoked_at=now)


def _audit(identity, action, surface, step):
    audit.record(
        identity.user,
        action,
        target_type=audit.IDENTITY_TARGET,
        target_id=identity.pk,
        detail={"surface": surface, "step": step},
    )


def _use_recovery_code(identity, code, now):
    wanted = hash_recovery(code)
    match = None
    for row in identity.recovery_codes.filter(used_at__isnull=True):
        if hmac.compare_digest(row.code_hash, wanted) and match is None:
            match = row
    if match is None:
        return False
    match.used_at = now
    match.save(update_fields=["used_at"])
    return True


def verify_second_step(identity_id, code, *, surface):
    """Check an authenticator code or a recovery code. Callers never learn why it failed."""
    text = re.sub(r"\s", "", code or "")
    with transaction.atomic():
        identity = (
            AdminIdentity.objects.select_for_update()
            .select_related("user")
            .filter(pk=identity_id)
            .first()
        )
        now = timezone.now()
        if identity is None:
            return Result(ok=False)
        if is_locked(identity, now):
            return Result(ok=False, locked=True)
        if not eligible(identity, now):
            return Result(ok=False)
        try:
            secret = crypto.decrypt(identity.totp_secret_ciphertext)
        except crypto.SecretsKeyError:
            note_secrets_key_problem()
            return Result(ok=False)  # our fault, not a wrong code: nothing is counted

        if _SIX_DIGITS.fullmatch(text):
            step = STEP_TOTP
            matched = totp.matching_step(secret, text, now)
            ok = matched is not None and matched > identity.totp_last_step
            if ok:
                identity.totp_last_step = matched
        else:
            step = STEP_RECOVERY
            recovery = normalize_recovery(text)
            ok = len(recovery) == RECOVERY_LENGTH and _use_recovery_code(identity, recovery, now)

        if ok:
            identity.failed_attempts = 0
            identity.save()
            _audit(identity, "admin_sign_in_ok", surface, step)
            return Result(ok=True, credential_version=identity.credential_version)

        identity.failed_attempts += 1
        _audit(identity, "admin_sign_in_failed", surface, step)
        locked = identity.failed_attempts >= MAX_FAILURES
        if locked:
            identity.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            identity.failed_attempts = 0
            bump_credentials(identity, now)
            _audit(identity, "admin_locked", surface, step)
            transaction.on_commit(
                lambda: background.run_later(send_lockout_notice, identity.pk), robust=True
            )
        identity.save()
        return Result(ok=False, locked=locked)


def send_lockout_notice(identity_id):
    identity = AdminIdentity.objects.filter(pk=identity_id).first()
    if identity is None:
        return
    send_email(
        to=identity.email,
        category="admin_lockout",
        subject="NegosyoOnline admin sign-in paused",
        body=(
            f"Admin sign-in for this address is paused for {LOCK_MINUTES} minutes "
            "after too many wrong codes."
        ),
    )


def store_enrolment(identity, secret, recovery_codes, now):
    """Replace the identity's secret and recovery codes. The caller holds the row lock."""
    identity.totp_secret_ciphertext = crypto.encrypt(secret)
    identity.totp_last_step = 0
    identity.failed_attempts = 0
    identity.locked_until = None
    identity.enrolled_at = now
    bump_credentials(identity, now)
    identity.save()
    identity.recovery_codes.all().delete()
    AdminRecoveryCode.objects.bulk_create(
        AdminRecoveryCode(identity=identity, code_hash=hash_recovery(code))
        for code in recovery_codes
    )


def clear_enrolment(identity, now):
    identity.totp_secret_ciphertext = ""
    identity.totp_last_step = 0
    identity.failed_attempts = 0
    identity.locked_until = None
    identity.enrolled_at = None
    bump_credentials(identity, now)
    identity.save()
    identity.recovery_codes.all().delete()
