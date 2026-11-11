"""The command that deletes one number's conversations: a person's deletion request."""
import io
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from apps.inbox import sending, storing
from apps.inbox.access import scope_for
from apps.inbox.models import Contact, ContactGroup, Conversation, ConversationMessage, InboxAccess
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.notifications import gateway, sms
from apps.notifications.models import SmsMessage
from apps.notifications.tests.base import MOBILE, online_phone
from apps.operators.models import OperatorAuditEntry
from apps.sites.tests.base import make_account, make_site

TYPED = "0918 999 0000"
OTHER = "+639171112222"
NONE_STORED = "Nothing is stored for that number."
NOT_A_NUMBER = "Type one full number."
TABLES_EXIST = "apps.inbox.management.commands.sms_inbox_delete.tables_exist"


class DeleteCase(InboxTestCase):
    def setUp(self):
        super().setUp()
        self.refs = 0

    def keep(self, sender=SENDER, texts=2, phone=None):
        """Store `texts` incoming texts from `sender`, the way the gateway does."""
        items = []
        for _ in range(texts):
            self.refs += 1
            items.append({"id": f"in_{self.refs}", "from": sender, "text": "Is my order ready?"})
        self.assertIs(storing.store(phone or self.phone, {"messages": items}), True)

    def run_command(self, typed, *args, actor="ana", confirm=False):
        """The command with `typed` on standard input. Returns (output, error output, input)."""
        out, err, stdin = io.StringIO(), io.StringIO(), io.StringIO(typed + "\n")
        options = {"confirm": confirm, "stdout": out, "stderr": err}
        if actor is not None:
            options["actor"] = actor
        with mock.patch("sys.stdin", stdin):
            call_command("sms_inbox_delete", *args, **options)
        return out.getvalue(), err.getvalue(), stdin

    def entries(self):
        return [
            (e.action, e.operator, e.target_type, e.target_id, e.detail, e.personal)
            for e in OperatorAuditEntry.objects.order_by("pk")
        ]

    def entry(self, change, conversations, messages, send_records=0, contacts=0):
        detail = {
            "platform": "sms", "change": change, "conversations": conversations,
            "messages": messages, "send_records": send_records, "contacts": contacts,
        }
        return ("sms_inbox_delete", self.staff, "conversation", "", detail, {})

    def stored(self):
        return Conversation.objects.count(), ConversationMessage.objects.count()

class DeleteCommandTests(DeleteCase):
    def test_1_without_confirm_it_only_counts(self):
        self.keep()
        out, _, _ = self.run_command(TYPED)
        self.assertEqual(
            out.strip(),
            "Found: 1 conversation, 2 messages, 0 send records, 0 contacts. Nothing was deleted. Run again with --confirm to delete them.",
        )
        self.assertEqual(self.stored(), (1, 2))
        self.assertEqual(self.entries(), [self.entry("count", 1, 2)])

    def test_2_with_confirm_it_deletes(self):
        self.keep()
        self.keep(OTHER, texts=3)
        out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 2 messages, 0 send records, 0 contacts.")
        self.assertEqual(list(Conversation.objects.values_list("contact", flat=True)), [OTHER])
        self.assertEqual(ConversationMessage.objects.filter(conversation__contact=OTHER).count(), 3)
        self.assertEqual(self.stored(), (1, 3))
        self.assertEqual(self.entries(), [self.entry("delete", 1, 2)])

    def test_3_every_form_of_a_philippine_mobile_matches(self):
        self.keep()
        for typed in ("09189990000", "639189990000", "+63 918 999 0000", "9189990000"):
            with self.subTest(typed=typed):
                self.assertIn("Found: 1 conversation, 2 messages, 0 send records, 0 contacts.", self.run_command(typed)[0])

    def test_4_both_phone_lines_are_covered(self):
        self.keep()
        self.keep(texts=1, phone=online_phone(name="second", forwards_incoming=True))
        out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 2 conversations, 3 messages, 0 send records, 0 contacts.")
        self.assertEqual(self.stored(), (0, 0))
        self.assertEqual(self.entries(), [self.entry("delete", 2, 3)])

    def test_5_a_number_that_is_not_a_philippine_mobile_matches_by_its_digits(self):
        self.keep("+14155550123", texts=1)
        self.keep("0281234567", texts=1)
        second = online_phone(name="second", forwards_incoming=True)
        self.keep("(02) 8123-4567", texts=1, phone=second)
        self.assertEqual(Conversation.objects.filter(can_reply=False).count(), 3)
        for typed in ("+14155550123", "+1 415 555 0123", "+1 (415) 555-0123", "1.415.555.0123"):
            with self.subTest(typed=typed):
                self.assertIn("Found: 1 conversation, 1 message, 0 send records, 0 contacts.", self.run_command(typed)[0])
        # The landline is stored once in each form: either form typed finds both.
        for typed in ("(02) 8123-4567", "0281234567"):
            with self.subTest(typed=typed):
                self.assertIn("Found: 2 conversations, 2 messages, 0 send records, 0 contacts.", self.run_command(typed)[0])
        OperatorAuditEntry.objects.all().delete()
        for typed in ("+14155550124", "4155550123"):
            with self.subTest(typed=typed):
                self.assertEqual(self.run_command(typed)[0].strip(), NONE_STORED)
        self.assertEqual(self.entries(), [self.entry("count", 0, 0)] * 2)
        out, _, _ = self.run_command("+1 (415) 555-0123", confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 1 message, 0 send records, 0 contacts.")
        self.assertEqual(
            sorted(Conversation.objects.values_list("contact", flat=True)), ["(02) 8123-4567", "0281234567"]
        )
        self.assertEqual(self.stored(), (2, 2))

    def test_5b_digits_never_match_a_name_or_a_philippine_mobile(self):
        self.keep("BDO1234567", texts=1)
        self.keep("GLOBE2024", texts=1)
        self.keep(texts=1)
        self.assertIs(Conversation.objects.get(contact="BDO1234567").can_reply, False)
        self.assertIn("Found: 1 conversation, 1 message, 0 send records, 0 contacts.", self.run_command("1234567")[0])
        with self.assertRaisesMessage(CommandError, NOT_A_NUMBER):
            self.run_command("2024", confirm=True)
        self.assertEqual(self.run_command("0002024", confirm=True)[0].strip(), NONE_STORED)
        self.assertEqual(self.run_command("+1 639 189 990 000", confirm=True)[0].strip(), NONE_STORED)
        self.assertEqual(self.stored(), (3, 3))

    def test_6_anything_that_is_not_a_full_number_is_refused(self):
        self.keep()
        for typed in ("GLOBE", "8080", "0918", "", "1" * 40):
            for confirm in (False, True):
                with self.subTest(typed=typed, confirm=confirm):
                    with self.assertRaisesMessage(CommandError, NOT_A_NUMBER):
                        self.run_command(typed, confirm=confirm)
        self.assertEqual(self.stored(), (1, 2))
        self.assertEqual(self.entries(), [])

    def test_7_the_number_is_nowhere(self):
        forms = (TYPED, SENDER, "09189990000", "9189990000", "918 999 0000", "0000")
        for confirm in (False, True):
            with self.subTest(confirm=confirm):
                self.keep()
                OperatorAuditEntry.objects.all().delete()
                with self.assertNoLogs(level="DEBUG"):
                    out, err, _ = self.run_command(TYPED, confirm=confirm)
                entry = OperatorAuditEntry.objects.get()
                recorded = json.dumps([entry.detail, entry.personal, entry.target_id])
                for text in (out, err, recorded):
                    for form in forms:
                        self.assertNotIn(form, text)
        with self.assertRaises(CommandError):
            self.run_command(TYPED, "--number", "x")

    def test_8_it_works_while_the_inbox_is_off(self):
        self.keep()
        with override_settings(SMS_INBOX_ENABLED=False):
            out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 2 messages, 0 send records, 0 contacts.")
        self.assertEqual(self.stored(), (0, 0))
        self.assertEqual(self.entries(), [self.entry("delete", 1, 2)])

    def test_9_with_no_table_it_refuses(self):
        out, err, stdin = io.StringIO(), io.StringIO(), io.StringIO(TYPED + "\n")
        with mock.patch(TABLES_EXIST, return_value=False), mock.patch("sys.stdin", stdin):
            with self.assertNumQueries(0):
                with self.assertRaisesMessage(
                    CommandError, "The inbox tables do not exist here, so no text is stored."
                ):
                    call_command("sms_inbox_delete", actor="ana", confirm=True, stdout=out, stderr=err)
        self.assertEqual(stdin.tell(), 0)

    def test_10_a_number_with_no_conversation(self):
        self.keep(OTHER)
        for confirm, change in ((False, "count"), (True, "delete")):
            with self.subTest(confirm=confirm):
                OperatorAuditEntry.objects.all().delete()
                self.assertEqual(self.run_command(TYPED, confirm=confirm)[0].strip(), NONE_STORED)
                self.assertEqual(self.entries(), [self.entry(change, 0, 0)])
        self.assertEqual(self.stored(), (1, 2))

    def test_11_the_actor_must_be_active_staff(self):
        users = get_user_model().objects
        users.create_user("visitor", password="pw")
        users.create_user("gone", password="pw", is_staff=True, is_active=False)
        self.keep()
        for name in (None, "missing", "visitor", "gone"):
            with self.subTest(name=name):
                out, err, stdin = io.StringIO(), io.StringIO(), io.StringIO(TYPED + "\n")
                options = {} if name is None else {"actor": name}
                with mock.patch("sys.stdin", stdin):
                    with self.assertRaisesMessage(CommandError, "--actor must be an active staff user."):
                        call_command("sms_inbox_delete", confirm=True, stdout=out, stderr=err, **options)
                self.assertEqual(stdin.tell(), 0)
        self.assertEqual(self.stored(), (1, 2))
        self.assertEqual(self.entries(), [])

    def test_12_a_conversation_owned_by_a_site_is_left(self):
        self.keep()
        theirs = Conversation.objects.create(
            platform="sms", line=self.phone, contact=SENDER, can_reply=True, last_message_at=timezone.now(),
            owner_kind="site", site=make_site(make_account()),
        )
        ConversationMessage.objects.create(conversation=theirs, platform="sms", direction="in", body="Hello")
        out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 2 messages, 0 send records, 0 contacts.")
        self.assertEqual(list(Conversation.objects.all()), [theirs])
        self.assertEqual(theirs.messages.count(), 1)
        self.assertEqual(self.entries(), [self.entry("delete", 1, 2)])

    def test_13_no_inbox_access_is_needed_or_given(self):
        self.keep()
        self.assertEqual(InboxAccess.objects.count(), 0)
        self.assertIn("Deleted: 1 conversation, 2 messages, 0 send records, 0 contacts.", self.run_command(TYPED, confirm=True)[0])
        self.assertEqual(InboxAccess.objects.count(), 0)


class DeleteSendRecordsTests(DeleteCase):
    """The send records of staff texts go with the conversation."""

    def setUp(self):
        super().setUp()
        InboxAccess.objects.create(operator=self.staff, platform="sms", can_send=True, granted_by=self.staff)
        self.scope = scope_for(self.staff, "sms")

    def text_to(self, number=SENDER, reply=True, request="request-id-00000%02d"):
        self.refs += 1
        key = request % self.refs
        if reply:
            conversation = Conversation.objects.get(contact=number)
            return sending.reply(self.scope, conversation.public_id, "Yes, we are open.", key)
        return sending.compose(self.scope, number, "Yes, we are open.", key)

    def test_14_the_send_records_of_staff_texts_go_with_the_conversation(self):
        self.keep()
        self.text_to()
        self.text_to(reply=False)
        verify = SmsMessage.objects.create(
            recipient=SENDER, category="code", purpose="mobile_verify", priority=10,
            expires_at=timezone.now(),
        )
        elsewhere = SmsMessage.objects.create(
            recipient="+639170000099", category="inbox", purpose="inbox_reply", priority=20,
            expires_at=timezone.now(),
        )
        waiting = list(SmsMessage.objects.filter(purpose__startswith="inbox_", recipient=SENDER))
        self.assertEqual(len(waiting), 2)
        OperatorAuditEntry.objects.all().delete()

        out, _, _ = self.run_command(TYPED)
        self.assertEqual(
            out.strip(),
            "Found: 1 conversation, 4 messages, 2 send records, 0 contacts. Nothing was deleted. "
            "Run again with --confirm to delete them.",
        )
        self.assertEqual(SmsMessage.objects.count(), 4)
        out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 4 messages, 2 send records, 0 contacts.")
        self.assertEqual(self.stored(), (0, 0))
        self.assertEqual(
            sorted(SmsMessage.objects.values_list("pk", flat=True)), sorted([verify.pk, elsewhere.pk])
        )
        for record in waiting:
            self.assertIsNone(sms.take_text(record))
        self.assertEqual(
            self.entries(), [self.entry("count", 1, 4, 2), self.entry("delete", 1, 4, 2)]
        )

    def test_15_a_text_with_the_phone_stops_the_command(self):
        self.keep()
        self.text_to()
        record = SmsMessage.objects.get(purpose="inbox_reply")
        SmsMessage.objects.filter(pk=record.pk).update(status="dispatched")
        OperatorAuditEntry.objects.all().delete()
        words = "A text to that number is with the phone right now. Run again in 10 minutes."
        for confirm in (False, True):
            with self.subTest(confirm=confirm), self.assertRaisesMessage(CommandError, words):
                self.run_command(TYPED, confirm=confirm)
        self.assertEqual(self.stored(), (1, 3))
        self.assertEqual(SmsMessage.objects.count(), 1)
        self.assertEqual(self.entries(), [])
        SmsMessage.objects.filter(pk=record.pk).update(status="sent")
        self.assertEqual(
            self.run_command(TYPED, confirm=True)[0].strip(), "Deleted: 1 conversation, 3 messages, 1 send record, 0 contacts."
        )

    def test_16_a_waiting_text_is_never_sent_after_the_delete(self):
        self.keep()
        self.text_to()
        self.run_command(TYPED, confirm=True)
        self.assertEqual(gateway._hand_out(self.phone, timezone.now()), [])

    def test_17_a_number_that_is_not_a_philippine_mobile_reads_no_send_record(self):
        self.keep("+14155550123", texts=1)
        with CaptureQueriesContext(connection) as queries:
            out, _, _ = self.run_command("+14155550123")
        self.assertIn("Found: 1 conversation, 1 message, 0 send records, 0 contacts.", out)
        self.assertEqual([q["sql"] for q in queries if "notifications_smsmessage" in q["sql"]], [])


CONTACT_TABLES = "apps.inbox.management.commands.sms_inbox_delete.contacts_tables_exist"


class DeleteContactTests(DeleteCase):
    """The contact held under the number goes with it; its groups stay."""

    def setUp(self):
        super().setUp()
        self.group = ContactGroup.objects.create(owner_kind="hq", name="Regulars")
        self.contact = self.make("Ana", "+639189990000")
        self.contact.groups.add(self.group)
        self.other = self.make("Ben", OTHER)

    def make(self, name, mobile):
        return Contact.objects.create(
            owner_kind="hq", first_name=name, last_name="Sample", mobile=mobile, created_by=self.staff
        )

    def test_18_without_confirm_a_contact_is_counted_and_kept(self):
        out, _, _ = self.run_command(TYPED)
        self.assertEqual(
            out.strip(), "Found: 0 conversations, 0 messages, 0 send records, 1 contact. "
            "Nothing was deleted. Run again with --confirm to delete them.",
        )
        self.assertEqual(Contact.objects.count(), 2)
        self.assertEqual(self.entries(), [self.entry("count", 0, 0, contacts=1)])

    def test_19_with_confirm_it_is_deleted_and_the_group_stays(self):
        out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 0 conversations, 0 messages, 0 send records, 1 contact.")
        self.assertEqual(list(Contact.objects.all()), [self.other])
        self.assertEqual(list(ContactGroup.objects.all()), [self.group])
        entry = OperatorAuditEntry.objects.get()
        self.assertEqual(entry.detail["contacts"], 1)
        text = json.dumps([entry.detail, entry.personal, entry.target_id])
        for value in ("Ana", "Sample", "9189990000", "918 999"):
            self.assertNotIn(value, text)

    def test_20_every_form_of_the_number_finds_it(self):
        for typed in ("09189990000", "639189990000", "+63 918 999 0000", "9189990000"):
            with self.subTest(typed=typed):
                self.assertIn("1 contact.", self.run_command(typed)[0])

    def test_21_conversations_and_contact_go_together(self):
        self.keep()
        out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 2 messages, 0 send records, 1 contact.")
        self.assertEqual(self.stored(), (0, 0))
        self.assertEqual(Contact.objects.count(), 1)

    def test_22_another_number_is_left(self):
        self.run_command("+14155550123", confirm=True)
        self.run_command("0917 111 2223", confirm=True)
        self.assertEqual(Contact.objects.count(), 2)

    def test_23_without_the_contact_tables_it_still_works(self):
        self.keep()
        with mock.patch(CONTACT_TABLES, return_value=False):
            out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 2 messages, 0 send records.")
        self.assertEqual(Contact.objects.count(), 2)
        self.assertNotIn("contacts", OperatorAuditEntry.objects.get().detail)

    def test_24_with_contacts_off_the_contact_is_still_deleted(self):
        with override_settings(SMS_CONTACTS_ENABLED=False):
            out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertIn("1 contact.", out)
        self.assertEqual(Contact.objects.count(), 1)

    def test_25_nothing_stored_says_so(self):
        out, _, _ = self.run_command("0917 111 3333", confirm=True)
        self.assertEqual(out.strip(), NONE_STORED)
