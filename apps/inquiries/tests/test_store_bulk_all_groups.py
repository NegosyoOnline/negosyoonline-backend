"""ALL-MEMBERS-ALL-GROUPS (Coach Ed, 2026-11-25): the "all" audience of store Bulk SMS and Schedule SMS is the
store's verified members plus every contact in any of the store's groups, each phone once. Every name and
number is made up."""
from unittest import mock

from django.test import override_settings

from apps.inbox.models import Contact
from apps.inquiries.models import InquirySmsOptOut
from apps.inquiries.tests.test_store_bulk import NUMBERS
from apps.inquiries.tests.test_store_bulk_pick import PickCase
from apps.inquiries.tests.test_store_bulk_schedule import AFTER, ScheduleCase
from apps.notifications import services
from apps.notifications.models import StoreBulkBatch

HI = "Hi {first_name}, sale today!"


class AllGroupsCase(PickCase, ScheduleCase):
    def all_body(self):
        return self.audience("all").json()


class AudienceTests(AllGroupsCase):
    def test_members_plus_group_contacts_and_a_contact_in_no_group_is_not_counted(self):
        self.add("Ana", "+639172000001", [self.gid])
        self.add("Ben", "+639172000002", [self.gid, self.gid2])  # two groups, still one row
        self.add("Out", "+639172000006")  # in no group
        body = self.all_body()
        self.assertEqual((body["total_members"], body["will_send"]), (5, 5))
        self.assertEqual(body["left_out"], {"unverified": 0, "invalid_number": 0, "opted_out": 0, "duplicates": 0})
        self.assertNotIn("+6391", str(body))

    def test_a_member_and_contact_phone_is_counted_once_and_reported_under_duplicates(self):
        self.add("Same", NUMBERS[0], [self.gid])
        body = self.all_body()
        self.assertEqual((body["total_members"], body["will_send"]), (4, 3))
        self.assertEqual(body["left_out"]["duplicates"], 1)

    def test_the_member_row_wins(self):
        self.add("Same", NUMBERS[0], [self.gid])
        batch = self.start()
        rows = list(batch.recipients.order_by("position"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].mobile, NUMBERS[0])
        self.assertIsNotNone(rows[0].member_ref)
        self.assertIsNone(rows[0].contact_ref)

    def test_an_opted_out_contact_and_a_test_number_are_left_out(self):
        self.add("Di", "+639172000004", [self.gid])
        self.add("Test", "+639002000005", [self.gid])
        InquirySmsOptOut.objects.create(site=self.site, mobile="+639172000004")
        body = self.all_body()
        self.assertEqual((body["total_members"], body["will_send"]), (5, 3))
        self.assertEqual((body["left_out"]["opted_out"], body["left_out"]["invalid_number"]), (1, 1))

    def test_another_stores_groups_and_contacts_are_never_included(self):
        other = self.post(f"/v1/sites/{self.ben_site_id}/contacts/groups/", {"name": "B"}, client=self.ben).json()["id"]
        response = self.post(
            f"/v1/sites/{self.ben_site_id}/contacts/",
            {"first_name": "Zed", "mobile": "+639172000009", "groups": [other]}, client=self.ben)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.all_body()["will_send"], 3)

    def test_with_the_switch_off_all_is_members_only(self):
        self.add("Ana", "+639172000001", [self.gid])
        with override_settings(STORE_CONTACTS_ENABLED=False):
            response = self.audience("all")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual((body["total_members"], body["will_send"]), (3, 3))
            self.assertEqual(self.confirm().status_code, 201)
        self.assertEqual(StoreBulkBatch.objects.get().size, 3)


class ConfirmTests(AllGroupsCase):
    def test_each_row_carries_its_own_ref_and_first_name_fills_for_both(self):
        ana = self.add("Ana", "+639172000001", [self.gid])
        batch = self.start(text=HI)
        self.assertEqual(batch.size, 4)
        rows = list(batch.recipients.order_by("position"))
        self.assertEqual([(r.member_ref is not None, r.contact_ref is not None) for r in rows],
                         [(True, False)] * 3 + [(False, True)])
        self.assertEqual(rows[3].contact_ref, Contact.objects.get(public_id=ana).pk)
        with mock.patch.object(services, "send_sms", wraps=services.send_sms) as spy:
            self.pump(4)
        texts = [call.kwargs["text"] for call in spy.call_args_list]
        self.assertEqual(texts, ["Hi Member, sale today!"] * 3 + ["Hi Ana, sale today!"])
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent), ("finished", 4))

    def test_expected_count_is_the_new_total(self):
        self.add("Ana", "+639172000001", [self.gid])
        self.assertEqual(self.confirm(expected_count=3).status_code, 409)
        self.assertEqual(self.confirm(expected_count=4).status_code, 201)

    def test_a_contact_that_left_the_list_or_stopped_is_skipped_at_send_time(self):
        ana = self.add("Ana", "+639172000001", [self.gid])
        self.add("Ben", "+639172000002", [self.gid])
        batch = self.start()
        Contact.objects.filter(public_id=ana).delete()
        InquirySmsOptOut.objects.create(site=self.site, mobile="+639172000002")
        self.pump(5)
        reasons = dict(batch.recipients.values_list("position", "reason"))
        self.assertEqual((reasons[3], reasons[4]), ("contact_removed", "opted_out"))


class ScheduleTests(AllGroupsCase):
    def test_the_create_count_includes_group_contacts(self):
        self.add("Ana", "+639172000001", [self.gid])
        self.add("Out", "+639172000006")
        body = self.schedule().json()
        self.assertEqual((body["size"], body["left_out"]["duplicates"]), (4, 0))

    def test_the_due_time_start_sends_to_group_contacts_too(self):
        ana = self.add("Ana", "+639172000001", [self.gid])
        batch = self.make(text=HI)
        self.assertEqual(batch.size, 4)
        self.add("Ben", "+639172000002", [self.gid])  # joined a group after scheduling: the recount has them
        self.assertEqual(self.run_due(), 1)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.size), ("waiting", 5))
        rows = list(batch.recipients.order_by("position"))
        self.assertEqual([r.contact_ref is not None for r in rows], [False] * 3 + [True] * 2)
        self.assertEqual(rows[3].contact_ref, Contact.objects.get(public_id=ana).pk)
        with mock.patch.object(services, "send_sms", wraps=services.send_sms) as spy:
            self.pump(5, now=AFTER)
        self.assertEqual([c.kwargs["text"] for c in spy.call_args_list][3:], ["Hi Ana, sale today!", "Hi Ben, sale today!"])

    def test_the_due_time_start_with_the_switch_off_is_members_only(self):
        self.add("Ana", "+639172000001", [self.gid])
        batch = self.make()
        with override_settings(STORE_CONTACTS_ENABLED=False):
            self.run_due()
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.size), ("waiting", 3))
        self.assertFalse(batch.recipients.filter(contact_ref__isnull=False).exists())
