"""What a member is sent: the code emails, the SMS wording the SMS slice uses, and the notices to a
member's previous contacts. Bodies name the store's display name, never the platform. A code appears
only in the body handed to the provider, never in a subject, a log, a row or an error. The email
copy is the approved text; every SMS goes through the hook the SMS slice registers (hooks.py)."""
import logging

from django.db import transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.members import holds, hooks, otp
from apps.members.models import MemberOtpChallenge
from apps.notifications.services import EmailNotSent, send_email

logger = logging.getLogger(__name__)

HELD_OUTCOMES = frozenset({"paused", "refused_not_test_number", "offer_email"})
PAUSE_MESSAGE = "Please try again shortly."
NOT_TEST_NUMBER = "This number cannot receive messages yet."
SMS_UNAVAILABLE = "Text messages are not available yet. Please try again later."
MAIL_FAILED = "We could not send the email. Please try again."


def store_name(site):
    return "".join(ch for ch in (site.display_name or "") if ch not in "\r\n<>").strip() or site.public_id


def sender(site):
    """The store name as From name; the owner Account's email as Reply-To only once it is verified."""
    owner = site.account
    return store_name(site), (owner.email if owner.email_verified_at and owner.email else "")


def sms_available():
    transport = hooks.sms_transport()
    return bool(transport and transport["available"]())


def require_transport():
    if not sms_available():
        raise ApiError("sms_unavailable", SMS_UNAVAILABLE, status=503)


def outcome_error(outcome, *, field="contact"):
    """The ApiError a held outcome answers, or None."""
    if outcome == "paused":
        return ApiError("sms_paused", PAUSE_MESSAGE, status=503)
    if outcome == "refused_not_test_number":
        return ApiError("not_test_number", NOT_TEST_NUMBER, fields={field: [NOT_TEST_NUMBER]})
    if outcome == "mail_failed":
        return ApiError("mail_failed", MAIL_FAILED, status=503)
    return None


SMS_SIGNIN = "{name}: your sign-in code is {code}. It expires in 10 minutes. Never share this code with anyone."
SMS_BODIES = {
    "current_proof": "{name}: use code {code} to confirm you want to change your mobile number. "
                     "It expires in 10 minutes. Never share it.",
    "replacement_proof": "{name}: use code {code} to confirm this number for your account. "
                         "It expires in 10 minutes. Never share it.",
    "onboarding": "{name}: your mobile confirmation code is {code}. It expires in 10 minutes. "
                  "Do not share it.",
    "cod_confirmation": "{name}: your order confirmation code is {code}. It expires in 10 minutes. Do not share it.",
    "withdrawal": "{name}: your withdrawal confirmation code is {code}. It expires in 10 minutes. Never share it.",
    "payout_destination": "{name}: use code {code} to confirm your payout account. It expires in 10 minutes. "
                          "Never share it.",
}
_IGNORE = "If you did not ask for this code, you can ignore this email. Never share this code with anyone."
EMAIL_SIGNIN = ("Your {name} sign-in code", "Your sign-in code is {code}. It expires in 10 minutes.", _IGNORE)
_CHANGE = ("Confirm your email change for {name}", "Your confirmation code is {code}. It expires in 10 minutes.",
           "If you did not ask to change your email, ignore this message and contact the store. "
           "Never share this code with anyone.")
EMAIL_TEXTS = {  # purpose -> (subject, first line, closing line)
    "email_verify": ("Verify your email for {name}", "Your verification code is {code}. It expires in 10 minutes.",
                     _IGNORE),
    "current_proof": _CHANGE,
    "replacement_proof": _CHANGE,
    "onboarding": ("Confirm your email for {name}", "Your email confirmation code is {code}. It expires in 10 minutes.",
                   _IGNORE),
    "cod_confirmation": ("Your {name} order confirmation code", "Your order confirmation code is {code}. "
                         "It expires in 10 minutes.", _IGNORE),
    "withdrawal": ("Your {name} withdrawal code", "Your withdrawal confirmation code is {code}. It expires in 10 minutes.",
                   "If you did not ask for this code, do not share it and contact the store right away. "
                   "Never share this code with anyone."),
    "payout_destination": ("Confirm your payout account for {name}",
                           "Your payout account confirmation code is {code}. It expires in 10 minutes.",
                           "If you did not ask to change your payout account, do not share this code and contact "
                           "the store right away. Never share this code with anyone."),
}


def otp_sms_body(site, code, purpose="signin"):
    return SMS_BODIES.get(purpose, SMS_SIGNIN).format(name=store_name(site), code=code)


def otp_email(site, code, purpose="signin"):
    subject, first, last = EMAIL_TEXTS.get(purpose, EMAIL_SIGNIN)
    return subject.format(name=store_name(site)), f"{first.format(code=code)}\n{last}"


def deliver_code(challenge, code, *, sms_category="otp_no_fallback"):
    """sms: whatever the SMS slice's hook answers (queued, offer_email, paused,
    refused_not_test_number), or sms_unavailable with no hook. email: emailed | mail_failed."""
    site = challenge.site
    if challenge.channel == "sms":
        transport = hooks.sms_transport()
        if not transport:
            otp.mark_held(challenge.pk)
            return "sms_unavailable"
        outcome = transport["deliver_code"](challenge, code, sms_category)
        if outcome in HELD_OUTCOMES:
            otp.mark_held(challenge.pk)
        return outcome
    from_name, reply_to = sender(site)
    subject, body = otp_email(site, code, challenge.purpose)
    try:
        send_email(to=challenge.target, category=f"member_{challenge.purpose}", subject=subject, body=body,
                   sender_name=from_name, reply_to=reply_to)
    except (EmailNotSent, ValueError):
        logger.warning("member code email not sent, site %s", site.public_id)
        MemberOtpChallenge.objects.filter(pk=challenge.pk, delivery="sending").update(delivery="failed")
        return "mail_failed"
    MemberOtpChallenge.objects.filter(pk=challenge.pk, delivery="sending").update(delivery="sent")
    return "emailed"


def notice_texts(site, *, kind, field=None, hold_end=None, changed_at=None):
    """(category, sms_body, subject, email_body). Notices never carry a code, the new contact or a link."""
    name = store_name(site)
    if kind == "change":
        what = "mobile number" if field == "mobile" else "email"
        when = holds.display(changed_at or timezone.now())
        sms_body = f"{name}: the {what} on your account was changed on {when}. Not you? Contact the store now."
        email_body = (f"The {what} on your account was changed on {when}. "
                      "If this was not you, contact the store right away.")
        return "member_contact_changed", sms_body, f"Your {name} contact was changed", email_body
    until = holds.display(hold_end)
    sms_body = (f"{name}: your contact was changed after a recovery request. Payout changes and withdrawals are "
                f"paused until {until}. Not you? Contact the store.")
    email_body = ("A recovery request changed the sign-in contact on your account. For your security, payout "
                  f"changes and withdrawals are paused until {until}. If this was not you, contact the store "
                  "right away.")
    return "member_recovery_notice", sms_body, f"Your {name} contact was changed after a recovery request", email_body


def send_contact_notice(site, audit, *, kind, prior_verified, field=None, hold_end=None):
    """To every contact verified before the change: a text through the SMS hook when one is registered,
    and the email to each previous verified address. A failed notice is logged, never raised: the
    change is already committed."""
    category, sms_body, subject, email_body = notice_texts(
        site, kind=kind, field=field, hold_end=hold_end, changed_at=audit.created_at
    )
    transport = hooks.sms_transport()
    if transport and transport.get("send_notice"):
        for n, (kind_, value) in enumerate(v for v in prior_verified if v[0] == "mobile"):
            try:
                transport["send_notice"](site, value, sms_body, f"member_contact:{audit.pk}:sms:{n}")
            except Exception:  # noqa: BLE001 - the change is committed
                logger.exception("member contact notice text failed, site %s", site.public_id)
    from_name, reply_to = sender(site)
    for kind_, address in prior_verified:
        if kind_ != "email":
            continue
        try:
            send_email(to=address, category=category, subject=subject, body=email_body,
                       sender_name=from_name, reply_to=reply_to)
        except (EmailNotSent, ValueError):
            logger.warning("member contact notice email not sent, site %s", site.public_id)
