"""Task 8 of plan 8: opted_out, the access command, retention and deletion. Made-up numbers."""
import io
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.common import retention as common_retention
from apps.inbox import contacts, reading, storing
from apps.inbox.models import BulkBatch, BulkRecipient, DoNotText, InboxAccess
from apps.inbox.retention import bulk_batches
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.inbox.tests.bulk_base import BulkCase, mobile
from apps.notifications.models import SmsMessage
from apps.operators.models import OperatorAuditEntry


class OptedOutTests(BulkCase):
    def contact_scope(self):
        scope = contacts.contact_scope(self.staff)
        self.assertIsNotNone(scope)
        return scope

    def test_1_a_listed_contact_is_flagged_in_a_list_and_in_open(self):
        listed, other = self.make_group("G", mobile(1), mobile(2)).contacts.order_by("pk")
        DoNotText.objects.create(platform="sms", mobile=listed.mobile)
        rows, _ = contacts.list_contacts(self.contact_scope())
        self.assertEqual({r["id"]: r["opted_out"] for r in rows}, {listed.public_id: True, other.public_id: False})
        opened = contacts.open_contact(self.contact_scope(), listed.public_id)
        self.assertTrue(opened["opted_out"])

    def test_2_without_the_table_nothing_is_read_and_nothing_is_flagged(self):
        (contact,) = self.make_group("G", mobile(1)).contacts.all()
        with mock.patch.object(contacts, "dnt_tables_exist", return_value=False):
            rows, _ = contacts.list_contacts(self.contact_scope())
        self.assertFalse(rows[0]["opted_out"])

    def test_3_an_upload_check_counts_the_listed(self):
        DoNotText.objects.create(platform="sms", mobile=mobile(1))
        group = self.make_group("G")
        answer = contacts.check_upload(self.contact_scope(), f"a,b,{mobile(1)}\nc,d,{mobile(2)}\n", group.public_id)
        self.assertEqual((answer["new"], answer["already_opted_out"]), (2, 1))

    def test_4_a_conversation_answer_says_so_and_a_reply_is_still_allowed(self):
        storing.store(self.phone, {"messages": [{"id": "in_1", "from": SENDER, "text": "STOP"}]})
        from apps.inbox.access import scope_for
        scope = scope_for(self.staff, "sms")
        conversation = scope.conversations.get()
        self.assertTrue(reading.open_conversation(scope, conversation.public_id)["opted_out"])
        other = {"id": "in_2", "from": "+639189990001", "text": "hello"}
        storing.store(self.phone, {"messages": [other]})
        second = scope.conversations.exclude(pk=conversation.pk).get()
        self.assertFalse(reading.open_conversation(scope, second.public_id)["opted_out"])


class AccessCommandTests(InboxTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        self.boss = get_user_model().objects.create_user("boss", password="pw", is_staff=True)

    def run_command(self, *args):
        out = io.StringIO()
        call_command("sms_inbox_access", *args, stdout=out)
        return out.getvalue()

    def row(self):
        return InboxAccess.objects.get(operator=self.staff)

    def test_1_bulk_needs_send(self):
        with self.assertRaises(CommandError):
            self.run_command("grant", "--user", "ana", "--bulk", "--actor", "boss")
        with self.assertRaises(CommandError):
            self.run_command("revoke", "--user", "ana", "--bulk", "--actor", "boss")

    def test_2_grant_bulk_is_audited_and_listed(self):
        self.assertIn("Granted: read, send and bulk.", self.run_command("grant", "--user", "ana", "--send", "--bulk", "--actor", "boss"))
        self.assertEqual((self.row().can_send, self.row().can_bulk), (True, True))
        self.assertEqual(OperatorAuditEntry.objects.get(action="sms_inbox_access").detail["level"], "bulk")
        self.assertIn("read, send and bulk", self.run_command("list"))
        self.assertIn("Already granted", self.run_command("grant", "--user", "ana", "--send", "--bulk", "--actor", "boss"))

    def test_3_grant_send_without_bulk_removes_bulk_and_says_so(self):
        self.run_command("grant", "--user", "ana", "--send", "--bulk", "--actor", "boss")
        out = self.run_command("grant", "--user", "ana", "--send", "--actor", "boss")
        self.assertIn("Bulk SMS access was removed", out)
        self.assertEqual((self.row().can_send, self.row().can_bulk), (True, False))

    def test_4_revoke_removes_the_row(self):
        self.run_command("grant", "--user", "ana", "--send", "--bulk", "--actor", "boss")
        self.run_command("revoke", "--user", "ana", "--actor", "boss")
        self.assertEqual(InboxAccess.objects.count(), 0)


class RetentionAndDeleteTests(BulkCase):
    def old_batch(self, days, **fields):
        batch = BulkBatch.objects.create(
            created_by=self.staff, request_id=f"request-id-old-{days:05d}", text="hi", source="pasted",
            local_day=timezone.localdate(), lines_read=1, duplicates=0, refused=0, opted_out=0, size=1,
            left_out=0, state="finished", **fields,
        )
        BulkBatch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - timedelta(days=days))
        return batch

    def test_1_registered_and_deletes_old_batches_with_their_rows_only(self):
        self.assertIn("bulk_batches", common_retention.names())
        old, young = self.old_batch(366), self.old_batch(364)
        BulkRecipient.objects.create(batch=old, position=0, mobile=mobile(1))
        DoNotText.objects.create(platform="sms", mobile=mobile(9))
        DoNotText.objects.update(created_at=timezone.now() - timedelta(days=2000))
        self.assertEqual(bulk_batches(timezone.now(), True), 1)
        self.assertEqual(BulkBatch.objects.count(), 2)
        self.assertEqual(bulk_batches(timezone.now(), False), 1)
        self.assertEqual(list(BulkBatch.objects.all()), [young])
        self.assertEqual((BulkRecipient.objects.count(), DoNotText.objects.count()), (0, 1))

    def test_2_without_the_table_it_does_nothing(self):
        with mock.patch("apps.inbox.retention.bulk_tables_exist", return_value=False):
            self.assertEqual(bulk_batches(timezone.now(), False), 0)

    def run_delete(self, typed, *, confirm=False):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(typed + "\n")):
            call_command("sms_inbox_delete", actor="ana", confirm=confirm, stdout=out, stderr=io.StringIO())
        return out.getvalue()

    def test_3_the_delete_command_removes_a_waiting_recipient_and_its_queued_record(self):
        self.start(2, mobile(1), mobile(2))
        from apps.inbox import bulk
        bulk.feed(timezone.now())  # a bulk send record is queued for mobile(1)
        self.assertEqual(SmsMessage.objects.filter(purpose="inbox_bulk").count(), 1)
        DoNotText.objects.create(platform="sms", mobile=mobile(1))
        counted = self.run_delete(mobile(1))
        self.assertIn("1 send record", counted)
        self.assertIn("1 bulk recipient", counted)
        self.assertEqual(BulkRecipient.objects.count(), 2)
        deleted = self.run_delete(mobile(1), confirm=True)
        self.assertIn("Deleted", deleted)
        self.assertEqual(list(BulkRecipient.objects.values_list("mobile", flat=True)), [mobile(2)])
        self.assertEqual(SmsMessage.objects.filter(purpose="inbox_bulk").count(), 0)
        self.assertEqual(DoNotText.objects.count(), 1)  # the list is never touched
        entry = OperatorAuditEntry.objects.filter(action="sms_inbox_delete").latest("pk")
        self.assertEqual(entry.detail["bulk_recipients"], 1)
        self.assertNotIn(mobile(1), counted + deleted)

    def test_4_it_works_with_the_bulk_switch_off_and_without_the_tables(self):
        from django.test import override_settings
        with override_settings(SMS_BULK_ENABLED=False):
            self.assertIn("Nothing is stored", self.run_delete(mobile(1)))
        with mock.patch("apps.inbox.management.commands.sms_inbox_delete.bulk_tables_exist", return_value=False):
            out = self.run_delete(mobile(1))
        self.assertIn("Nothing is stored", out)
        self.assertNotIn("bulk", out)
