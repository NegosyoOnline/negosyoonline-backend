from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.common.ids import is_public_id
from apps.inbox import switch
from apps.inbox.models import Contact, ContactGroup, Conversation, ConversationMessage, InboxAccess
from apps.notifications.tests.base import online_phone
from apps.sites.tests.base import make_account, make_site


class SwitchTests(SimpleTestCase):
    def test_1_the_switch_is_off_unless_set(self):
        self.assertIs(switch.inbox_on(), False)
        with override_settings(SMS_INBOX_ENABLED=True):
            self.assertIs(switch.inbox_on(), True)

    def test_2_the_app_is_installed(self):
        self.assertTrue(apps.is_installed("apps.inbox"))

    def test_3_the_django_admin_is_not_installed(self):
        self.assertFalse(apps.is_installed("django.contrib.admin"))


class RecordTests(TestCase):
    def setUp(self):
        self.phone = online_phone()
        self.staff = get_user_model().objects.create_user("ana", password="pw", is_staff=True)

    def conversation(self, **fields):
        fields.setdefault("owner_kind", "hq")
        fields.setdefault("platform", "sms")
        fields.setdefault("line", self.phone)
        fields.setdefault("contact", "+639189990000")
        fields.setdefault("last_message_at", timezone.now())
        return Conversation.objects.create(**fields)

    def message(self, conversation, **fields):
        fields.setdefault("platform", "sms")
        fields.setdefault("direction", "in")
        fields.setdefault("body", "hello")
        return ConversationMessage.objects.create(conversation=conversation, **fields)

    def refused(self, make, **fields):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make(**fields)

    def test_4_an_hq_conversation_has_no_site_and_no_account(self):
        site = make_site(make_account())
        self.refused(self.conversation, owner_kind="hq", site=site)
        self.refused(self.conversation, owner_kind="site")
        self.refused(self.conversation, owner_kind="account", site=site)

    def test_5_one_hq_conversation_per_platform_line_and_contact(self):
        self.conversation()
        self.refused(self.conversation)
        self.conversation(line=online_phone("second"))

    def test_6_only_sms_is_a_platform(self):
        self.refused(self.conversation, platform="email")
        conversation = self.conversation()
        self.refused(self.message, conversation=conversation, platform="email")

    def test_7_a_phone_stores_an_incoming_id_once(self):
        conversation = self.conversation()
        self.message(conversation, device=self.phone, gateway_ref="in_1")
        self.refused(self.message, conversation=conversation, device=self.phone, gateway_ref="in_1")
        self.message(conversation, device=online_phone("second"), gateway_ref="in_1")
        self.message(conversation, direction="out")
        self.message(conversation, direction="out")

    def test_8_one_access_row_per_staff_user_and_platform(self):
        InboxAccess.objects.create(operator=self.staff, platform="sms", granted_by=self.staff)
        with self.assertRaises(IntegrityError), transaction.atomic():
            InboxAccess.objects.create(operator=self.staff, platform="sms", granted_by=self.staff)

    def test_9_public_ids_carry_their_prefix(self):
        conversation = self.conversation()
        self.assertTrue(is_public_id(conversation.public_id, "conv"))
        self.assertTrue(is_public_id(self.message(conversation).public_id, "msg"))

    def test_10_the_switch_knows_the_message_table(self):
        self.assertEqual(switch.MESSAGE_TABLE, ConversationMessage._meta.db_table)
        self.assertIs(switch.tables_exist(), True)


class ContactRecordTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("ana", password="pw", is_staff=True)

    def contact(self, **fields):
        fields.setdefault("owner_kind", "hq")
        fields.setdefault("first_name", "Ana")
        fields.setdefault("mobile", "+639170000101")
        fields.setdefault("created_by", self.staff)
        return Contact.objects.create(**fields)

    def test_1_the_contacts_switch_is_off_unless_set(self):
        self.assertIs(switch.contacts_on(), False)
        with override_settings(SMS_CONTACTS_ENABLED=True):
            self.assertIs(switch.contacts_on(), True)

    def test_2_the_table_check_is_true_once_migrated(self):
        self.assertIs(switch.contacts_tables_exist(), True)

    def test_3_ids_have_their_prefixes(self):
        contact = self.contact()
        group = ContactGroup.objects.create(owner_kind="hq", name="Resellers")
        self.assertTrue(is_public_id(contact.public_id, "cont"))
        self.assertTrue(is_public_id(group.public_id, "grp"))

    def test_4_an_hq_contact_cannot_belong_to_a_site(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.contact(site=make_site(make_account()))

    def test_5_one_contact_per_number(self):
        self.contact()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.contact(first_name="Ben")

    def test_6_group_names_are_unique_ignoring_capitals(self):
        ContactGroup.objects.create(owner_kind="hq", name="Resellers")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContactGroup.objects.create(owner_kind="hq", name="RESELLERS")

    def test_7_deleting_a_group_keeps_its_contacts_and_the_reverse(self):
        group = ContactGroup.objects.create(owner_kind="hq", name="A")
        contact = self.contact()
        contact.groups.add(group)
        group.delete()
        self.assertTrue(Contact.objects.filter(pk=contact.pk).exists())
        group = ContactGroup.objects.create(owner_kind="hq", name="A")
        contact.groups.add(group)
        contact.delete()
        self.assertTrue(ContactGroup.objects.filter(pk=group.pk).exists())

    def test_8_a_staff_user_who_created_a_contact_cannot_be_deleted(self):
        from django.db.models import ProtectedError
        self.contact()
        with self.assertRaises(ProtectedError):
            self.staff.delete()
