"""STORE-CONTACTS slice B (c): a bulk text to picked groups and contacts. Every name and number is made up."""
from django.test import override_settings

from apps.inbox.models import Contact, ContactGroup
from apps.inquiries.models import InquirySmsOptOut
from apps.inquiries.tests.test_store_bulk import BulkCase, FULL, TEXT
from apps.notifications.models import StoreBulkBatch, StoreBulkRecipient

on = override_settings(STORE_CONTACTS_ENABLED=True)


@on
class PickCase(BulkCase):
    def setUp(self):
        super().setUp()
        self.cbase = f"/v1/sites/{self.site_id}/contacts/"
        self.gid = self.post(self.cbase + "groups/", {"name": "Regulars"}).json()["id"]
        self.gid2 = self.post(self.cbase + "groups/", {"name": "Others"}).json()["id"]

    def add(self, first, mobile, groups=()):
        response = self.post(self.cbase, {"first_name": first, "mobile": mobile, "groups": list(groups)})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def pick(self, groups=None, contacts=None, path="audience/"):
        return self.send("post", self.root + path, {"audience": "pick", "groups": groups or [], "contacts": contacts or []})

    def code(self, response):
        return response.status_code, response.json()["error"]["code"]


class PickAudienceTests(PickCase):
    def test_counts_exclusions_and_dedupe(self):
        a = self.add("Ana", "+639172000001", [self.gid])
        b = self.add("Ben", "+639172000002", [self.gid, self.gid2])
        self.add("Cy", "+639172000003", [self.gid])
        stopped = self.add("Di", "+639172000004", [self.gid])
        InquirySmsOptOut.objects.create(site=self.site, mobile="+639172000004")
        test = self.add("Test", "+639002000005", [self.gid])
        self.add("Out", "+639172000006")  # in no picked group
        body = self.pick(groups=[self.gid, self.gid2], contacts=[a, b, stopped, test]).json()
        self.assertEqual(body["audience"], "pick")
        self.assertEqual((body["total_members"], body["will_send"]), (5, 3))
        self.assertEqual(body["left_out"], {"unverified": 0, "invalid_number": 1, "opted_out": 1, "duplicates": 0})
        self.assertNotIn("+6391", str(body))

    def test_a_contact_picked_twice_by_group_and_by_name_counts_once(self):
        a = self.add("Ana", "+639172000001", [self.gid])
        self.assertEqual(self.pick(groups=[self.gid], contacts=[a]).json()["will_send"], 1)

    def test_one_contact_alone(self):
        a = self.add("Ana", "+639172000001")
        self.assertEqual(self.pick(contacts=[a]).json()["will_send"], 1)

    def test_at_least_one_id_and_only_this_stores_ids(self):
        self.assertEqual(self.code(self.pick()), (400, "invalid_audience"))
        ben = self.post(f"/v1/sites/{self.ben_site_id}/contacts/groups/", {"name": "B"}, client=self.ben).json()["id"]
        self.assertEqual(self.code(self.pick(groups=[ben])), (400, "invalid_audience"))
        self.assertEqual(self.code(self.pick(contacts=["cont_nope"])), (400, "invalid_audience"))

    def test_all_stays_the_default(self):
        self.add("Ana", "+639172000001")
        body = self.send("post", self.root + "audience/", {}).json()
        self.assertEqual((body["audience"], body["will_send"]), ("all", 3))

    def test_switch_off_refuses_pick(self):
        a = self.add("Ana", "+639172000001")
        with override_settings(STORE_CONTACTS_ENABLED=False):
            self.assertEqual(self.code(self.pick(contacts=[a])), (400, "invalid_audience"))


class PickBatchTests(PickCase):
    def confirm_pick(self, key="key-pick-0001", **extra):
        return self.send("post", self.root + "batches/", {
            "text": TEXT, "audience": "pick", "idempotency_key": key, "confirm": True, **extra})

    def test_a_batch_sends_to_a_contact_and_masks_it_at_the_end(self):
        a = self.add("Ana", "+639172000001", [self.gid])
        response = self.confirm_pick(groups=[self.gid])
        self.assertEqual(response.status_code, 201, response.content)
        batch = StoreBulkBatch.objects.get(public_id=response.json()["id"])
        self.assertEqual((batch.audience, batch.size, batch.picked), ("pick", 1, {"groups": [self.gid], "contacts": []}))
        recipient = batch.recipients.get()
        self.assertEqual((recipient.member_ref, recipient.contact_ref, recipient.name), (None, Contact.objects.get(public_id=a).pk, "Ana"))
        self.pump(1)
        self.assertEqual([r.recipient for r in self.records()], ["+639172000001"])
        self.assertEqual(self.records()[0].purpose, "store_bulk")
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent), ("finished", 1))
        self.assertIn("*", StoreBulkRecipient.objects.get(batch=batch).mobile)

    def test_a_changed_pick_is_audience_changed(self):
        self.add("Ana", "+639172000001", [self.gid])
        response = self.confirm_pick(groups=[self.gid], expected_count=2)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], {"code": "audience_changed", "message": response.json()["error"]["message"], "will_send": 1})
        self.assertEqual(self.confirm_pick(groups=[self.gid], expected_count=1).status_code, 201)

    def test_nobody_after_exclusions_is_empty_audience(self):
        self.add("Di", "+639172000004", [self.gid])
        InquirySmsOptOut.objects.create(site=self.site, mobile="+639172000004")
        self.assertEqual(self.confirm_pick(groups=[self.gid]).json()["error"]["code"], "empty_audience")

    def test_switch_off_refuses_the_batch(self):
        a = self.add("Ana", "+639172000001")
        with override_settings(STORE_CONTACTS_ENABLED=False):
            response = self.confirm_pick(contacts=[a])
        self.assertEqual(self.code(response), (400, "invalid_audience"))
        self.assertFalse(StoreBulkBatch.objects.exists())

    def test_a_deleted_contact_or_a_stop_after_confirm_is_skipped(self):
        a = self.add("Ana", "+639172000001", [self.gid])
        self.add("Ben", "+639172000002", [self.gid])
        self.add("Cy", "+639172000003", [self.gid])
        batch = StoreBulkBatch.objects.get(public_id=self.confirm_pick(groups=[self.gid]).json()["id"])
        Contact.objects.filter(public_id=a).delete()
        InquirySmsOptOut.objects.create(site=self.site, mobile="+639172000002")
        self.pump(3)
        reasons = dict(batch.recipients.values_list("position", "reason"))
        self.assertEqual(reasons, {0: "contact_removed", 1: "opted_out", 2: ""})
        self.assertEqual([r.recipient for r in self.records()], ["+639172000003"])

    def test_an_unknown_group_or_other_audience_is_refused(self):
        self.assertEqual(self.confirm_pick(groups=["grp_nope"]).status_code, 400)
        self.assertEqual(self.send("post", self.root + "audience/", {"audience": "promo"}).status_code, 400)
