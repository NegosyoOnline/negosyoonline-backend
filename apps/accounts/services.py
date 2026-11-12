"""Account sign-in and settings. Other apps call these functions, never the tables."""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import codes
from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity, PolicyAcceptance
from apps.accounts.sessions import client_ip, revoke_sessions, start_session
from apps.common.errors import ApiError
from apps.notifications.services import EmailNotSent, send_email

SIGNIN = "signin"
EMAIL_CHANGE_PAUSE = timedelta(hours=24)
SUPPORT_EMAIL = "support@negosyoonline.com"  # the address Account Settings shows
GOOGLE = ExternalIdentity.Provider.GOOGLE
FACEBOOK = ExternalIdentity.Provider.FACEBOOK


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


def _invalid_code(state="refused"):
    return ApiError(
        "invalid_code",
        "That code is wrong or has expired.",
        event="code_locked" if state == "locked" else "code_refused",
        reason="missing",
    )


def _require_code(email, code, purpose):
    state = codes.verify_code(email, code, purpose)
    if state != "ok":
        raise _invalid_code(state)


def _email_taken():
    return ApiError("email_taken", "That email is already used by another account.", status=409)


def _change_purpose(account):
    return f"change:{account.pk}"


def _refuse_closed(account):
    if account.closed_at is not None:
        raise ApiError("account_closed", "This account is closed.", status=403)


def _policies_required():
    return ApiError(
        "policies_required", "Accept the terms and privacy policy to create an account."
    )


def _create_account(email, request, *, method, name=""):
    """A new account whose email is already proven, with its policy acceptance."""
    account = Account.objects.create(email=email, name=name, email_verified_at=timezone.now())
    PolicyAcceptance.objects.create(
        account=account,
        policy_version=settings.POLICY_VERSION,
        policies=list(settings.POLICIES),
        ip=client_ip(request) or None,
    )
    record_event(account, "account_created", method=method)
    return account


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
    _require_code(email, code, SIGNIN)
    account = Account.objects.filter(email=email).first()
    created = account is None
    if created:
        if not accept_policies:
            raise _policies_required()
    else:
        _refuse_closed(account)
    codes.clear_code(email, SIGNIN)
    if created:
        account = _create_account(email, request, method="email_code")
    else:
        if account.email_verified_at is None:
            account.email_verified_at = timezone.now()
            account.save(update_fields=["email_verified_at"])
        revoke_sessions(account)
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="email_code", session=session.public_id)
    return account, session, token, created


@transaction.atomic
def sign_in_with_google(profile, accept_policies, request):
    """Sign in with a profile from apps.accounts.google. Returns (account, session, token, created).

    The Google user id is matched first, then the email. Other sessions are left signed in.
    """
    created = False
    identity = (
        ExternalIdentity.objects.select_related("account")
        .filter(provider=GOOGLE, provider_user_id=profile.user_id)
        .first()
    )
    if identity is not None:
        account = identity.account
        _refuse_closed(account)
    else:
        if not profile.email_verified:
            raise ApiError("google_email_unverified", "Google has not verified that email address.")
        email = normalise_email(profile.email)
        account = Account.objects.filter(email=email).first()
        if account is None:
            if not accept_policies:
                raise _policies_required()
            account = _create_account(email, request, method="google", name=profile.name)
            created = True
        else:
            _refuse_closed(account)
            if account.identities.filter(provider=GOOGLE).exists():
                raise ApiError(
                    "google_account_mismatch",
                    "This email is already linked to a different Google account.",
                    status=409,
                )
            if account.email_verified_at is None:
                account.email_verified_at = timezone.now()
                account.save(update_fields=["email_verified_at"])
        ExternalIdentity.objects.create(
            account=account, provider=GOOGLE, provider_user_id=profile.user_id
        )
        record_event(account, "identity_linked", provider="google")
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="google", session=session.public_id)
    return account, session, token, created


def _facebook_purpose(user_id):
    # A code sent for one Facebook user confirms an email for that user only.
    return f"facebook:{user_id}"


@transaction.atomic
def sign_in_with_facebook(profile, request):
    """Sign in a Facebook user we have linked before. Returns (account, session, token).

    Returns None when this Facebook user has never been linked: the caller must then have
    the person confirm an email with confirm_facebook_email().
    """
    identity = (
        ExternalIdentity.objects.select_related("account")
        .filter(provider=FACEBOOK, provider_user_id=profile.user_id)
        .first()
    )
    if identity is None:
        return None
    account = identity.account
    _refuse_closed(account)
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="facebook", session=session.public_id)
    return account, session, token


def send_facebook_email_code(user_id, email, ip):
    """Email the code that confirms `email` for this Facebook user. Returns the address used."""
    email = normalise_email(email)
    code = codes.issue_code(email, ip, _facebook_purpose(user_id))
    send_email(
        to=email,
        category="facebook_email_code",
        subject="Confirm your email for NegosyoOnline",
        body=(
            f"Your confirmation code is {code}. It expires in 10 minutes.\n\n"
            "You are seeing this because someone is signing in to NegosyoOnline with Facebook "
            "using this email. If that was not you, you can ignore this email."
        ),
    )
    return email


@transaction.atomic
def confirm_facebook_email(user_id, name, email, code, accept_policies, request):
    """First Facebook sign-in: check the emailed code, then link Facebook to the account with
    that email, creating the account if there is none.

    Returns (account, session, token, created).
    """
    email = normalise_email(email)
    purpose = _facebook_purpose(user_id)
    _require_code(email, code, purpose)
    account = Account.objects.filter(email=email).first()
    created = account is None
    if created:
        if not accept_policies:
            raise _policies_required()
    else:
        _refuse_closed(account)
        if account.identities.filter(provider=FACEBOOK).exists():
            raise ApiError(
                "facebook_account_mismatch",
                "This email is already linked to a different Facebook account.",
                status=409,
            )
    if ExternalIdentity.objects.filter(provider=FACEBOOK, provider_user_id=user_id).exists():
        raise ApiError(
            "facebook_already_linked", "This Facebook account is already linked.", status=409
        )
    codes.clear_code(email, purpose)
    if created:
        account = _create_account(email, request, method="facebook", name=name[:120])
    else:
        if account.email_verified_at is None:
            account.email_verified_at = timezone.now()
            account.save(update_fields=["email_verified_at"])
        revoke_sessions(account)  # an email was just verified by code
    ExternalIdentity.objects.create(account=account, provider=FACEBOOK, provider_user_id=user_id)
    record_event(account, "identity_linked", provider="facebook")
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="facebook", session=session.public_id)
    return account, session, token, created


@transaction.atomic
def remove_facebook_identity(user_id):
    """Meta's data deletion request: unlink this Facebook user. True when there was a link."""
    identity = (
        ExternalIdentity.objects.select_related("account")
        .filter(provider=FACEBOOK, provider_user_id=user_id)
        .first()
    )
    if identity is None:
        return False
    account = identity.account
    identity.delete()
    record_event(account, "identity_removed", provider="facebook", reason="data_deletion_request")
    return True


def update_account(account, *, name):
    account.name = name.strip()
    account.save(update_fields=["name"])
    record_event(account, "profile_updated")
    return account


def email_change_pause(account):
    """(changed_at, available_at) while the pause after an email change runs, else (None, None)."""
    changed_at = (
        account.audit_events.filter(event="email_changed")
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if changed_at is None or changed_at + EMAIL_CHANGE_PAUSE <= timezone.now():
        return None, None
    return changed_at, changed_at + EMAIL_CHANGE_PAUSE


def _refuse_paused(account):
    if email_change_pause(account)[1] is not None:
        raise ApiError(
            "email_change_cooldown",
            "Your email was changed recently. You can change it again 24 hours after that change.",
            status=409,
        )


def start_email_change(account, new_email, ip):
    """Email a code to the new address. The email changes only when that code is confirmed."""
    _refuse_paused(account)
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


def confirm_email_change(account, new_email, code, current_session):
    """Change the email, sign out the other devices, start the pause and tell the old address."""
    old_email = _apply_email_change(account, new_email, code, current_session)
    try:
        # The new address is left out: this email goes to whoever holds the old mailbox.
        send_email(
            to=old_email,
            category="email_changed_notice",
            subject="Your NegosyoOnline email was changed",
            body=(
                "The email address of your NegosyoOnline account was just changed, so this "
                "address can no longer be used to sign in. Every other device was signed out.\n\n"
                f"If you did not make this change, contact {SUPPORT_EMAIL} right away."
            ),
        )
    except EmailNotSent:
        pass  # already logged and recorded as failed; the change itself stands
    return account


@transaction.atomic
def _apply_email_change(account, new_email, code, current_session):
    _refuse_paused(account)
    new_email = normalise_email(new_email)
    purpose = _change_purpose(account)
    _require_code(new_email, code, purpose)
    if Account.objects.filter(email=new_email).exclude(pk=account.pk).exists():
        raise _email_taken()
    codes.clear_code(new_email, purpose)
    old_email = account.email
    account.email = new_email
    account.email_verified_at = timezone.now()
    account.save(update_fields=["email", "email_verified_at"])
    revoke_sessions(account, keep=current_session)
    record_event(account, "email_changed", old=old_email, new=new_email)
    return old_email


def connect_identity(account, provider, provider_user_id):
    """Link a provider user to this account from Account Settings. Returns the result slug.

    Nothing is matched by email and nobody is signed in: the caller has already checked that
    `account` is the one signed in on this browser.
    """
    linked = ExternalIdentity.objects.filter(
        provider=provider, provider_user_id=provider_user_id
    ).first()
    if linked is not None:
        return "already_connected" if linked.account_id == account.pk else "in_use"
    if account.identities.filter(provider=provider).exists():
        return "already_connected"
    try:
        with transaction.atomic():
            ExternalIdentity.objects.create(
                account=account, provider=provider, provider_user_id=provider_user_id
            )
            record_event(account, "identity_linked", provider=str(provider), via="settings")
    except IntegrityError:  # another request linked it between the checks and the insert
        return "failed"
    _notify_connection_change(account, provider, connected=True)
    return "connected"


def disconnect_identity(account, provider):
    """Remove this account's link to a provider. True when there was one."""
    with transaction.atomic():
        removed, _ = account.identities.filter(provider=provider).delete()
        if removed:
            record_event(account, "identity_removed", provider=str(provider), reason="owner")
    if removed:
        _notify_connection_change(account, provider, connected=False)
    return bool(removed)


def _notify_connection_change(account, provider, *, connected):
    """The security alert for a connect or a disconnect made in Account Settings."""
    label = ExternalIdentity.Provider(provider).label
    when = timezone.localtime(timezone.now()).strftime("%B %d, %Y at %I:%M %p %Z")
    if connected:
        subject = f"{label} was connected to your NegosyoOnline account"
        change = f"A {label} account was connected to your NegosyoOnline account on {when}. It can now be used to sign in."
    else:
        subject = f"{label} was disconnected from your NegosyoOnline account"
        change = f"{label} sign-in was removed from your NegosyoOnline account on {when}. You can still sign in with a code sent to this email."
    try:
        send_email(
            to=account.email,
            category="connection_changed_notice",
            subject=subject,
            body=f"{change}\n\nIf you did not make this change, contact {SUPPORT_EMAIL} right away.",
        )
    except EmailNotSent:
        pass  # already logged and recorded as failed; the change itself stands
