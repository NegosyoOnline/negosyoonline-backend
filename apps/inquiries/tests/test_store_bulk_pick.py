"""STORE-CONTACTS slice B (c): a bulk text to picked groups and contacts. Every name and number is made up."""
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

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

    def confirm_pick(self, key="key-pick-0001", **extra):
        return self.send("post", self.root + "batches/", {
            "text": TEXT, "audience": "pick", "idempotency_key": key, "confirm": True, **extra})


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

    def test_an_explicit_all_still_works(self):
        self.add("Ana", "+639172000001")
        body = self.send("post", self.root + "audience/", {"audience": "all"}).json()
        self.assertEqual((body["audience"], body["will_send"]), ("all", 3))

    def test_switch_off_refuses_pick(self):
        a = self.add("Ana", "+639172000001")
        with override_settings(STORE_CONTACTS_ENABLED=False):
            self.assertEqual(self.code(self.pick(contacts=[a])), (400, "invalid_audience"))


class PickBatchTests(PickCase):
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


class BigListTests(PickCase):
    """A store with more than 20,000 contacts (Coach Ed, 2026-11-25: "No limit"). The data is made with
    bulk_create; the audience and confirm must take a fixed number of queries however many contacts
    a group holds."""

    BIG = 25_000
    QUERY_BOUND = 40  # the auth, site and switch checks, the groups, one join, the opt-outs, the batch rows

    def setUp(self):
        super().setUp()
        site = self.site
        Contact.objects.bulk_create(
            [
                Contact(
                    owner_kind="site", site=site, created_by_account=self.account,
                    first_name=f"C{n}", mobile=f"+6391{n:08d}",
                )
                for n in range(self.BIG)
            ],
            batch_size=1000,
        )
        group = ContactGroup.objects.get(public_id=self.gid)
        through = Contact.groups.through
        through.objects.bulk_create(
            [through(contact_id=pk, contactgroup_id=group.pk) for pk in Contact.objects.filter(site=site).values_list("pk", flat=True)],
            batch_size=1000,
        )

    def test_the_audience_of_a_big_group_takes_a_fixed_number_of_queries(self):
        with CaptureQueriesContext(connection) as queries:
            body = self.pick(groups=[self.gid]).json()
        self.assertEqual((body["total_members"], body["will_send"]), (self.BIG, self.BIG))
        self.assertLess(len(queries), self.QUERY_BOUND, len(queries))

    def test_confirming_a_big_group_takes_a_fixed_number_of_queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.confirm_pick(groups=[self.gid])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["size"], self.BIG)
        self.assertEqual(StoreBulkRecipient.objects.count(), self.BIG)
        # the recipient rows go in by bulk_create (batch_size=500; SQLite cuts that by its variable
        # limit); every other query is fixed, however many contacts the group holds
        others = [q["sql"] for q in queries if not q["sql"].startswith('INSERT INTO "notifications_storebulkrecipient"')]
        self.assertLess(len(others), self.QUERY_BOUND, others[:5])

    def test_a_pick_of_more_than_twenty_thousand_contact_ids_is_accepted(self):
        ids = list(Contact.objects.filter(site=self.site).values_list("public_id", flat=True))
        self.assertGreater(len(ids), 20_000)
        body = self.pick(contacts=ids).json()
        self.assertEqual((body["total_members"], body["will_send"]), (self.BIG, self.BIG))
        response = self.confirm_pick(contacts=ids)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["size"], self.BIG)

    def test_the_daily_limit_is_still_2000(self):
        self.assertEqual(self.status().json()["daily_limit"], 2000)
