"""How a member's one-time code reaches a handset: the transport PK2 left a seam for.

A code goes by SMS through apps.notifications.services.send_sms with no email fallback of its own:
TWCinc only OFFERS the email, and the member then asks for it. The challenge row is written first;
a text that cannot go out supersedes it, so its code never verifies. Two codes only: signing in by
mobile, and confirming the mobile after the first order. Nothing else is texted from here.
"""
import logging

from apps.common.errors import ApiError
from apps.members import messaging, otp
from apps.members.models import MemberOtpChallenge
from apps.notifications import sms
from apps.notifications.models import SmsMessage
from apps.notifications.services import send_sms, sms_statuses

logger = logging.getLogger(__name__)

PURPOSES = {"signin": "member_signin_code", "onboarding": "member_mobile_verify"}
PAUSED_REASONS = frozenset({"paused", "platform_limit"})
FAILED_STATUSES = frozenset({"failed", "expired"})


def available():
    """Can a text go out at all: the gateway switch is on and its Redis is there."""
    return sms.gateway_on()


def precheck(site, mobile):
    """Before any member lookup, the same for every input: the SMS pause and the day's platform
    ceiling answer sms_paused, so a refusal never tells a member from a stranger."""
    rules = sms.policy()
    if rules.sending_stopped or sms.platform_count() >= rules.platform_daily_limit:
        raise ApiError("sms_paused", messaging.PAUSE_MESSAGE, status=503)


def _is_disposed(member):
    """A disposed member's number is a placeholder no handset has; it is never texted."""
    from apps.members.retention import _placeholder_mobile

    return member is None or member.mobile == _placeholder_mobile(member)


def _hold(challenge):
    otp.mark_held(challenge.pk)


def deliver_code(challenge, code, sms_category):
    """queued | paused | offer_email | sms_unavailable | held. A code that is not queued is superseded."""
    purpose = PURPOSES.get(challenge.purpose)
    member = challenge.member
    if purpose is None or (member is not None and _is_disposed(member)):
        _hold(challenge)
        return "held" if purpose else "sms_unavailable"
    text = messaging.otp_sms_body(challenge.site, code, challenge.purpose)
    try:
        result = send_sms(to=challenge.target, purpose=purpose, text=text, site=challenge.site, fallback=None)
    except ValueError:
        logger.error("member code text refused as malformed, purpose=%s", purpose)
        _hold(challenge)
        return "sms_unavailable"
    if result.outcome == sms.QUEUED:
        MemberOtpChallenge.objects.filter(pk=challenge.pk).update(sms_message_id=result.message.pk)
        return "queued"
    _hold(challenge)
    if result.reason in PAUSED_REASONS:
        return "paused"
    if challenge.purpose == "signin" and member is not None and member.email_state == "verified":
        return "offer_email"
    return "sms_unavailable"


def refresh_delivery(challenge):
    """Read the text's state: a failed or expired one marks the row failed and supersedes it, so the
    page can offer a retry or the email. Without a record to read, the member waits out the ten minutes."""
    if not challenge.sms_message_id or challenge.delivery in ("failed", "held"):
        return challenge.delivery
    public_id = SmsMessage.objects.filter(pk=challenge.sms_message_id).values_list("public_id", flat=True).first()
    status = sms_statuses([public_id]).get(public_id, ("", ""))[0]
    if status in FAILED_STATUSES:
        otp.on_sms_delivery(challenge.sms_message_id, "failed")
        return "failed"
    if status == "sent":
        otp.on_sms_delivery(challenge.sms_message_id, "sent")
        return "sent"
    return challenge.delivery


def register():
    from apps.members import hooks

    hooks.register_sms_transport(available=available, deliver_code=deliver_code, precheck=precheck)
