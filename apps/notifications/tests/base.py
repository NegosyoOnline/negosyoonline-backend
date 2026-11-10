"""Helpers shared by the SMS tests."""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsPolicy

MOBILE = "+639171234567"
CODE_TEXT = "Your NegosyoOnline code is 123456. It expires in 10 minutes. Do not share it."
STATUS_TEXT = "Your order 1042 from Anas Store is on its way."

# The tests run with DEBUG off and no Redis, where the gateway refuses to run; naming a Redis
# address turns it on. The cache itself stays the local one.
gateway_on = override_settings(SMS_GATEWAY_ENABLED=True, REDIS_URL="redis://redis.example.test/0")


def online_phone(name="system", **fields):
    fields.setdefault("last_contact_at", timezone.now())
    return SmsDevice.objects.create(name=name, token_hash=name.ljust(64, "0"), **fields)


def make_phone(name, token, **fields):
    """A phone that holds `token` and polled just now."""
    fields.setdefault("last_contact_at", timezone.now())
    return SmsDevice.objects.create(name=name, token_hash=sms.token_hash(token), **fields)


def set_policy(**fields):
    SmsPolicy.objects.update_or_create(pk=1, defaults=fields)


def fallback(to="buyer@example.test"):
    return sms.EmailFallback(to=to, subject="Your order is on its way", body="Order 1042 shipped.")


@gateway_on
class SmsTestCase(TestCase):
    """The gateway on, an empty cache, and an order-status purpose that only the tests declare."""

    def setUp(self):
        cache.clear()
        declared = {"order_status": sms.Purpose(category=sms.STATUS, priority=sms.Priority.STATUS)}
        patcher = mock.patch.dict(sms.PURPOSES, declared)
        patcher.start()
        self.addCleanup(patcher.stop)
        # No test starts a real background thread: a poll would otherwise start the day's
        # retention run beside the test's own database work. A case that needs the background
        # step to happen patches this again, to run it at once.
        later = mock.patch("apps.operators.background.run_later")
        later.start()
        self.addCleanup(later.stop)
