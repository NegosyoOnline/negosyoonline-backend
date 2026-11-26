"""ONE-PHONE (Coach Ed, 2026-11-26, ClickUp z8uq9mk7hh): a store entitled to a line with no phone of
its own sends its STORE_BULK texts from the platform phone; a store with its own active phone keeps
using only that phone. Codes and replies still go first and bulk stays paced one at a time."""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.notifications import gateway, sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.services import send_sms
from apps.notifications.tests.base import CODE_TEXT, MOBILE, set_policy
from apps.notifications.tests.test_sms_site_lines import S, TOKENS, LineCase, lines_on  # noqa: F401
from apps.notifications.tests.test_sms_store import store_text

TEXT = "Sale this weekend, 20 percent off."


def bulk(site, to=MOBILE):
    return send_sms(to=to, purpose="store_bulk", text=TEXT, site=site, ttl=sms.STAFF_TEXT_LIFETIME)


@lines_on
class OnePhoneTests(LineCase):
    """Store e is entitled (payment needing attention) and has no phone; store a has its own phone;
    store b's billing ended (not entitled)."""

    def test_line_for_bulk_uses_the_platform_phone_only_without_an_own_phone(self):
        self.assertEqual(sms.line_for(self.sites["e"], sms.STORE_BULK), self.phone)
        self.assertEqual(sms.line_for(self.sites["a"], sms.STORE_BULK), self.phones["a"])
        self.assertIsNone(sms.line_for(self.sites["b"], sms.STORE_BULK))
        self.assertEqual(sms.bulk_phone(self.sites["e"]), self.phone)

    def test_a_site_with_no_entitlement_has_no_bulk_phone_and_nothing_is_queued(self):
        result = bulk(self.sites["b"])
        self.assertEqual((result.outcome, result.reason), (sms.REFUSED, "no_line"))
        self.assertFalse(SmsMessage.objects.filter(category=sms.STORE_BULK, status=S.QUEUED).exists())

    @override_settings(SMS_SITE_LINES_ENABLED=False)
    def test_with_the_site_lines_switch_off_bulk_stays_blocked_no_line(self):
        self.assertIsNone(sms.bulk_phone(self.sites["e"]))
        result = bulk(self.sites["e"])
        self.assertEqual((result.outcome, result.reason), (sms.REFUSED, "no_line"))
        self.assertFalse(SmsMessage.objects.filter(category=sms.STORE_BULK, status=S.QUEUED).exists())

    def test_the_platform_phone_claims_bulk_of_a_site_without_a_phone_only(self):
        mine = bulk(self.sites["e"]).message
        own = bulk(self.sites["a"]).message
        claim = sms.claimable(self.phone)
        ids = set(SmsMessage.objects.filter(claim).values_list("pk", flat=True))
        self.assertIn(mine.pk, ids)
        self.assertNotIn(own.pk, ids)
        own_ids = set(SmsMessage.objects.filter(sms.claimable(self.phones["a"])).values_list("pk", flat=True))
        self.assertIn(own.pk, own_ids)
        self.assertNotIn(mine.pk, own_ids)

    def test_the_platform_phone_is_handed_the_text_over_the_poll(self):
        message = bulk(self.sites["e"]).message
        with mock.patch.object(gateway, "_waiting", wraps=gateway._waiting):
            handed = self.handed(base_token())
        self.assertIn(message.public_id, handed)

    def test_refusal_order_bulk_limit_then_no_line_then_offline(self):
        site = self.sites["e"]
        set_policy(store_bulk_daily_limit=1)
        self.assertEqual(bulk(site).outcome, sms.QUEUED)
        self.assertEqual(bulk(site).reason, "bulk_limit")
        set_policy(store_bulk_daily_limit=2000)
        SmsDevice.objects.filter(pk=self.phone.pk).update(last_contact_at=None)
        self.assertEqual(bulk(site).reason, "gateway_offline")  # the platform phone is away
        self.assertEqual(bulk(self.sites["a"]).outcome, sms.QUEUED)  # its own phone is online
        SmsDevice.objects.filter(pk=self.phones["a"].pk).update(last_contact_at=None)
        self.assertEqual(bulk(self.sites["a"]).reason, "site_phone_offline")
        self.assertEqual(bulk(self.sites["b"]).reason, "no_line")

    def test_bulk_does_not_take_a_platform_day_or_the_code_room(self):
        set_policy(platform_daily_limit=0)
        self.assertEqual(bulk(self.sites["e"]).outcome, sms.QUEUED)

    def test_a_code_and_a_reply_go_before_bulk_and_one_paced_text_is_out_at_a_time(self):
        first = bulk(self.sites["e"], "+639170000301").message
        second = bulk(self.sites["e"], "+639170000302").message
        reply = store_text(self.sites["e"], to="+639170000303").message
        code = send_sms(to="+639170000304", purpose="mobile_verify", text=CODE_TEXT).message
        waiting = gateway._waiting(self.phone, timezone.now())
        self.assertEqual(waiting[0].public_id, code.public_id)
        self.assertEqual(waiting[1].public_id, reply.public_id)
        self.assertNotIn(first.public_id, [m.public_id for m in waiting])
        # the reply goes out; now one bulk text only
        SmsMessage.objects.filter(pk__in=[code.pk, reply.pk]).update(status=S.SENT)
        waiting = gateway._waiting(self.phone, timezone.now())
        self.assertEqual(len(waiting), 1)
        self.assertIn(waiting[0].public_id, (first.public_id, second.public_id))


def base_token():
    from apps.notifications.tests.test_sms_gateway import TOKEN
    return TOKEN
