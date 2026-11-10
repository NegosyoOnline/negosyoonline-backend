"""The three calls the phone makes. Plain views outside /v1/, so they are not in the published
API. Each is a POST with a JSON body and the phone's token: `Authorization: Bearer <token>`.

    POST /gateway/v1/poll
        sends    {"app_version": "1.0.0", "battery": 87, "charging": true}   (all optional)
        answers  {"messages": [{"id", "to", "text", "expires_at", "gap"}], "wait": 5,
                  "incoming": false}
        Up to 5 texts. Each is handed out once. `gap` is the seconds to keep since the last
        text that carried a gap (0: send at once). `wait` is the seconds before the next poll.
        `incoming` says whether this phone forwards the texts it receives.

    POST /gateway/v1/report
        sends    {"reports": [{"id": "sms_...", "status": "sent" | "failed", "reason": "..."}]}
        answers  204. Repeating a report changes nothing.

    POST /gateway/v1/received
        sends    {"messages": [{"id", "from", "text", "received_at", "sim_slot"}]}   (up to 20)
        answers  204: all taken, drop them.

A token that is missing, wrong, rotated or of a disabled phone gets one and the same 401.
While SMS_GATEWAY_ENABLED is off every call answers 404.
"""
import json
import logging
import random
import re
from datetime import timedelta
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.sessions import client_ip
from apps.common.errors import error_body
from apps.notifications import services, sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.operators import background

logger = logging.getLogger(__name__)

Status = SmsMessage.Status
BATCH = 5  # texts in one poll answer
WAIT = 5  # seconds before the phone asks again
WAIT_STOPPED = 30  # the same, while sending is stopped
GAP_SECONDS = (18, 25)  # the pause before a marketing text, chosen for each one
FAILED_TRIES = 10  # bad tokens from one IP address ...
FAILED_TRIES_WINDOW = 15 * 60  # ... in this many seconds
MAX_REPORTS = 100
SWEEP = 200  # texts expired or failed in one poll; the rest wait for the next one
PHONE_REASON = "phone_failed"


def _error(status, code, message):
    return JsonResponse(error_body(code, message), status=status)


def _tries_key(request):
    return f"sms:gateway:failed-tries:{client_ip(request) or 'unknown'}"


def _device(request):
    """The phone that holds the token in the request, or None."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme != "Bearer" or not token.strip():
        return None
    return SmsDevice.objects.filter(
        token_hash=sms.token_hash(token.strip()), is_active=True
    ).first()


def phone_call(view):
    """A call from a phone: POST only, gateway on, a good token. The view gets the phone."""

    @csrf_exempt
    @wraps(view)
    def wrapped(request):
        if not sms.gateway_on():
            return _error(404, "not_found", "No such endpoint.")
        if request.method != "POST":
            return _error(405, "method_not_allowed", "Use POST.")
        key = _tries_key(request)
        if (cache.get(key) or 0) >= FAILED_TRIES:
            return _error(429, "rate_limited", "Too many attempts. Please wait and try again.")
        device = _device(request)
        if device is None:
            cache.add(key, 0, FAILED_TRIES_WINDOW)
            try:
                cache.incr(key)
            except ValueError:  # the entry expired between the add and the incr
                cache.set(key, 1, FAILED_TRIES_WINDOW)
            return _error(401, "unauthenticated", "Unknown phone.")
        return view(request, device)

    return wrapped


def _body(request):
    """The JSON object in the request, or None when it is not one."""
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


# --- Texts that will not be sent after all -------------------------------------------------


def _not_going_out(message, reason):
    """A queued or handed-out text ended unsent: email its fallback, when it has one."""
    fallback = sms.take_fallback(message)
    if fallback is None:
        sms.log_not_sent(reason, message.purpose)
    else:  # in the background, so the phone's call is not held up by the mail server
        background.run_later(services.email_instead, message, reason, message.purpose, fallback)


def _sweep(now):
    """Texts whose time passed while waiting, and texts the phone took and never reported."""
    expired = SmsMessage.objects.filter(status=Status.QUEUED, expires_at__lte=now)
    for message in expired.order_by("pk")[:SWEEP]:
        if SmsMessage.objects.filter(pk=message.pk, status=Status.QUEUED).update(
            status=Status.EXPIRED
        ):
            sms.take_text(message)
            _not_going_out(message, "expired")
    silent_since = now - timedelta(seconds=sms.NO_REPORT_AFTER)
    unreported = SmsMessage.objects.filter(status=Status.DISPATCHED, dispatched_at__lte=silent_since)
    for message in unreported.order_by("pk")[:SWEEP]:
        if SmsMessage.objects.filter(pk=message.pk, status=Status.DISPATCHED).update(
            status=Status.FAILED, reason="no_report"
        ):
            _not_going_out(message, "no_report")


# --- poll ----------------------------------------------------------------------------------


def _note_contact(device, body, now):
    device.last_contact_at = now
    version = body.get("app_version")
    if isinstance(version, str):
        device.app_version = version[:20]
    battery = body.get("battery")
    if isinstance(battery, int) and not isinstance(battery, bool) and 0 <= battery <= 100:
        device.battery_percent = battery
    charging = body.get("charging")
    if isinstance(charging, bool):
        device.is_charging = charging
    device.save(
        update_fields=["last_contact_at", "app_version", "battery_percent", "is_charging"]
    )


def _waiting(device, now):
    """The next texts for this phone: by sending class, then oldest first. A text that carries
    a gap is handed out one at a time, and the next only after the first is reported, so a
    paced text never sits on the phone past its expiry."""
    queued = SmsMessage.objects.filter(status=Status.QUEUED, expires_at__gt=now)
    paced = queued.filter(priority=SmsMessage.Priority.MARKETING)
    order = ("priority", "created_at", "pk")
    chosen = list(queued.exclude(priority=SmsMessage.Priority.MARKETING).order_by(*order)[:BATCH])
    pacing_now = SmsMessage.objects.filter(
        device=device, status=Status.DISPATCHED, priority=SmsMessage.Priority.MARKETING
    )
    if len(chosen) < BATCH and not pacing_now.exists():
        chosen += list(paced.order_by(*order)[:1])
    return chosen


def _hand_out(device, now):
    handed = []
    for message in _waiting(device, now):
        taken = SmsMessage.objects.filter(pk=message.pk, status=Status.QUEUED).update(
            status=Status.DISPATCHED, device=device, dispatched_at=now
        )
        if not taken:  # another call took it first
            continue
        text = sms.take_text(message)
        if text is None:
            SmsMessage.objects.filter(pk=message.pk).update(status=Status.FAILED, reason="text_lost")
            _not_going_out(message, "text_lost")
            continue
        paced = message.priority == SmsMessage.Priority.MARKETING
        handed.append(
            {
                "id": message.public_id,
                "to": message.recipient,
                "text": text,
                "expires_at": message.expires_at.isoformat(),
                "gap": random.randint(*GAP_SECONDS) if paced else 0,
            }
        )
    return handed


@phone_call
def poll(request, device):
    body = _body(request)
    if body is None:
        return _error(400, "invalid_request", "Send a JSON object.")
    now = timezone.now()
    _note_contact(device, body, now)
    _sweep(now)
    answer = {"messages": [], "wait": WAIT, "incoming": device.forwards_incoming}
    if sms.policy().sending_stopped:
        answer["wait"] = WAIT_STOPPED
    elif device == sms.pick_device():
        answer["messages"] = _hand_out(device, now)
        if len(answer["messages"]) == BATCH:
            answer["wait"] = 0  # more may be waiting: ask again as soon as these are sent
    return JsonResponse(answer)


# --- report --------------------------------------------------------------------------------


def _short_reason(raw):
    """The phone's reason as a few lower-case words. Digits are dropped, so it can never
    carry a number or a code."""
    words = re.findall(r"[a-z]+", raw.lower()) if isinstance(raw, str) else []
    return "_".join(words)[:40].strip("_") or PHONE_REASON


def _reports(body):
    """The reports in the body as (id, status, reason), or None when the shape is wrong."""
    reports = body.get("reports") if body else None
    if not isinstance(reports, list) or len(reports) > MAX_REPORTS:
        return None
    cleaned = []
    for report in reports:
        if not isinstance(report, dict) or not isinstance(report.get("id"), str):
            return None
        if report.get("status") not in ("sent", "failed"):
            return None
        cleaned.append((report["id"], report["status"], _short_reason(report.get("reason"))))
    return cleaned


@phone_call
def report(request, device):
    reports = _reports(_body(request))
    if reports is None:
        return _error(400, "invalid_request", "Send the reports as a list of id and status.")
    messages = {
        message.public_id: message
        for message in SmsMessage.objects.filter(
            public_id__in=[public_id for public_id, _, _ in reports], device=device
        )
    }
    if any(public_id not in messages for public_id, _, _ in reports):
        return _error(403, "forbidden", "A text in this report was not handed to this phone.")
    now = timezone.now()
    for public_id, status, reason in reports:
        message = messages[public_id]
        handed_out = SmsMessage.objects.filter(pk=message.pk, status=Status.DISPATCHED)
        if status == "sent":
            if handed_out.update(status=Status.SENT, sent_at=now):
                sms.take_fallback(message)  # not owed any more
        elif handed_out.update(status=Status.FAILED, reason=reason):
            _not_going_out(message, reason)
    return HttpResponse(status=204)


# --- received ------------------------------------------------------------------------------


def store_incoming(device, raw):
    """Where texts the phone received would be kept. Nothing is kept: there is no inbox yet,
    so the body is dropped unread, whatever the phone's switch says. The Dedicated SMS Line
    add-on replaces this body and nothing else."""


@phone_call
def received(request, device):
    store_incoming(device, request.body)
    return HttpResponse(status=204)
