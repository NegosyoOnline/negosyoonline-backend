"""hq sign-in before the second step: the emailed code, its limits, and the hq session.

hq uses no Django auth session. Its cookie holds a random token; the row holds the SHA-256.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.notifications.services import send_email
from apps.operators import audit, crypto, two_step
from apps.operators.models import AdminIdentity, AdminSession, AdminSignInChallenge

EMAIL_MAX_LENGTH = 254
SUBMITS_PER_HOUR = 5
CODE_MINUTES = 10
CODE_TRIES = 5
SESSION_HOURS = 12
CHALLENGE_KEPT = timedelta(hours=24)
SESSION_KEPT = timedelta(days=30)


def normalize_email(raw):
    """The trimmed, lower-cased address, or None when it is not one."""
    email = (raw or "").strip().lower()
    if not email or len(email) > EMAIL_MAX_LENGTH:
        return None
    try:
        validate_email(email)
    except ValidationError:
        return None
    return email


def _count(scope, value):
    key = f"hq-sign-in:{scope}:{hashlib.sha256(value.encode()).hexdigest()}"
    cache.add(key, 0, 3600)
    try:
        return cache.incr(key)
    except ValueError:  # the key expired between add and incr
        cache.set(key, 1, 3600)
        return 1


def within_limits(email, ip):
    """Count this submit for the address and for the IP. False once either is over the limit.

    Never raises: an over-limit submit gets the same answer as any other, with no code sent.
    """
    by_address = _count("address", email)
    by_ip = _count("ip", ip)
    return by_address <= SUBMITS_PER_HOUR and by_ip <= SUBMITS_PER_HOUR


def _code_hmac(nonce, code):
    return salted_hmac("apps.operators.email-code", f"{nonce}:{code}", algorithm="sha256").hexdigest()


def _purge(now):
    AdminSignInChallenge.objects.filter(expires_at__lt=now - CHALLENGE_KEPT).delete()
    AdminSession.objects.filter(
        Q(expires_at__lt=now - SESSION_KEPT) | Q(revoked_at__lt=now - SESSION_KEPT)
    ).delete()


def issue_email_code(email, nonce):
    """Runs after the response, off the request, so the answer never depends on the address."""
    if not crypto.key_ready():
        two_step.note_secrets_key_problem()
        return
    now = timezone.now()
    _purge(now)
    identity = AdminIdentity.objects.select_related("user").filter(email=email).first()
    if identity is None or not two_step.eligible(identity, now):
        return
    code = f"{secrets.randbelow(10**6):06d}"
    with transaction.atomic():
        # A new code replaces the live one.
        identity.challenges.filter(consumed_at__isnull=True).update(consumed_at=now)
        AdminSignInChallenge.objects.create(
            identity=identity,
            code_hmac=_code_hmac(nonce, code),
            nonce=nonce,
            expires_at=now + timedelta(minutes=CODE_MINUTES),
        )
    send_email(
        to=identity.email,
        category="admin_signin_code",
        subject="Your NegosyoOnline admin sign-in code",
        body=(
            f"Your NegosyoOnline admin sign-in code is {code}. It expires in {CODE_MINUTES} "
            "minutes. If you did not ask for it, ignore this email."
        ),
    )


def _audit(identity, action, step):
    audit.record(
        identity.user,
        action,
        target_type=audit.IDENTITY_TARGET,
        target_id=identity.pk,
        detail={"surface": two_step.SURFACE_HQ, "step": step},
    )


def check_email_code(email, nonce, code):
    """The token of a new hq session at stage email_ok, or None. Callers never learn why."""
    with transaction.atomic():
        challenge = (
            AdminSignInChallenge.objects.select_for_update()
            .filter(nonce=nonce, identity__email=email)
            .order_by("-pk")
            .first()
        )
        if challenge is None:
            return None
        identity = AdminIdentity.objects.select_related("user").get(pk=challenge.identity_id)
        now = timezone.now()
        usable = (
            challenge.consumed_at is None
            and challenge.expires_at > now
            and challenge.attempts < CODE_TRIES
            and two_step.eligible(identity, now)
        )
        if usable:
            # The try is counted before the code is compared.
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
        if not usable or not hmac.compare_digest(challenge.code_hmac, _code_hmac(nonce, code)):
            _audit(identity, "admin_sign_in_failed", two_step.STEP_EMAIL)
            return None
        challenge.consumed_at = now
        challenge.save(update_fields=["consumed_at"])
        token = secrets.token_urlsafe(32)
        AdminSession.objects.create(
            identity=identity,
            token_hash=_token_hash(token),
            credential_version=identity.credential_version,
            stage=AdminSession.Stage.EMAIL_OK,
            expires_at=now + timedelta(hours=SESSION_HOURS),
        )
        return token


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def load_session(token):
    """The live session for this cookie value, or None. The identity is read again every time."""
    if not token:
        return None
    session = (
        AdminSession.objects.select_related("identity__user")
        .filter(token_hash=_token_hash(token), revoked_at__isnull=True)
        .first()
    )
    now = timezone.now()
    if (
        session is None
        or session.expires_at <= now
        or session.credential_version != session.identity.credential_version
        or not two_step.eligible(session.identity, now)
    ):
        return None
    return session


def mark_verified(session, credential_version):
    """After the second step: a new token, and the version the check read under its lock."""
    token = secrets.token_urlsafe(32)
    session.token_hash = _token_hash(token)
    session.stage = AdminSession.Stage.VERIFIED
    session.verified_at = timezone.now()
    session.credential_version = credential_version
    session.save(update_fields=["token_hash", "stage", "verified_at", "credential_version"])
    return token


def sign_out(session):
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at"])
    audit.record(
        session.identity.user,
        "admin_sign_out",
        target_type=audit.IDENTITY_TARGET,
        target_id=session.identity_id,
        detail={"surface": two_step.SURFACE_HQ},
    )
