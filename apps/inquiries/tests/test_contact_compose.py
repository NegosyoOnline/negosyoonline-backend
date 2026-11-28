"""STORE-CONTACTS slice B (a): a store texts one of its own contacts through the compose route."""
from django.test import override_settings

from apps.inquiries.models import InquiryConversation, InquiryMessage, InquirySmsOptOut
from apps.inquiries.tests.test_sms_inbox import (
    MOBILE, OTHER_MOBILE, SmsOwnerCase, make_member,
)
from apps.notifications.models import SmsMessage
from apps.notifications.tests.base import set_policy

on = override_settings(STORE_CONTACTS_ENABLED=True)
POPUP = {"first_name": "Lia", "last_name": "Sample", "mobile": "0917 000 0301", "groups": []}
LIA = "+639170000301"


@on
class ContactComposeTests(SmsOwnerCase):
    def setUp(self):
        super().setUp()
        self.contacts = f"/v1/sites/{self.site_id}/contacts/"

    def popup(self, data=None):
        gid = self.post(self.contacts + "groups/", {"name": "Regulars"}).json()["id"]
        response = self.post(self.contacts, {**(data or POPUP), "groups": [gid]})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def to(self, contact_id, key="key-contact-01", text="Your order is ready.", base=None, client=None):
        return self.send(
            "post", f"{base or self.base}sms/compose/",
            {"contact_id": contact_id, "text": text, "idempotency_key": key}, client=client,
        )

    def test_the_popup_flow_adds_a_contact_then_texts_it(self):
        cid = self.popup()
        body = self.to(cid).json()
        self.assertEqual((body["state"], body["new_conversation"]), ("sending", True))
        record = SmsMessage.objects.get()
        self.assertEqual((record.purpose, record.recipient, record.site_id), ("store_compose", LIA, self.site.pk))
        message = InquiryMessage.objects.get(site=self.site, direction="out")
        self.assertTrue(message.body.endswith("Your order is ready."))
        conversation = InquiryConversation.objects.get(site=self.site, provider="sms")
        self.assertEqual((conversation.contact_mobile, conversation.contact_name), (LIA, "Lia Sample"))
        again = self.to(cid, key="key-contact-02").json()
        self.assertEqual((again["ref"], again["new_conversation"]), (body["ref"], False))

    def test_a_contact_that_is_a_verified_member_joins_the_members_conversation(self):
        make_member(self.site, LIA, name="Lia Member")
        first = self.compose().json()  # member (MOBILE) is another conversation
        member = make_member(self.site, OTHER_MOBILE, name="Other")
        member_ref = self.compose(member=member, key="key-member-01").json()["ref"]
        cid = self.popup({**POPUP, "mobile": OTHER_MOBILE})
        body = self.to(cid).json()
        self.assertEqual((body["ref"], body["new_conversation"]), (member_ref, False))
        self.assertNotEqual(first["ref"], member_ref)
        self.assertEqual(InquiryConversation.objects.filter(site=self.site, provider="sms", contact_mobile=OTHER_MOBILE).count(), 1)

    def test_a_new_conversation_with_a_verified_member_number_takes_the_members_name(self):
        make_member(self.site, LIA, name="Lia Member")
        self.to(self.popup())
        self.assertEqual(InquiryConversation.objects.get(site=self.site, provider="sms").contact_name, "Lia Member")

    def test_the_same_key_twice_sends_once_and_a_key_for_another_number_conflicts(self):
        cid = self.popup()
        first = self.to(cid).json()
        self.assertEqual(self.to(cid).json()["ref"], first["ref"])
        self.assertEqual(SmsMessage.objects.count(), 1)
        other = self.post(self.contacts, {"first_name": "Ben", "last_name": "X", "mobile": OTHER_MOBILE, "groups": []}).json()["id"]
        self.error(self.to(other), 409, "conflict")

    def test_a_stopped_number_is_refused(self):
        cid = self.popup()
        InquirySmsOptOut.objects.create(site=self.site, mobile=LIA)
        self.error(self.to(cid), 409, "opted_out")
        self.assertFalse(SmsMessage.objects.exists())

    def test_the_stores_old_daily_cap_no_longer_refuses_the_next_text(self):
        set_policy(store_daily_limit=1)
        first, second = self.popup(), self.post(
            self.contacts, {"first_name": "Mia", "mobile": OTHER_MOBILE, "groups": []}).json()["id"]
        self.assertEqual(self.to(first).json()["state"], "sending")
        body = self.to(second, key="key-contact-03").json()
        self.assertEqual(body["state"], "sending")

    def test_a_held_store_is_refused(self):
        cid = self.popup()
        type(self.site).objects.filter(pk=self.site.pk).update(operator_hold=True)
        self.error(self.to(cid), 409, "site_held")

    def test_another_stores_contact_is_not_found(self):
        gid = self.post(f"/v1/sites/{self.ben_site_id}/contacts/groups/", {"name": "G"}, client=self.ben).json()["id"]
        theirs = self.post(f"/v1/sites/{self.ben_site_id}/contacts/",
                           {"first_name": "Ben", "mobile": "+639175550001", "groups": [gid]}, client=self.ben).json()["id"]
        self.error(self.to(theirs), 404, "not_found")
        self.error(self.to("cont_nope"), 404, "not_found")
        self.assertFalse(SmsMessage.objects.exists())

    def test_with_the_contacts_switch_off_it_is_not_found(self):
        cid = self.popup()
        with override_settings(STORE_CONTACTS_ENABLED=False):
            self.error(self.to(cid), 404, "not_found")
        self.assertFalse(SmsMessage.objects.exists())

    def test_both_ids_or_neither_is_a_422(self):
        cid = self.popup()
        member = self.member.public_id
        for extra in ({"contact_id": cid, "member_id": member}, {}):
            response = self.send("post", self.base + "sms/compose/", {"text": "Hi", "idempotency_key": "key-both-001", **extra})
            self.assertEqual(response.status_code, 422, response.content)
        self.assertFalse(SmsMessage.objects.exists())

    def test_the_member_route_still_works(self):
        self.assertEqual(self.compose().json()["state"], "sending")
