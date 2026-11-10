"""Account sign-in and settings. Other apps call these functions, never the tables."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.accounts import codes
from apps.accounts.models import Account, AccountAuditEvent, PolicyAcceptance
from apps.accounts.sessions import client_ip, revoke_sessions, start_session
from apps.common.errors import ApiError
from apps.notifications.services import send_email

SIGNIN = "signin"


def normalise_email(email):
    value = (email or "").strip().lower()
    try:
        validate_email(value)
    except ValidationError:
        raise ApiError(
            "invalid_request",
            "Some values are missing or invalid.",
            fields={"email": ["Enter a valid email address."]},
        ) from None
    return value


def record_event(account, event, **detail):
    return AccountAuditEvent.objects.create(account=account, event=event, detail=detail)


def _invalid_code():
    return ApiError("invalid_code", "That code is wrong or has expired.")


def _email_taken():
    return ApiError("email_taken", "That email is already used by another account.", status=409)


def _change_purpose(account):
    return f"change:{account.pk}"


def request_signin_code(email, ip):
    """Email a sign-in code. Behaves the same whether or not the email has an account."""
    email = normalise_email(email)
    code = codes.issue_code(email, ip, SIGNIN)
    send_email(
        to=email,
        category="signin_code",
        subject="Your NegosyoOnline sign-in code",
        body=(
            f"Your sign-in code is {code}. It expires in 10 minutes.\n\n"
            "If you did not ask for this code, you can ignore this email."
        ),
    )


@transaction.atomic
def verify_signin_code(email, code, accept_policies, request):
    """Sign in, creating the account on first use. Returns (account, session, token, created)."""
    email = normalise_email(email)
    if not codes.check_code(email, code, SIGNIN):
        raise _invalid_code()
    account = Account.objects.filter(email=email).first()
    if account is not None and account.closed_at is not None:
        raise ApiError("account_closed", "This account is closed.", status=403)
    created = account is None
    if created and not accept_policies:
        raise ApiError(
            "policies_required", "Accept the terms and privacy policy to create an account."
        )
    codes.clear_code(email, SIGNIN)
    now = timezone.now()
    if created:
        account = Account.objects.create(email=email, email_verified_at=now)
        PolicyAcceptance.objects.create(
            account=account,
            policy_version=settings.POLICY_VERSION,
            policies=list(settings.POLICIES),
            ip=client_ip(request) or None,
        )
        record_event(account, "account_created", method="email_code")
    else:
        if account.email_verified_at is None:
            account.email_verified_at = now
            account.save(update_fields=["email_verified_at"])
        revoke_sessions(account)
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="email_code", session=session.public_id)
    return account, session, token, created


def update_account(account, *, name):
    account.name = name.strip()
    account.save(update_fields=["name"])
    record_event(account, "profile_updated")
    return account


def start_email_change(account, new_email, ip):
    """Email a code to the new address. The email changes only when that code is confirmed."""
    new_email = normalise_email(new_email)
    if new_email == account.email:
        raise ApiError("email_unchanged", "That is already your email.")
    if Account.objects.filter(email=new_email).exists():
        raise _email_taken()
    code = codes.issue_code(new_email, ip, _change_purpose(account))
    send_email(
        to=new_email,
        category="email_change_code",
        subject="Confirm your new NegosyoOnline email",
        body=(
            f"Your confirmation code is {code}. It expires in 10 minutes.\n\n"
            "If you did not ask to use this email for NegosyoOnline, you can ignore this email."
        ),
    )


@transaction.atomic
def confirm_email_change(account, new_email, code, current_session):
    new_email = normalise_email(new_email)
    purpose = _change_purpose(account)
    if not codes.check_code(new_email, code, purpose):
        raise _invalid_code()
    if Account.objects.filter(email=new_email).exclude(pk=account.pk).exists():
        raise _email_taken()
    codes.clear_code(new_email, purpose)
    old_email = account.email
    account.email = new_email
    account.email_verified_at = timezone.now()
    account.save(update_fields=["email", "email_verified_at"])
    revoke_sessions(account, keep=current_session)
    record_event(account, "email_changed", old=old_email, new=new_email)
    return account
