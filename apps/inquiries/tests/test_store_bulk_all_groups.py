"""ALL-GROUPS-OPTION (z8uq9mjx4d, Coach Ed 2026-11-25): on store Bulk SMS and Schedule SMS, audience "all"
means All groups: every contact of the store in at least one of its groups, each phone once. Verified
Members are not read. Every name and number is made up."""
from django.test import override_settings

from apps.inbox.models import Contact, ContactGroup
from apps.inquiries.models import InquirySmsOptOut
from apps.inquiries.tests.test_sms_inbox import make_member
from apps.inquiries.tests.test_store_bulk import TEXT
from apps.inquiries.tests.test_store_bulk_first_name import HI, SpyMixin, sent_texts
from apps.inquiries.tests.test_store_bulk_pick import PickCase
from apps.inquiries.tests.test_store_bulk_schedule import AFTER, LATER, ScheduleCase
from apps.notifications.models import StoreBulkBatch, StoreBulkRecipient


class AllGroupsCase(PickCase):  # contacts and groups through the API; no member mirror
    def all(self):
        return self.send("post", self.root + "audience/", {"audience": "all"}).json()

    def confirm_all(self, key="key-all-0001", **extra):
        return self.send("post", self.root + "batches/", {
            "text": TEXT, "audience": "all", "idempotency_key": key, "confirm": True, **extra})


class AudienceTests(AllGroupsCase):
    def test_group_contacts_only_and_a_member_in_no_group_is_left_out(self):
        make_member(self.site, "+639171000050", name="Lone Member")
        self.add("Ana", "+639172000001", [self.gid])
        self.add("Ben", "+639172000002", [self.gid2])
        self.add("Ungrouped", "+639172000003")
        body = self.all()
        self.assertEqual((body["audience"], body["total_members"], body["will_send"]), ("all", 2, 2))
        self.assertEqual(body["left_out"], {"unverified": 0, "invalid_number": 0, "opted_out": 0, "duplicates": 0})
        self.assertEqual(set(body), {"audience", "total_members", "will_send", "left_out"})

    def test_a_contact_in_two_groups_counts_once(self):
        self.add("Ana", "+639172000001", [self.gid, self.gid2])
        body = self.all()
        self.assertEqual((body["total_members"], body["will_send"], body["left_out"]["duplicates"]), (1, 1, 0))

    def test_stop_invalid_and_duplicate_exclusions(self):
        self.add("Ana", "+639172000001", [self.gid])
        self.add("Stop", "+639172000002", [self.gid])
        InquirySmsOptOut.objects.create(site=self.site, mobile="+639172000002")
        self.add("Test", "+639002000003", [self.gid])
        body = self.all()
        # a store's contacts are unique by phone (database constraint), so a repeat cannot occur here
        # and `duplicates` stays 0; the check in `_select` is kept as the guard it always was
        self.assertEqual((body["total_members"], body["will_send"]), (3, 1))
        self.assertEqual(body["left_out"], {"unverified": 0, "invalid_number": 1, "opted_out": 1, "duplicates": 0})

    def test_a_members_phone_that_is_also_a_group_contact_counts_once_as_a_contact(self):
        make_member(self.site, "+639172000101", name="Maria Santos")
        self.add("Ana", "+639172000101", [self.gid])
        body = self.all()
        self.assertEqual((body["total_members"], body["will_send"]), (1, 1))
        batch = self.start(audience="all")
        recipient = StoreBulkRecipient.objects.get(batch=batch)
        self.assertIsNotNone(recipient.contact_ref)
        self.assertIsNone(recipient.member_ref)

    def test_another_stores_group_contacts_are_never_counted(self):
        self.add("Ana", "+639172000001", [self.gid])
        group = self.post(f"/v1/sites/{self.ben_site_id}/contacts/groups/", {"name": "B"}, client=self.ben).json()["id"]
        self.post(f"/v1/sites/{self.ben_site_id}/contacts/", {"first_name": "Bo", "mobile": "+639182000001", "groups": [group]}, client=self.ben)
        self.assertEqual(self.all()["total_members"], 1)

    def test_switch_off_gives_zero_and_is_refused(self):
        self.add("Ana", "+639172000001", [self.gid])
        with override_settings(STORE_CONTACTS_ENABLED=False):
            body = self.all()
            self.assertEqual((body["total_members"], body["will_send"]), (0, 0))
            response = self.confirm_all()
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "empty_audience"))
        self.assertFalse(StoreBulkBatch.objects.exists())

    def test_a_missing_audience_is_still_400_with_the_new_message(self):
        response = self.send("post", self.root + "audience/", {})
        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid_audience")
        self.assertEqual(error["message"], "Choose All groups, or pick groups or contacts.")


class ConfirmTests(SpyMixin, AllGroupsCase):
    def test_confirm_creates_contact_ref_rows_and_fills_first_name(self):
        spy = self.spy_on_sends()
        self.add("Ana", "+639172000001", [self.gid])
        self.add("Ben", "+639172000002", [self.gid2])
        make_member(self.site, "+639171000050", name="Lone Member")
        batch = self.start(audience="all", text=HI)
        rows = list(batch.recipients.order_by("position"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.contact_ref and r.member_ref is None for r in rows))
        self.assertEqual(batch.audience, "all")
        self.pump(2)
        self.assertEqual(sent_texts(spy), ["Hi Ana, sale today!", "Hi Ben, sale today!"])
        self.assertNotIn("+639171000050", [m.recipient for m in self.records()])


class ScheduledTests(SpyMixin, ScheduleCase):
    mirror_members = False

    def test_a_scheduled_all_batch_starts_with_group_contacts(self):
        spy = self.spy_on_sends()
        group = ContactGroup.objects.create(owner_kind="site", site=self.site, name="G")
        for first, mobile in (("Ana", "+639172000001"), ("Ben", "+639172000002")):
            contact = Contact.objects.create(owner_kind="site", site=self.site, first_name=first, mobile=mobile, created_by_account=self.account)
            contact.groups.add(group)
        make_member(self.site, "+639171000050", name="Lone Member")
        batch = self.make(text=HI)
        self.assertEqual((batch.size, batch.audience), (2, "all"))
        self.assertEqual(self.run_due(), 1)
        batch.refresh_from_db()
        rows = list(batch.recipients.order_by("position"))
        self.assertEqual((batch.state, len(rows)), ("waiting", 2))
        self.assertTrue(all(r.contact_ref and r.member_ref is None for r in rows))
        self.pump(2, now=AFTER)
        self.assertEqual(sent_texts(spy), ["Hi Ana, sale today!", "Hi Ben, sale today!"])

    def test_scheduling_all_with_no_group_contact_is_refused(self):
        make_member(self.site, "+639171000050", name="Lone Member")
        response = self.schedule(at=LATER)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "empty_audience"))
