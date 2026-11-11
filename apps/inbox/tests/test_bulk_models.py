"""Task 1 of plan 8: the switch, the records and the access column. Every number is made up."""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.inbox import switch
from apps.inbox.access import scope_for
from apps.inbox.models import BulkBatch, BulkRecipient, DoNotText, InboxAccess

NUMBER = "+639170000201"


class BulkSwitchTests(SimpleTestCase):
    def test_1_the_switch_is_off_unless_set(self):
        self.assertIs(switch.bulk_on(), False)
        with override_settings(SMS_BULK_ENABLED=True):
            self.assertIs(switch.bulk_on(), True)


class BulkRecordTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user("boss", password="pw", is_staff=True)
        self.staff = get_user_model().objects.create_user("ana", password="pw", is_staff=True)

    def batch(self, **fields):
        fields.setdefault("created_by", self.staff)
        fields.setdefault("request_id", "r" * 20)
        fields.setdefault("text", "Hello")
        fields.setdefault("source", "pasted")
        fields.setdefault("local_day", timezone.localdate())
        for name in ("lines_read", "duplicates", "refused", "opted_out", "size", "left_out"):
            fields.setdefault(name, 0)
        return BulkBatch.objects.create(**fields)

    def test_1_bulk_needs_send(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            InboxAccess.objects.create(
                operator=self.staff, platform="sms", can_send=False, can_bulk=True, granted_by=self.admin
            )
        InboxAccess.objects.create(
            operator=self.staff, platform="sms", can_send=True, can_bulk=True, granted_by=self.admin
        )

    def test_2_one_running_batch_at_a_time_in_the_database(self):
        self.batch()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.batch(request_id="s" * 20)

    def test_3_a_stopped_batch_frees_the_rule(self):
        first = self.batch()
        first.state = "stopped"
        first.save()
        self.batch(request_id="s" * 20)

    def test_4_one_request_id_per_user(self):
        self.batch(state="finished")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.batch(state="finished")

    def test_5_a_position_is_unique_per_batch(self):
        batch = self.batch()
        BulkRecipient.objects.create(batch=batch, position=0, mobile=NUMBER)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BulkRecipient.objects.create(batch=batch, position=0, mobile="+639170000202")

    def test_6_a_number_is_on_the_list_once(self):
        DoNotText.objects.create(platform="sms", mobile=NUMBER)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DoNotText.objects.create(platform="sms", mobile=NUMBER)

    def test_7_scope_for_answers_without_reading_can_bulk(self):
        for can_send in (False, True):
            InboxAccess.objects.all().delete()
            InboxAccess.objects.create(operator=self.staff, platform="sms", can_send=can_send, granted_by=self.admin)
            with override_settings(SMS_INBOX_ENABLED=True), CaptureQueriesContext(connection) as queries:
                scope = scope_for(self.staff, "sms")
            self.assertEqual(scope.can_send, can_send)
            self.assertFalse(any("can_bulk" in q["sql"] for q in queries.captured_queries))

    def test_8_tables_exist(self):
        self.assertTrue(switch.bulk_tables_exist())
        self.assertTrue(switch.dnt_tables_exist())
