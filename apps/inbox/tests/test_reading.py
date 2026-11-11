"""What a screen may be shown: masked, the list without a text, opening and finding audited."""
import json

from django.contrib import admin
from django.utils import timezone

from apps.inbox import reading, storing
from apps.inbox.access import scope_for
from apps.inbox.models import Conversation, InboxAccess
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.operators.models import OperatorAuditEntry
from apps.sites.tests.base import make_account, make_site

MASKED = "+63918***0000"
FOREIGN = "+14155550123"


class ReadingTests(InboxTestCase):
    def setUp(self):
        super().setUp()
        InboxAccess.objects.create(operator=self.staff, platform="sms", granted_by=self.staff)
        self.scope = scope_for(self.staff, "sms")
        self.count = 0

    def receive(self, sender=SENDER, body="Is my order ready?"):
        self.count += 1
        item = {"id": f"in_{self.count}", "from": sender, "text": body}
        self.assertTrue(storing.store(self.phone, {"messages": [item]}))
        return Conversation.objects.get(messages__gateway_ref=item["id"])

    def entries(self, action):
        return list(OperatorAuditEntry.objects.filter(action=action).order_by("pk"))

    def test_1_the_list_is_masked_and_holds_no_text(self):
        first = self.receive()
        self.receive(body="Hello again")
        second = self.receive(sender="+639170000001", body="Second sender")
        rows = reading.conversations(self.scope)
        self.assertEqual([row["id"] for row in rows], [second.public_id, first.public_id])
        self.assertEqual(
            set(rows[1]), {"id", "contact", "can_reply", "unread", "last_message_at", "last_direction"}
        )
        self.assertEqual(
            (rows[1]["contact"], rows[1]["can_reply"], rows[1]["unread"], rows[1]["last_direction"]),
            (MASKED, True, 2, "in"),
        )
        answer = json.dumps(rows)
        for word in (SENDER, "9189990000", "order", "Hello", "Second"):
            self.assertNotIn(word, answer)

    def test_2_a_name_and_a_short_code_are_shown_as_received(self):
        for sender in ("GLOBE", "8080", "225687"):
            self.receive(sender=sender)
        self.assertEqual(
            sorted(row["contact"] for row in reading.conversations(self.scope)), ["225687", "8080", "GLOBE"]
        )

    def test_2b_a_number_that_cannot_be_replied_to_is_masked_too(self):
        shown = {FOREIGN: "***0123", "0281234567": "***4567", "8123456": "***", "+1 (415) 555-0123": "***0123"}
        ids = {self.receive(sender=sender).public_id: masked for sender, masked in shown.items()}
        rows = reading.conversations(self.scope)
        self.assertEqual({row["id"]: row["contact"] for row in rows}, ids)
        self.assertEqual({row["can_reply"] for row in rows}, {False})
        answer = json.dumps(rows)
        for sender in shown:
            self.assertNotIn(sender, answer)

    def test_3_the_list_writes_no_audit_entry(self):
        self.receive()
        reading.conversations(self.scope)
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)

    def test_4_open_gives_the_texts_masks_the_number_and_is_audited(self):
        conversation = self.receive()
        self.receive(body="Hello again")
        answer = reading.open_conversation(self.scope, conversation.public_id)
        self.assertEqual(set(answer), {"id", "contact", "can_reply", "messages"})
        self.assertEqual((answer["id"], answer["contact"], answer["can_reply"]), (conversation.public_id, MASKED, True))
        self.assertEqual([m["body"] for m in answer["messages"]], ["Is my order ready?", "Hello again"])
        for message in answer["messages"]:
            self.assertEqual(set(message), {"id", "direction", "body", "clipped", "at"})
        self.assertNotIn(SENDER, json.dumps(answer))
        entry, = self.entries("sms_inbox_open")
        self.assertEqual((entry.operator, entry.target_type, entry.target_id), (self.staff, "conversation", conversation.public_id))
        self.assertEqual(entry.detail, {"platform": "sms", "owner_kind": "hq", "messages": 2})
        self.assertEqual(entry.personal, {"contact": MASKED})
        recorded = json.dumps([entry.detail, entry.personal])
        for word in (SENDER, "order", "Hello"):
            self.assertNotIn(word, recorded)

    def test_4b_open_masks_a_foreign_number_in_the_answer_and_in_the_entry(self):
        conversation = self.receive(sender=FOREIGN)
        answer = reading.open_conversation(self.scope, conversation.public_id)
        self.assertEqual(answer["contact"], "***0123")
        entry, = self.entries("sms_inbox_open")
        self.assertEqual(entry.personal, {"contact": "***0123"})
        for held in (json.dumps(answer), json.dumps([entry.detail, entry.personal])):
            self.assertNotIn(FOREIGN, held)
            self.assertNotIn("4155550123", held)

    def test_5_open_clears_the_unread_count(self):
        conversation = self.receive()
        reading.open_conversation(self.scope, conversation.public_id)
        conversation.refresh_from_db()
        self.assertEqual(conversation.unread_count, 0)
        self.receive()
        conversation.refresh_from_db()
        self.assertEqual(conversation.unread_count, 1)

    def test_6_each_open_is_one_entry(self):
        conversation = self.receive()
        for _ in range(2):
            reading.open_conversation(self.scope, conversation.public_id)
        self.assertEqual(len(self.entries("sms_inbox_open")), 2)

    def test_7_an_id_outside_the_scope_is_not_found(self):
        mine = self.receive()
        theirs = Conversation.objects.create(
            owner_kind="site", site=make_site(make_account()), platform="sms", line=self.phone,
            contact=SENDER, last_message_at=timezone.now(), unread_count=3,
        )
        for public_id in ("conv_" + "0" * 20, "not an id", "", None, mine.pk, theirs.public_id):
            with self.subTest(public_id=public_id):
                self.assertIsNone(reading.open_conversation(self.scope, public_id))
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)
        theirs.refresh_from_db()
        mine.refresh_from_db()
        self.assertEqual((theirs.unread_count, mine.unread_count), (3, 1))

    def test_8_find_matches_a_full_number_and_is_audited(self):
        conversation = self.receive()
        self.receive(sender="+639170000001")
        with self.assertNoLogs(level="DEBUG"):
            rows = reading.find(self.scope, "0918 999 0000")
        self.assertEqual([(row["id"], row["contact"]) for row in rows], [(conversation.public_id, MASKED)])
        entry, = self.entries("sms_inbox_find")
        self.assertEqual((entry.operator, entry.target_type, entry.target_id), (self.staff, "", ""))
        self.assertEqual((entry.detail, entry.personal), ({"platform": "sms", "matches": 1}, {}))
        self.assertNotRegex(json.dumps([entry.detail, entry.personal, entry.reason]), r"\d{4}")

    def test_8b_a_lookup_with_no_match_is_audited_and_a_fragment_is_not(self):
        self.receive()
        self.receive(sender=FOREIGN)
        self.receive(sender="GLOBE")
        for typed in ("0918", "9990000", "", "GLOBE", None, FOREIGN):
            with self.subTest(typed=typed):
                self.assertEqual(reading.find(self.scope, typed), [])
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)
        self.assertEqual(reading.find(self.scope, "+639170000009"), [])
        entry, = self.entries("sms_inbox_find")
        self.assertEqual(entry.detail, {"platform": "sms", "matches": 0})

    def test_9_no_django_admin_screen(self):
        registered = [m._meta.label for m in admin.site._registry if m._meta.app_label == "inbox"]
        self.assertEqual(registered, [])
