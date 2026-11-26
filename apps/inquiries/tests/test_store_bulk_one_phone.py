"""ONE-PHONE (Coach Ed, 2026-11-26, z8uq9mk7hh), end to end: a store entitled to a line with no phone of
its own has its Bulk and Schedule batches fed and sent by the platform phone, one paced text at a
time. A store with its own phone is never fed or claimed by the platform phone."""
import json
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.inquiries import store_bulk
from apps.inquiries.tests.test_store_bulk import NUMBERS, TOKEN, BulkCase
from apps.inquiries.tests.test_store_bulk_schedule import AFTER, ScheduleCase
from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsMessage, StoreBulkBatch

PLATFORM = "token-platform-one-phone"


class PlatformCalls:
    def pair_platform(self):
        SmsDevice.objects.filter(pk=self.platform.pk).update(token_hash=sms.token_hash(PLATFORM))

    def call(self, name, body=None, token=PLATFORM):
        return self.client.post(
            f"/gateway/v1/{name}", data=json.dumps(body or {}), content_type="application/json",
            secure=True, HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_REAL_IP="203.0.113.7",
        )

    def poll_platform(self):
        response = self.call("poll")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["messages"]

    def report_sent(self, messages):
        response = self.call("report", {"reports": [{"id": m["id"], "status": "sent"} for m in messages]})
        self.assertLess(response.status_code, 300, response.content)


class BulkOnThePlatformPhoneTests(PlatformCalls, BulkCase):
    def setUp(self):
        super().setUp()
        self.pair_platform()

    def test_a_batch_is_fed_and_sent_one_text_at_a_time_and_settles(self):
        self.line.delete()  # no phone of its own; still entitled (site_has_line is patched on)
        batch = self.start()
        sent = []
        with mock.patch.object(store_bulk, "in_window", return_value=True):
            for _ in NUMBERS:
                messages = self.poll_platform()
                self.assertEqual(len(messages), 1)  # one paced text at a time
                self.assertEqual(self.poll_platform(), [], "nothing more until it is reported")
                sent.append(messages[0]["to"])
                self.report_sent(messages)
            self.assertEqual(self.poll_platform(), [])
        self.assertEqual(sorted(sent), sorted(NUMBERS))
        batch.refresh_from_db()
        self.assertNotIn(batch.state, StoreBulkBatch.ACTIVE)
        self.assertEqual(self.records().count(), len(NUMBERS))
        self.assertEqual(set(self.records().values_list("device_id", flat=True)), {self.platform.pk})

    def test_a_store_with_its_own_phone_is_not_fed_or_claimed_by_the_platform_phone(self):
        self.start()
        with mock.patch.object(store_bulk, "in_window", return_value=True):
            self.assertEqual(self.poll_platform(), [])  # the platform phone feeds nothing for this store
            self.assertFalse(self.records().exists())
            self.feed()  # its own phone's feed queues the text
            self.assertEqual(self.records().get().status, "queued")
            self.assertEqual(self.poll_platform(), [])  # and the platform phone does not claim it
            self.assertEqual(self.records().get().status, "queued")
            self.assertEqual(store_bulk.feed_platform(self.platform), 0)

    def test_with_the_switch_off_the_platform_phone_claims_no_bulk(self):
        self.line.delete()
        self.start()
        with mock.patch.object(store_bulk, "in_window", return_value=True):
            self.feed()
        message = self.records().get()
        self.assertTrue(SmsMessage.objects.filter(sms.claimable(self.platform), pk=message.pk).exists())
        with self.settings(SMS_SITE_LINES_ENABLED=False):
            self.assertFalse(SmsMessage.objects.filter(sms.claimable(self.platform), pk=message.pk).exists())

    def test_a_store_with_a_phone_whose_line_is_off_has_its_bulk_kept_off_the_platform_phone(self):
        self.has_line.return_value = False  # own phone present, billing/HQ line off
        self.assertIsNone(sms.bulk_phone(self.site))
        message = SmsMessage.objects.create(
            recipient=NUMBERS[0], category="storebulk", purpose="store_bulk", site=self.site, priority=20,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.assertFalse(SmsMessage.objects.filter(sms.claimable(self.platform), pk=message.pk).exists())


class ScheduleOnThePlatformPhoneTests(PlatformCalls, ScheduleCase):
    def setUp(self):
        super().setUp()
        self.pair_platform()
        self.line.delete()

    def test_a_due_scheduled_batch_starts_and_sends_from_the_platform_phone(self):
        batch = self.make()
        self.assertEqual(batch.state, StoreBulkBatch.SCHEDULED)
        with mock.patch.object(store_bulk, "in_window", return_value=True), \
                mock.patch("apps.inquiries.store_bulk_schedule._now", return_value=AFTER), \
                mock.patch("django.utils.timezone.now", return_value=AFTER):
            first = self.poll_platform()
        batch.refresh_from_db()
        self.assertIn(batch.state, StoreBulkBatch.ACTIVE)
        self.assertEqual(len(first), 1)
        self.assertEqual(SmsMessage.objects.get(public_id=first[0]["id"]).device_id, self.platform.pk)
