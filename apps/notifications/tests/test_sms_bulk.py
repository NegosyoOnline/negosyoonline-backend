"""Task 3 of plan 8: the inbox_bulk purpose, the room for staff texts and taking a text back."""
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.notifications import gateway, services, sms
from apps.notifications.models import SmsMessage
from apps.notifications.tests.base import CODE_TEXT, MOBILE, SmsTestCase, online_phone, set_policy

TEXT = "Hello from NegosyoOnline. Reply STOP to opt out"


class BulkPurposeTests(SmsTestCase):
    def setUp(self):
        super().setUp()
        self.phone = online_phone()

    def bulk(self, to=MOBILE):
        return services.send_sms(to=to, purpose="inbox_bulk", text=TEXT, ttl=services.SMS_STAFF_TEXT_LIFETIME)

    def test_1_the_purpose_is_declared_paced_and_in_the_inbox_category(self):
        declared = sms.PURPOSES["inbox_bulk"]
        self.assertEqual((declared.category, declared.paced), (sms.INBOX, True))
        self.assertIn("inbox_bulk", services.STAFF_PURPOSES)
        self.assertEqual(
            sorted(sms.PURPOSES), sorted(["mobile_verify", "inbox_reply", "inbox_compose", "inbox_bulk", "order_status"])
        )

    def test_2_a_code_and_a_staff_text_go_out_before_a_bulk_text(self):
        bulk = self.bulk("+639170000301")
        reply = services.send_sms(to="+639170000302", purpose="inbox_reply", text="Hi", ttl=600)
        code = services.send_sms(to="+639170000303", purpose="mobile_verify", text=CODE_TEXT)
        first = gateway._waiting(self.phone, timezone.now())
        self.assertEqual(first[0].public_id, code.message.public_id)
        # the replies are paced too: one paced text at a time, the reply before the bulk text
        self.assertEqual(first[1].public_id, reply.message.public_id)
        self.assertNotIn(bulk.message.public_id, [m.public_id for m in first])

    def test_3_a_bulk_text_is_refused_at_the_staff_limit_but_a_code_is_not(self):
        set_policy(platform_daily_limit=300)
        result = self.bulk()
        self.assertEqual((result.outcome, result.reason), (sms.REFUSED, "staff_limit"))
        code = services.send_sms(to=MOBILE, purpose="mobile_verify", text=CODE_TEXT)
        self.assertEqual(code.outcome, sms.QUEUED)

    def test_4_room_for_staff(self):
        for i in range(214):
            SmsMessage.objects.create(
                recipient=MOBILE, category=sms.CODE, purpose="mobile_verify", priority=sms.Priority.CODE,
                status=SmsMessage.Status.SENT, expires_at=timezone.now() + timedelta(hours=1),
            )
        cache.clear()
        self.assertEqual(services.sms_room_for_staff(), (214, 1486))

    def test_5_room_for_staff_reads_nothing_while_the_gateway_is_off(self):
        with override_settings(SMS_GATEWAY_ENABLED=False), self.assertNumQueries(0):
            self.assertEqual(services.sms_room_for_staff(), (0, 0))

    def test_6_cancel_takes_back_a_queued_text_only(self):
        queued = self.bulk().message
        self.assertTrue(services.cancel_sms(queued.public_id))
        queued.refresh_from_db()
        self.assertEqual((queued.status, queued.reason), (SmsMessage.Status.EXPIRED, "stopped"))
        self.assertIsNone(sms.take_text(queued))
        taken = self.bulk("+639170000304").message
        SmsMessage.objects.filter(pk=taken.pk).update(status=SmsMessage.Status.DISPATCHED)
        self.assertFalse(services.cancel_sms(taken.public_id))
        taken.refresh_from_db()
        self.assertEqual(taken.status, SmsMessage.Status.DISPATCHED)
        self.assertFalse(services.cancel_sms("sms_missing"))
