"""Mobile verification: text a code to a number, then confirm it.

The code is never stored. The challenge row keeps a keyed hash of account, number and code;
the text itself waits encrypted in the cache, for at most ten minutes, inside `send_sms`.
While texts are switched off (`sms_on()` is False) nothing here reads or writes the two
mobile tables, because the code is released before those tables are created.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import AccountMobile, MobileOtpChallenge
from apps.accounts.services import record_event
from apps.common import throttle
from apps.common.errors import ApiError
from apps.common.masking import mask_mobile
from apps.common.phones import normalise_ph_mobile
from apps.notifications.email_render import account_mail
from apps.notifications.services import SMS_QUEUED, EmailNotSent, send_email, send_sms, sms_on

CODE_TTL = 600  # seconds
MAX_ATTEMPTS = 5
MINUTES_15, HOUR, DAY = 900, 3600, 86400
PAUSED_REASONS = ("paused", "platform_limit")

NOT_VERIFIED = {"verified": False, "mobile_masked": ""}


def _field_error(code, field, message):
    return ApiError(code, message, fields={field: [message]})


def _unavailable():
    return ApiError(
        "sms_unavailable",
        "Text messages are not available right now. Please try again later.",
        status=503,
    )


def _paused():
    return ApiError("sms_paused", "Please try again shortly.", status=503)


def _expired():
    return _field_error("code_expired", "code", "This code has expired. Request a new one.")


def _number_key(number):
    """A number is never part of a cache key as itself."""
    return hashlib.sha256(f"mobile|{number}".encode()).hexdigest()[:32]


def _code_hash(account, number, code):
    value = f"mobile-otp|{account.pk}|{number}|{code}"
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def mobile_out(account):
    """The `mobile` object of `GET /v1/account/`. Never the full number."""
    if not sms_on():
        return dict(NOT_VERIFIED)
    held = AccountMobile.objects.filter(account=account).first()
    if held is None:
        return dict(NOT_VERIFIED)
    return {"verified": True, "mobile_masked": mask_mobile(held.number)}


def _open_challenges(account):
    return MobileOtpChallenge.objects.filter(
        account=account, used_at__isnull=True, superseded_at__isnull=True
    )


def start(account, raw_mobile, ip):
    """Text a code to the number. Raises ApiError; returns nothing."""
    if not sms_on():
        raise _unavailable()
    try:
        number = normalise_ph_mobile(raw_mobile)
    except ValueError:
        raise _field_error(
            "invalid_mobile", "mobile", "Enter a valid Philippine mobile number."
        ) from None
    if AccountMobile.objects.filter(account=account, number=number).exists():
        raise _field_error("mobile_unchanged", "mobile", "That is already your mobile number.")
    number_key = _number_key(number)
    throttle.hit("mobile-start-account", account.pk, limit=3, window=MINUTES_15)
    throttle.hit("mobile-start-number", number_key, limit=3, window=MINUTES_15)
    throttle.hit("mobile-start-number-day", number_key, limit=10, window=DAY)
    throttle.hit("mobile-start-ip", ip, limit=20, window=HOUR)

    code = f"{secrets.randbelow(10**6):06d}"
    # No email fallback: the step proves the subscriber holds the phone.
    result = send_sms(
        to=number,
        purpose="mobile_verify",
        text=f"Your NegosyoOnline code is {code}. It expires in 10 minutes. Do not share it.",
        ttl=CODE_TTL,
    )
    if result.outcome != SMS_QUEUED:
        raise _paused() if result.reason in PAUSED_REASONS else _unavailable()
    now = timezone.now()
    with transaction.atomic():
        _open_challenges(account).update(superseded_at=now)
        MobileOtpChallenge.objects.create(
            account=account,
            number=number,
            code_hash=_code_hash(account, number, code),
            expires_at=now + timedelta(seconds=CODE_TTL),
            sms_public_id=result.message.public_id,
        )


def verify(account, raw_code):
    """Confirm the code of the account's open challenge. Returns the `mobile` object."""
    if not sms_on():
        raise _unavailable()
    throttle.hit("mobile-verify-account", account.pk, limit=10, window=MINUTES_15)
    challenge = _open_challenges(account).order_by("-pk").first()
    now = timezone.now()
    if challenge is None or challenge.expires_at <= now:
        raise _expired()
    counted = MobileOtpChallenge.objects.filter(
        pk=challenge.pk, attempts__lt=MAX_ATTEMPTS
    ).update(attempts=F("attempts") + 1)
    if not counted:
        raise _field_error("code_burned", "code", "Too many wrong codes. Request a new one.")
    code = "".join(str(raw_code or "").split())
    if not hmac.compare_digest(
        challenge.code_hash, _code_hash(account, challenge.number, code)
    ):
        raise _field_error("wrong_code", "code", "That code did not work. Please try again.")

    with transaction.atomic():
        used = MobileOtpChallenge.objects.filter(
            pk=challenge.pk, used_at__isnull=True, superseded_at__isnull=True
        ).update(used_at=now)
        if not used:  # another request used or replaced it a moment ago
            raise _expired()
        held = AccountMobile.objects.select_for_update().filter(account=account).first()
        changed = held is not None and held.number != challenge.number
        if held is None:
            AccountMobile.objects.create(
                account=account, number=challenge.number, verified_at=now
            )
        else:
            held.number = challenge.number
            held.verified_at = now
            held.save(update_fields=["number", "verified_at", "updated_at"])
        # The audit event never holds a number.
        record_event(account, "mobile_changed" if changed else "mobile_verified")
    if changed:
        _notify_change(account, challenge.number)
    return {"verified": True, "mobile_masked": mask_mobile(challenge.number)}


def _notify_change(account, number):
    """The notice for a changed number. By email only: no text goes to the old number."""
    try:
        send_email(
            to=account.email,
            category="mobile_changed_notice",
            subject="Your NegosyoOnline mobile number changed",
            **account_mail(
                "mobile_notice",
                "The mobile number on your NegosyoOnline account was changed to the number "
                f"ending in {number[-4:]}. If you did not make this change, sign in and "
                "update it.",
                last4=number[-4:],
            ),
        )
    except EmailNotSent:
        pass  # already logged and recorded as failed; the change itself stands
