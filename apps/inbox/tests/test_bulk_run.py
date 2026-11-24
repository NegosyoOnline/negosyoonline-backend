"""Task 6 of plan 8: the batch driver, fed by the phone's poll. Made-up numbers only."""
import json
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.inbox import bulk
from apps.inbox.models import BulkBatch, BulkRecipient
from apps.inbox.tests.bulk_base import TEXT, BulkCase, mobile
from apps.inbox.tests.test_storing import TOKEN
from apps.notifications import gateway, services, sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.tests.base import CODE_TEXT, set_policy


class RunCase(BulkCase):
    def setUp(self):
        super().setUp()
        SmsDevice.objects.filter(pk=self.phone.pk).update(token_hash=sms.token_hash(TOKEN))

    def poll(self):
        return self.client.post(
            "/gateway/v1/poll", data="{}", content_type="application/json", secure=True,
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}", HTTP_X_REAL_IP="203.0.113.7",
        ).json()["messages"]

    def report(self, message_id, status="sent", reason=""):
        SmsMessage.objects.filter(public_id=message_id).update(status=status, reason=reason)

    def batch(self):
        return BulkBatch.objects.get()


class DriverTests(RunCase):
    def test_1_one_text_at_a_time_with_no_stop_footer(self):
        self.start(5, *(mobile(i) for i in range(1, 6)))
        handed = self.poll()
        self.assertEqual([m["to"] for m in handed], [mobile(1)])
        self.assertEqual(handed[0]["text"], TEXT)
        self.assertNotIn("STOP", handed[0]["text"])
        self.assertEqual(self.batch().state, "sending")
        self.assertEqual(self.poll(), [])  # the last is not reported yet
        self.report(handed[0]["id"])
        again = self.poll()
        self.assertEqual([m["to"] for m in again], [mobile(2)])
        self.assertEqual(self.batch().sent, 1)

    def test_2_a_code_and_a_staff_reply_go_before_the_next_bulk_text(self):
        self.start(2, mobile(1), mobile(2))
        first = self.poll()
        self.report(first[0]["id"])
        services.send_sms(to=mobile(50), purpose="mobile_verify", text=CODE_TEXT)
        services.send_sms(to=mobile(51), purpose="inbox_reply", text="Hi", ttl=600)
        handed = [m["to"] for m in self.poll()]
        self.assertEqual(handed, [mobile(50), mobile(51)])
        self.assertEqual(SmsMessage.objects.get(recipient=mobile(2)).status, "queued")

    def test_3_phone_offline_queues_nothing(self):
        self.start(2, mobile(1), mobile(2))
        SmsDevice.objects.update(last_contact_at=timezone.now() - timezone.timedelta(minutes=10))
        bulk.feed(timezone.now())
        self.assertEqual((self.batch().state, SmsMessage.objects.filter(purpose="inbox_bulk", status="queued").count()), ("waiting", 0))
        self.assertEqual(BulkRecipient.objects.filter(state="waiting").count(), 2)

    def test_4_sending_stopped_queues_nothing(self):
        self.start(2, mobile(1), mobile(2))
        set_policy(sending_stopped=True)
        self.assertEqual(self.poll(), [])
        self.assertEqual(BulkRecipient.objects.filter(state="waiting").count(), 2)

    def test_5_at_the_staff_limit_the_rest_are_not_sent_and_the_batch_finishes(self):
        self.start(3, mobile(1), mobile(2), mobile(3))
        first = self.poll()
        self.report(first[0]["id"])
        set_policy(platform_daily_limit=300)
        from django.core.cache import cache
        cache.clear()
        # one text already counted today (the first), so the room is gone
        self.poll()
        batch = self.batch()
        self.assertEqual((batch.state, batch.sent, batch.not_sent, batch.failed), ("finished", 1, 2, 0))
        self.assertEqual(batch.recipients.count(), 0)

    def test_6_a_failed_report_counts_with_its_reason(self):
        self.start(1, mobile(1))
        handed = self.poll()
        self.report(handed[0]["id"], "failed", "phone_failed")
        self.poll()
        batch = self.batch()
        self.assertEqual((batch.state, batch.failed, batch.sent), ("finished", 1, 0))

    def test_7_a_day_change_ends_the_rest(self):
        self.start(3, mobile(1), mobile(2), mobile(3))
        first = self.poll()
        self.report(first[0]["id"])
        with mock.patch("apps.inbox.bulk.timezone.localdate", return_value=timezone.localdate() + timezone.timedelta(days=1)):
            self.poll()
        batch = self.batch()
        self.assertEqual((batch.state, batch.sent, batch.not_sent), ("finished", 1, 2))

    def test_8_finished_batch_keeps_counts_and_loses_its_rows(self):
        self.start(2, mobile(1), mobile(2))
        for _ in range(2):
            self.report(self.poll()[0]["id"])
        self.poll()
        batch = self.batch()
        self.assertEqual((batch.state, batch.sent, batch.size, batch.recipients.count()), ("finished", 2, 2, 0))
        self.assertIsNotNone(batch.finished_at)
        again = bulk.list_batches(self.scope())[0][0]
        self.assertEqual((again["sent"], again["waiting"]), (2, 0))

    def test_9_stop(self):
        self.start(5, *(mobile(i) for i in range(1, 6)))
        first = self.poll()
        self.report(first[0]["id"])
        second = self.poll()  # queued and handed to the phone
        self.report(second[0]["id"])
        row = bulk.stop(self.scope(), self.batch().public_id)
        self.assertEqual(row["state"], "stopped")
        batch = self.batch()
        self.assertEqual((batch.sent, batch.not_sent, batch.recipients.count()), (2, 3, 0))
        self.assertEqual(self.poll(), [])
        # the one-at-a-time rule is free at once
        self.start(1, mobile(9))
        with self.assertRaises(Exception):
            bulk.stop(self.scope(), batch.public_id)

    def test_10_stop_takes_back_a_queued_text_and_leaves_a_dispatched_one(self):
        self.start(3, mobile(1), mobile(2), mobile(3))
        handed = self.poll()  # mobile(1) with the phone
        batch = self.batch()
        bulk.stop(self.scope(), batch.public_id)
        batch.refresh_from_db()
        self.assertEqual(batch.recipients.filter(state="queued").count(), 1)  # left to finish
        self.report(handed[0]["id"])
        bulk.settle_pending()
        batch.refresh_from_db()
        self.assertEqual((batch.sent, batch.not_sent, batch.recipients.count()), (1, 2, 0))

    def test_11_stopping_a_finished_batch_is_409(self):
        self.start(1, mobile(1))
        self.report(self.poll()[0]["id"])
        self.poll()
        with self.assertRaises(Exception) as caught:
            bulk.stop(self.scope(), self.batch().public_id)
        self.assertEqual(caught.exception.status, 409)

    def test_12_queued_text_is_taken_back_on_stop(self):
        self.start(2, mobile(1), mobile(2))
        bulk.feed(timezone.now())  # queued, not handed out
        bulk.stop(self.scope(), self.batch().public_id)
        message = SmsMessage.objects.get(purpose="inbox_bulk")
        self.assertEqual((message.status, message.reason), ("expired", "stopped"))
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.batch().recipients.count(), 0)

    def test_13_switch_off_the_poll_does_no_bulk_work(self):
        with override_settings(SMS_BULK_ENABLED=False), mock.patch("apps.inbox.bulk.feed") as feed:
            self.poll()
        feed.assert_not_called()
        with override_settings(SMS_BULK_ENABLED=False), CaptureQueriesContext(connection) as queries:
            self.poll()
        self.assertFalse(any("inbox_bulkbatch" in q["sql"] or "inbox_bulkrecipient" in q["sql"] for q in queries.captured_queries))
