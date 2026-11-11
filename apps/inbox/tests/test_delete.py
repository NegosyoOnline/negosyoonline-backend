"""The command that deletes one number's conversations: a person's deletion request."""
import io
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.inbox import storing
from apps.inbox.models import Conversation, ConversationMessage, InboxAccess
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.notifications.tests.base import online_phone
from apps.operators.models import OperatorAuditEntry
from apps.sites.tests.base import make_account, make_site

TYPED = "0918 999 0000"
OTHER = "+639171112222"
NONE_STORED = "No conversation is stored for that number."
NOT_A_NUMBER = "Type one full number."
TABLES_EXIST = "apps.inbox.management.commands.sms_inbox_delete.tables_exist"


class DeleteCommandTests(InboxTestCase):
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

    def entry(self, change, conversations, messages):
        detail = {"platform": "sms", "change": change, "conversations": conversations, "messages": messages}
        return ("sms_inbox_delete", self.staff, "conversation", "", detail, {})

    def stored(self):
        return Conversation.objects.count(), ConversationMessage.objects.count()

    def test_1_without_confirm_it_only_counts(self):
        self.keep()
        out, _, _ = self.run_command(TYPED)
        self.assertEqual(
            out.strip(),
            "Found: 1 conversation, 2 messages. Nothing was deleted. Run again with --confirm to delete them.",
        )
        self.assertEqual(self.stored(), (1, 2))
        self.assertEqual(self.entries(), [self.entry("count", 1, 2)])

    def test_2_with_confirm_it_deletes(self):
        self.keep()
        self.keep(OTHER, texts=3)
        out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 2 messages.")
        self.assertEqual(list(Conversation.objects.values_list("contact", flat=True)), [OTHER])
        self.assertEqual(ConversationMessage.objects.filter(conversation__contact=OTHER).count(), 3)
        self.assertEqual(self.stored(), (1, 3))
        self.assertEqual(self.entries(), [self.entry("delete", 1, 2)])

    def test_3_every_form_of_a_philippine_mobile_matches(self):
        self.keep()
        for typed in ("09189990000", "639189990000", "+63 918 999 0000", "9189990000"):
            with self.subTest(typed=typed):
                self.assertIn("Found: 1 conversation, 2 messages.", self.run_command(typed)[0])

    def test_4_both_phone_lines_are_covered(self):
        self.keep()
        self.keep(texts=1, phone=online_phone(name="second", forwards_incoming=True))
        out, _, _ = self.run_command(TYPED, confirm=True)
        self.assertEqual(out.strip(), "Deleted: 2 conversations, 3 messages.")
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
                self.assertIn("Found: 1 conversation, 1 message.", self.run_command(typed)[0])
        # The landline is stored once in each form: either form typed finds both.
        for typed in ("(02) 8123-4567", "0281234567"):
            with self.subTest(typed=typed):
                self.assertIn("Found: 2 conversations, 2 messages.", self.run_command(typed)[0])
        OperatorAuditEntry.objects.all().delete()
        for typed in ("+14155550124", "4155550123"):
            with self.subTest(typed=typed):
                self.assertEqual(self.run_command(typed)[0].strip(), NONE_STORED)
        self.assertEqual(self.entries(), [self.entry("count", 0, 0)] * 2)
        out, _, _ = self.run_command("+1 (415) 555-0123", confirm=True)
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 1 message.")
        self.assertEqual(
            sorted(Conversation.objects.values_list("contact", flat=True)), ["(02) 8123-4567", "0281234567"]
        )
        self.assertEqual(self.stored(), (2, 2))

    def test_5b_digits_never_match_a_name_or_a_philippine_mobile(self):
        self.keep("BDO1234567", texts=1)
        self.keep("GLOBE2024", texts=1)
        self.keep(texts=1)
        self.assertIs(Conversation.objects.get(contact="BDO1234567").can_reply, False)
        self.assertIn("Found: 1 conversation, 1 message.", self.run_command("1234567")[0])
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
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 2 messages.")
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
        self.assertEqual(out.strip(), "Deleted: 1 conversation, 2 messages.")
        self.assertEqual(list(Conversation.objects.all()), [theirs])
        self.assertEqual(theirs.messages.count(), 1)
        self.assertEqual(self.entries(), [self.entry("delete", 1, 2)])

    def test_13_no_inbox_access_is_needed_or_given(self):
        self.keep()
        self.assertEqual(InboxAccess.objects.count(), 0)
        self.assertIn("Deleted: 1 conversation, 2 messages.", self.run_command(TYPED, confirm=True)[0])
        self.assertEqual(InboxAccess.objects.count(), 0)
