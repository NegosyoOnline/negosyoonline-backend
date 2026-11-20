"""The platform phone: register, rotate, disable and read it.

The HQ screens (apps.notifications.staff) and `manage.py sms_device` both call these functions,
so the two cannot diverge. Only `register` and `rotate` ever see a token, and they return it to
their caller once; it is never stored, logged or written to the audit log. Only its SHA-256 is
kept (sms.token_hash). None of these functions touches `forwards_incoming`.
"""
import logging
import re
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.notifications import sms
from apps.notifications.models import SmsDevice
from apps.operators import audit

logger = logging.getLogger(__name__)

TARGET = "sms_device"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
ONLINE_WITHIN = timedelta(seconds=60)

CONNECTED, WAITING, OFFLINE, DISABLED, NO_PHONE = (
    "connected", "waiting", "offline", "disabled", "no_phone",
)


class DeviceError(Exception):
    """A refusal with a stable code and a plain message."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def clean_name(raw):
    name = raw.strip().lower() if isinstance(raw, str) else ""
    if not NAME.match(name):
        raise DeviceError("invalid_name", "The name needs lower-case letters, digits and hyphens.")
    return name


def platform_phone():
    """The phone the screens show: the one that sends (the first active phone), else the newest
    disabled one, else None. Until phones belong to Sites, every phone is a platform phone."""
    return sms.pick_device() or SmsDevice.objects.order_by("-pk").first()


def _record(actor, action, device, via):
    audit.record(
        actor, action, target_type=TARGET, target_id=device.pk,
        detail={"device": device.pk, "name": device.name, "via": via},
    )
    logger.error("sms-device-change: %s on phone %s by operator %s", action, device.pk, actor.pk)


def _locked(name, platform_only):
    device = SmsDevice.objects.select_for_update().filter(name=name).first()
    if device is None or (platform_only and device.pk != getattr(platform_phone(), "pk", None)):
        raise DeviceError("not_found", "No such phone.")
    return device


def register(actor, name, *, via="hq", single=True):
    """Add a phone. Returns (device, token); the token is shown once. With `single` (the
    screens) it is refused while an active platform phone exists."""
    name = clean_name(name)
    with transaction.atomic():
        if single and sms.pick_device() is not None:
            raise DeviceError("phone_exists", "A phone is already registered.")
        if SmsDevice.objects.filter(name=name).exists():
            raise DeviceError("name_taken", "A phone with that name already exists.")
        token = secrets.token_urlsafe(32)
        device = SmsDevice.objects.create(name=name, token_hash=sms.token_hash(token))
        _record(actor, "sms_device_add", device, via)
    return device, token


def rotate(actor, name, *, via="hq", platform_only=True):
    """A new token for a phone; the old one fails at once. Returns (device, token). A disabled
    phone stays disabled."""
    with transaction.atomic():
        device = _locked(clean_name(name), platform_only)
        token = secrets.token_urlsafe(32)
        device.token_hash = sms.token_hash(token)
        device.rotated_at = timezone.now()
        device.save(update_fields=["token_hash", "rotated_at"])
        _record(actor, "sms_device_rotate", device, via)
    return device, token


def disable(actor, name, *, via="hq", platform_only=True):
    """Stop a phone. Returns (device, changed). There is no enable."""
    with transaction.atomic():
        device = _locked(clean_name(name), platform_only)
        if not device.is_active:
            return device, False
        device.is_active = False
        device.save(update_fields=["is_active"])
        _record(actor, "sms_device_disable", device, via)
    return device, True


def connection(device, now=None):
    if device is None:
        return NO_PHONE
    if not device.is_active:
        return DISABLED
    now = now or timezone.now()
    token_changed = device.rotated_at or device.created_at
    if device.last_contact_at is None or device.last_contact_at < token_changed:
        return WAITING
    if device.last_contact_at >= now - ONLINE_WITHIN:
        return CONNECTED
    return OFFLINE


def status(device=None):
    """The platform phone as the screens show it, or None. Never a token or a hash."""
    device = device or platform_phone()
    if device is None:
        return None
    return {
        "name": device.name,
        "enabled": device.is_active,
        "forwards_incoming": device.forwards_incoming,
        "last_poll_at": device.last_contact_at.isoformat() if device.last_contact_at else None,
        "app_version": device.app_version or None,
        "battery": device.battery_percent,
        "charging": device.is_charging,
        "connection": connection(device),
    }
