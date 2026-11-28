"""Account sign-in and settings. Other apps call these functions, never the tables."""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import codes
from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    AccountMobile,
    ExternalIdentity,
    PolicyAcceptance,
)
from apps.accounts.sessions import client_ip, revoke_sessions, start_session
from apps.common import throttle
from apps.common.errors import ApiError
from apps.notifications.email_render import account_mail
from apps.notifications.services import SMS_QUEUED, EmailNotSent, send_email, send_sms, sms_on

SIGNIN = "signin"
EMAIL_CHANGE_PAUSE = timedelta(hours=24)
EMAIL_CHANGE_STARTS_PER_HOUR = 8  # step-up and start mails together, per account
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


REFERRAL_CODE_MAX = 12  # Account.referral_code max_length


def find_referrer(code):
    """The open account that owns a referral code, or None. Never raises, whatever the input.

    Off (BILLING_REFERRALS_ENABLED) it finds nobody. Every miss costs the same one query.
    """
    if not settings.BILLING_REFERRALS_ENABLED:
        return None
    code = str(code or "").strip()[:REFERRAL_CODE_MAX].lower()
    return Account.objects.filter(referral_code=code, closed_at__isnull=True).first()


def _create_account(email, request, *, method, name="", referral_code=""):
    """A new account whose email is already proven, with its policy acceptance.

    `referral_code` records who referred it, once, here only. A code that is unknown, closed or
    the account's own is ignored: signup never fails because of it.
    """
    referrer = find_referrer(referral_code) if referral_code else None
    if referrer is not None and referrer.email == email:
        referrer = None  # defence in depth: email is unique, so this cannot happen today
    account = Account.objects.create(
        email=email, name=name, email_verified_at=timezone.now(), referred_by=referrer
    )
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
    try:
        send_email(
            to=email,
            category="signin_code",
            subject="Your NegosyoOnline sign-in code",
            **account_mail(
                "signin",
                f"Your sign-in code is {code}. It expires in 10 minutes.\n\n"
                "If you did not ask for this code, you can ignore this email.",
                code=code,
            ),
        )
    except EmailNotSent:
        codes.release_cooldown(email, ip, SIGNIN)  # the person can ask again at once
        raise


@transaction.atomic
def verify_signin_code(email, code, accept_policies, request, referral_code=""):
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
        account = _create_account(email, request, method="email_code", referral_code=referral_code)
    else:
        if account.email_verified_at is None:
            account.email_verified_at = timezone.now()
            account.save(update_fields=["email_verified_at"])
        revoke_sessions(account)
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="email_code", session=session.public_id)
    return account, session, token, created


@transaction.atomic
def sign_in_with_google(profile, accept_policies, request, referral_code=""):
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
            account = _create_account(
                email, request, method="google", name=profile.name, referral_code=referral_code
            )
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
    purpose = _facebook_purpose(user_id)
    code = codes.issue_code(email, ip, purpose)
    try:
        send_email(
            to=email,
            category="facebook_email_code",
            subject="Confirm your email for NegosyoOnline",
            **account_mail(
                "facebook_confirm",
                f"Your confirmation code is {code}. It expires in 10 minutes.\n\n"
                "You are seeing this because someone is signing in to NegosyoOnline with Facebook "
                "using this email. If that was not you, you can ignore this email.",
                code=code,
            ),
        )
    except EmailNotSent:
        codes.release_cooldown(email, ip, purpose)
        raise
    return email


@transaction.atomic
def confirm_facebook_email(user_id, name, email, code, accept_policies, request, referral_code=""):
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
        account = _create_account(
            email, request, method="facebook", name=name[:120], referral_code=referral_code
        )
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


def _throttle_email_change(account):
    """At most 5 email-change mails an hour per account, so a session cannot be used to mail
    codes to arbitrary addresses."""
    throttle.hit("email-change", account.pk, limit=EMAIL_CHANGE_STARTS_PER_HOUR, window=codes.HOUR)


STEP_UP_PURPOSES = ("email_change", "mobile_change", "sms_line_rotate")
STEP_UP_CHANNELS = ("email", "mobile")
STEP_UP_SMS = "Your NegosyoOnline code is {code}. It expires in 10 minutes. Do not share it."


def step_up_channels(account):
    """Where a step-up code can go now: the email always, the verified mobile only while texts
    are on and the account holds one. Empty while ACCOUNT_EMAIL_CHANGE_HARDENED is off."""
    if not settings.ACCOUNT_EMAIL_CHANGE_HARDENED:
        return []
    channels = ["email"]
    if sms_on() and AccountMobile.objects.filter(account=account).exists():
        channels.append("mobile")
    return channels


def _step_code_scope(account, purpose, channel):
    """A code is bound to its account, purpose and channel: the cache key holds all three."""
    return str(account.pk), f"step-up:{purpose}:{channel}"


def require_step_up(account, step_up_code, purpose):
    """Accept a code from either channel for this purpose, or raise step_up_required."""
    if step_up_code:
        for channel in STEP_UP_CHANNELS:
            value, scope = _step_code_scope(account, purpose, channel)
            if codes.check_code(value, step_up_code, scope):
                return
    raise ApiError("step_up_required", "Enter the code we sent you first.")


def verify_step_up(account, purpose, code):
    """Check a step-up code without using it up, so the dashboard can refuse a wrong one before
    it opens the new address or number step. The start step still checks it again. Each check
    counts toward the code's attempt limit. Only while ACCOUNT_EMAIL_CHANGE_HARDENED is on."""
    if not settings.ACCOUNT_EMAIL_CHANGE_HARDENED:
        raise ApiError("not_found", "Not found.", status=404)
    if purpose not in STEP_UP_PURPOSES:
        raise ApiError("invalid_request", "Some values are missing or invalid.")
    try:
        require_step_up(account, code, purpose)
    except ApiError:
        raise ApiError(
            "step_up_code_invalid", "That code is wrong or has expired. Ask for a new one."
        ) from None


def clear_step_up(account, purpose):
    for channel in STEP_UP_CHANNELS:
        value, scope = _step_code_scope(account, purpose, channel)
        codes.clear_code(value, scope)


def send_step_up(account, ip, channel, purpose):
    """Send a one-time code to the account's email or verified mobile: proof that the person
    holds one of them, asked for before an email or mobile change can start. Only while
    ACCOUNT_EMAIL_CHANGE_HARDENED is on."""
    if not settings.ACCOUNT_EMAIL_CHANGE_HARDENED:
        raise ApiError("not_found", "Not found.", status=404)
    if purpose not in STEP_UP_PURPOSES or channel not in STEP_UP_CHANNELS:
        raise ApiError("invalid_request", "Some values are missing or invalid.")
    if channel not in step_up_channels(account):
        raise ApiError(
            "step_up_channel_unavailable", "We cannot send a code there for this account."
        )
    if purpose == "email_change":
        _refuse_paused(account)
    _throttle_email_change(account)  # one limit across both channels
    value, scope = _step_code_scope(account, purpose, channel)
    code = codes.issue_code(value, ip, scope)
    try:
        if channel == "email":
            _mail_step_up(account, code)
        else:
            _text_step_up(account, code)
    except (EmailNotSent, ApiError):
        codes.release_cooldown(value, ip, scope)
        raise
    # The event holds the channel and purpose only: never an address, a number or a code.
    record_event(account, f"{purpose}_step_up", channel=channel, purpose=purpose)


def _mail_step_up(account, code):
    send_email(
        to=account.email,
        category="email_change_step_up",
        subject="Confirm it is you on NegosyoOnline",
        **account_mail(
            "email_change_current",
            f"Your confirmation code is {code}. It expires in 10 minutes.\n\n"
            "If you did not ask to change your email or mobile number, you can ignore this email.",
            code=code,
        ),
    )


def _text_step_up(account, code):
    held = AccountMobile.objects.get(account=account)
    result = send_sms(
        to=held.number,
        purpose="mobile_verify",
        text=STEP_UP_SMS.format(code=code),
        ttl=codes.CODE_TTL,
    )
    if result.outcome != SMS_QUEUED:
        paused = result.reason in ("paused", "platform_limit")
        raise ApiError(
            "sms_paused" if paused else "sms_unavailable",
            "Please try again shortly."
            if paused
            else "Text messages are not available right now. Please try again later.",
            status=503,
        )


def send_email_change_step_up(account, ip):
    """The first route, kept: a code to the CURRENT email for an email change."""
    send_step_up(account, ip, "email", "email_change")


def start_email_change(account, new_email, ip, step_up_code=""):
    """Email a code to the new address. The email changes only when that code is confirmed.

    With ACCOUNT_EMAIL_CHANGE_HARDENED on, the code sent to the current address must come
    first, and an address that already has an account is answered like a free one (nothing is
    sent), so a signed-in person cannot learn which addresses are in use."""
    hardened = settings.ACCOUNT_EMAIL_CHANGE_HARDENED
    _refuse_paused(account)
    new_email = normalise_email(new_email)
    if new_email == account.email:
        raise ApiError("email_unchanged", "That is already your email.")
    _throttle_email_change(account)
    if hardened:
        require_step_up(account, step_up_code, "email_change")
    if Account.objects.filter(email=new_email).exists():
        if hardened:
            record_event(account, "email_change_started")
            return
        raise _email_taken()
    purpose = _change_purpose(account)
    code = codes.issue_code(new_email, ip, purpose)
    try:
        send_email(
            to=new_email,
            category="email_change_code",
            subject="Confirm your new NegosyoOnline email",
            **account_mail(
                "email_change",
                f"Your confirmation code is {code}. It expires in 10 minutes.\n\n"
                "If you did not ask to use this email for NegosyoOnline, you can ignore this email.",
                code=code,
            ),
        )
    except EmailNotSent:
        codes.release_cooldown(new_email, ip, purpose)
        raise
    record_event(account, "email_change_started")


def confirm_email_change(account, new_email, code, current_session):
    """Change the email, sign out the other devices, start the pause and tell the old address."""
    old_email = _apply_email_change(account, new_email, code, current_session)
    try:
        # The new address is left out: this email goes to whoever holds the old mailbox.
        send_email(
            to=old_email,
            category="email_changed_notice",
            subject="Your NegosyoOnline email was changed",
            **account_mail(
                "email_changed_notice",
                "The email address of your NegosyoOnline account was just changed, so this "
                "address can no longer be used to sign in. Every other device was signed out.\n\n"
                f"If you did not make this change, contact {SUPPORT_EMAIL} right away.",
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
    clear_step_up(account, "email_change")
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
            **account_mail(
                "connection_notice",
                f"{change}\n\nIf you did not make this change, contact {SUPPORT_EMAIL} right away.",
                subject=subject,
                heading=subject.replace(" to your NegosyoOnline account", "").replace(
                    " from your NegosyoOnline account", ""
                ),
                change=change,
            ),
        )
    except EmailNotSent:
        pass  # already logged and recorded as failed; the change itself stands
