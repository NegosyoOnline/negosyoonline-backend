"""STORE-INBOX-CONTACT-NAME (z8uq9mknf5): a store's text conversation is named by the member, else the
store's own saved contact, else "Customer", set on creation and resolved at read time."""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.inbox.models import Contact
from apps.inquiries import sms_inbox
from apps.inquiries.models import InquiryConversation
from apps.inquiries.tests.test_sms_inbox import MOBILE, OTHER_MOBILE, SmsOwnerCase, make_member

LIA = "+639170000301"
THIRD = "+639170000302"


class ContactNameTests(SmsOwnerCase):
    def setUp(self):
        super().setUp()
        self.member.delete()

    def contact(self, site, mobile, first="Lia", last="Sample", kind="site"):
        if kind == "hq":
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.create(username=f"staff{mobile[-4:]}")
            return Contact.objects.create(owner_kind="hq", mobile=mobile, first_name=first, last_name=last, created_by=user)
        owner = site.account
        return Contact.objects.create(owner_kind="site", site=site, mobile=mobile, first_name=first, last_name=last,
                                      created_by_account=owner)

    def incoming(self, mobile=LIA, site=None):
        sms_inbox.store_incoming(site or self.site, self.phone, mobile, f"r{mobile[-3:]}", "hello", timezone.now(), False)
        return InquiryConversation.objects.get(site=site or self.site, provider="sms", contact_mobile=mobile)

    def items(self, q=""):
        url = self.base + "conversations/" + (f"?q={q}" if q else "")
        return [i for i in self.get(url).json()["items"] if i["channel"] == "sms"]

    def unnamed(self, mobile=LIA):
        conversation = self.incoming(mobile)
        InquiryConversation.objects.filter(pk=conversation.pk).update(display_name="", contact_name="")
        return conversation

    def test_an_incoming_text_is_named_from_the_stores_contact_when_created(self):
        self.contact(self.site, LIA)
        conversation = self.incoming()
        self.assertEqual((conversation.display_name, conversation.contact_name), ("Lia Sample", "Lia Sample"))

    def test_a_member_name_wins_over_the_contact(self):
        make_member(self.site, LIA, name="Maria Member")
        self.contact(self.site, LIA)
        self.assertEqual(self.incoming().contact_name, "Maria Member")

    def test_neither_gives_customer(self):
        self.incoming()
        self.assertEqual(self.items()[0]["display_name"], "Customer")

    def test_first_name_only_contact_is_trimmed(self):
        self.contact(self.site, LIA, first="Jose", last="")
        self.assertEqual(self.incoming().contact_name, "Jose")

    def test_an_unnamed_conversation_is_named_at_read_time_in_list_and_thread_without_a_write(self):
        conversation = self.unnamed()
        self.contact(self.site, LIA)
        self.assertEqual(self.items()[0]["display_name"], "Lia Sample")
        self.assertEqual(self.thread(conversation.external_id).json()["display_name"], "Lia Sample")
        conversation.refresh_from_db()
        self.assertEqual((conversation.display_name, conversation.contact_name), ("", ""))

    def test_read_time_follows_a_renamed_contact_and_a_later_member_but_a_stored_name_stays(self):
        unnamed = self.unnamed()
        contact = self.contact(self.site, LIA)
        Contact.objects.filter(pk=contact.pk).update(first_name="Liza")
        self.assertEqual(self.items()[0]["display_name"], "Liza Sample")
        make_member(self.site, LIA, name="Maria Member")
        self.assertEqual(self.items()[0]["display_name"], "Maria Member")
        InquiryConversation.objects.filter(pk=unnamed.pk).update(contact_name="Kept Name")
        self.assertEqual(self.items()[0]["display_name"], "Kept Name")

    def test_never_an_hq_contact_or_another_stores_contact(self):
        self.contact(self.site, LIA, kind="hq", first="HQ", last="Person")
        self.contact(self.ben_site, LIA, first="Ben", last="Contact")
        conversation = self.unnamed()
        self.assertEqual(self.items()[0]["display_name"], "Customer")
        self.assertEqual(self.thread(conversation.external_id).json()["display_name"], "Customer")
        self.assertEqual(self.items("HQ")[:], [])
        self.assertEqual(self.items("Ben Contact"), [])
        self.assertEqual(self.incoming(THIRD).contact_name, "")

    def test_search_by_name_finds_a_thread_named_only_by_a_contact(self):
        self.unnamed()
        self.contact(self.site, LIA)
        self.assertEqual([i["display_name"] for i in self.items("Sample")], ["Lia Sample"])
        self.assertEqual([i["display_name"] for i in self.items("lia sam")], ["Lia Sample"])
        self.assertEqual(self.items("Nobody"), [])

    def test_list_names_cost_a_fixed_number_of_queries(self):
        for n in range(3):
            self.contact(self.site, f"+63917000040{n}", first=f"Pat{n}")
            self.unnamed(f"+63917000040{n}")
        with CaptureQueriesContext(connection) as few:
            self.items()
        for n in range(3, 8):
            self.contact(self.site, f"+63917000040{n}", first=f"Pat{n}")
            self.unnamed(f"+63917000040{n}")
        with CaptureQueriesContext(connection) as many:
            names = [i["display_name"] for i in self.items()]
        self.assertIn("Pat7 Sample", names)
        # per-row snippet queries exist already; the name lookup must add none per row
        self.assertLessEqual(len(many) - len(few), 5)

    def test_the_full_number_never_reaches_the_json(self):
        conversation = self.unnamed()
        self.contact(self.site, LIA)
        body = self.thread(conversation.external_id)
        self.assertNotIn(LIA, body.content.decode())
        self.assertNotIn(LIA, self.get(self.base + "conversations/").content.decode())
        self.assertEqual(body.json()["contact_mobile"], sms_inbox.mask_mobile(LIA))
