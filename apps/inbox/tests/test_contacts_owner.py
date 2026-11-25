"""A store's own contacts routes (STORE-CONTACTS, slice A). Every name and number is made up."""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import override_settings

from apps.accounts.models import AccountAuditEvent
from apps.inbox import contacts, contacts_owner
from apps.inbox import retention as inbox_retention
from apps.inbox.models import Contact, ContactGroup, InboxAccess
from apps.inquiries.models import InquirySmsOptOut
from apps.inquiries.tests.base import switched_on
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

on = override_settings(STORE_CONTACTS_ENABLED=True)
ANA = {"first_name": "Ana", "last_name": "Sample", "mobile": "0917 000 0201", "groups": []}
FULL_ANA = "+639170000201"
BEN = {"first_name": "Ben", "last_name": "Example", "mobile": "+639170000202"}
PERSONAL = ("Ana", "Sample", "Ben", "Example", "0917", "9170000201", "9170000202", "0201", "0202")


@switched_on
@on
class OwnerCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.base = f"/v1/sites/{self.site_id}/contacts/"

    def add(self, data=None, base=None, client=None):
        response = self.post((base or self.base), data or ANA, client=client)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def group(self, name="Regulars", base=None):
        response = self.post((base or self.base) + "groups/", {"name": name})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def staff(self):
        admin = self.staff_user()
        staff = get_user_model().objects.create_user("hq", password="pw", is_staff=True)
        InboxAccess.objects.create(operator=staff, platform="sms", can_send=True, granted_by=admin)
        return staff

    def hq_contact(self, mobile="+639170000201", name="Hq"):
        return Contact.objects.create(
            owner_kind="hq", first_name=name, mobile=mobile, created_by=self.staff_user()
        )

    def staff_user(self):
        user = get_user_model().objects.filter(username="boss").first()
        return user or get_user_model().objects.create_user("boss", password="pw", is_staff=True)

    def ben_site(self):
        """Another owner's store: (the client signed in as its owner, its contacts base)."""
        ben = self.other_client()
        site = self.create_site(client=ben, subdomain="bens-store", display_name="Ben's Store")
        return ben, f"/v1/sites/{site['id']}/contacts/", Site.objects.get(public_id=site["id"])

    def call(self, method, path, data=None, client=None):
        if method == "get":
            return self.get(path, client=client)
        return self.send(method, path, data or {}, client=client)


class GateTests(OwnerCase):
    def paths(self, base=None):
        base = base or self.base
        return [
            ("get", base + "status/"), ("get", base), ("get", base + "cont_abc/"),
            ("post", base), ("patch", base + "cont_abc/"), ("delete", base + "cont_abc/"),
            ("get", base + "groups/"), ("post", base + "groups/"), ("patch", base + "groups/grp_abc/"),
            ("delete", base + "groups/grp_abc/"), ("post", base + "upload/check/"), ("post", base + "upload/save/"),
        ]

    def test_switch_off_every_route_including_status_is_404(self):
        with override_settings(STORE_CONTACTS_ENABLED=False):
            for method, path in self.paths():
                self.error(self.call(method, path), 404, "not_found")
            self.client.cookies.clear()
            self.error(self.get(self.base + "status/"), 404, "not_found")
        self.assertEqual(Contact.objects.count(), 0)

    def test_tables_missing_is_404(self):
        with mock.patch("apps.inbox.switch.contacts_tables_exist", return_value=False):
            self.error(self.get(self.base + "status/"), 404, "not_found")

    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        self.error(self.get(self.base + "status/"), 401, "unauthenticated")
        self.error(self.get(self.base), 401, "unauthenticated")

    def test_another_accounts_site_is_404_like_a_missing_site(self):
        ben = self.other_client()
        missing = f"/v1/sites/s_doesnotexist/contacts/"
        for method, path in self.paths():
            foreign = self.call(method, path, client=ben)
            gone = self.call(method, path.replace(self.base, missing))
            self.error(foreign, 404, "not_found")
            self.assertEqual(foreign.json(), gone.json())

    def test_status(self):
        self.assertEqual(self.get(self.base + "status/").json(), {"contacts": True, "count": 0, "max": 20000})
        self.add()
        self.assertEqual(self.get(self.base + "status/").json()["count"], 1)


class ContactTests(OwnerCase):
    def test_add_returns_masked_row_and_stores_the_site_row(self):
        row = self.add({**ANA, "groups": [self.group()["id"]]})
        self.assertEqual(set(row), {"id", "first_name", "last_name", "mobile_masked", "groups", "opted_out"})
        self.assertNotIn("9170000201", json.dumps(row))
        self.assertEqual(row["groups"][0]["name"], "Regulars")
        stored = Contact.objects.get()
        self.assertEqual((stored.owner_kind, stored.site, stored.created_by_account, stored.created_by),
                         ("site", self.site, self.account, None))
        self.assertEqual(stored.mobile, FULL_ANA)

    def test_list_orders_by_name_ignoring_case_with_count_and_paging(self):
        for first, last, number in (("ben", "z", 301), ("Ana", "b", 302), ("ana", "A", 303), ("Cy", "", 304)):
            self.add({"first_name": first, "last_name": last, "mobile": f"+63917000{number:04d}"})
        body = self.get(self.base).json()
        self.assertEqual([(r["first_name"], r["last_name"]) for r in body["results"]],
                         [("ana", "A"), ("Ana", "b"), ("ben", "z"), ("Cy", "")])
        self.assertEqual((body["count"], body["next"]), (4, None))
        first = self.get(self.base + "?limit=3").json()
        self.assertEqual((len(first["results"]), first["count"]), (3, 4))
        second = self.get(self.base + f"?limit=3&cursor={first['next']}").json()
        self.assertEqual([r["first_name"] for r in second["results"]], ["Cy"])
        self.assertIsNone(second["next"])
        self.error(self.get(self.base + "?cursor=junk"), 400, "invalid_cursor")

    def test_list_filters_by_group_and_search_and_count_follows_the_filter(self):
        group = self.group()
        self.add({**ANA, "groups": [group["id"]]})
        self.add(BEN)
        body = self.get(self.base + f"?group={group['id']}").json()
        self.assertEqual((body["count"], [r["first_name"] for r in body["results"]]), (1, ["Ana"]))
        self.assertEqual(self.get(self.base + "?q=exam").json()["count"], 1)
        self.assertEqual(self.get(self.base + "?q=%2B639170000202").json()["count"], 1)
        self.error(self.get(self.base + "?q=a"), 400, "invalid_request")
        self.error(self.get(self.base + "?group=grp_nope"), 404, "not_found")

    def test_open_gives_the_full_number_and_audits_without_it(self):
        row = self.add()
        opened = self.get(self.base + row["id"] + "/").json()
        self.assertEqual(opened["mobile"], FULL_ANA)
        self.assertTrue(opened["mobile_masked"].startswith("+63") and opened["mobile_masked"] != FULL_ANA)
        events = list(AccountAuditEvent.objects.filter(event="store_contact_opened"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].detail, {"site": self.site_id, "target": row["id"]})
        self.assertEqual(events[0].account, self.account)
        self.assertNothingPersonal()

    def test_a_list_writes_no_open_entry(self):
        self.add()
        self.get(self.base)
        self.assertFalse(AccountAuditEvent.objects.filter(event="store_contact_opened").exists())

    def test_open_failing_rolls_back_nothing_and_unknown_is_404(self):
        self.error(self.get(self.base + "cont_" + "0" * 20 + "/"), 404, "not_found")
        self.assertFalse(AccountAuditEvent.objects.filter(event="store_contact_opened").exists())

    def test_patch_changes_only_what_is_sent(self):
        group = self.group()
        row = self.add({**ANA, "groups": [group["id"]]})
        done = self.patch(self.base + row["id"] + "/", {"first_name": "Anna"})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual((done.json()["first_name"], done.json()["last_name"]), ("Anna", "Sample"))
        self.assertEqual(len(done.json()["groups"]), 1)
        self.assertEqual(Contact.objects.get().mobile, FULL_ANA)
        done = self.patch(self.base + row["id"] + "/", {"groups": [], "last_name": None})
        self.assertEqual((done.json()["groups"], done.json()["last_name"]), ([], ""))
        self.error(self.patch(self.base + row["id"] + "/", {"mobile": "123"}), 400, "invalid_request")
        self.error(self.patch(self.base + row["id"] + "/", {"first_name": None}), 400, "invalid_request")
        self.error(self.patch(self.base + row["id"] + "/", {"owner_kind": "hq"}), 400, "invalid_request")

    def test_first_name_is_required_on_add(self):
        for body in ({"mobile": "0917 000 0211"}, {"first_name": "", "mobile": "0917 000 0211"},
                     {"first_name": "  ", "mobile": "0917 000 0211"}):
            bad = self.error(self.post(self.base, body), 400, "invalid_request")
            self.assertIn("first_name", bad["fields"])
        self.assertEqual(Contact.objects.count(), 0)

    def test_every_patch_ends_with_a_first_name(self):
        nameless = Contact.objects.create(owner_kind="site", site=self.site, mobile="+639170000221", created_by_account=self.account)
        url = self.base + nameless.public_id + "/"
        group = self.group()
        for body in ({"last_name": "Q"}, {"mobile": "0917 000 0222"}, {"first_name": "  "}, {"first_name": ""},
                     {"groups": [group["id"]]}):
            bad = self.error(self.patch(url, body), 400, "invalid_request")
            self.assertIn("first_name", bad["fields"], body)
            self.assertEqual(bad["fields"]["first_name"], ["Enter a first name of up to 80 characters."])
        nameless.refresh_from_db()
        self.assertEqual((nameless.first_name, nameless.last_name, nameless.mobile), ("", "", "+639170000221"))
        done = self.patch(url, {"groups": [group["id"]], "first_name": "Ana"})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(Contact.objects.get().first_name, "Ana")

    def test_a_named_contact_cannot_be_blanked(self):
        row = self.add()
        for blank in ("", "  "):
            bad = self.error(self.patch(self.base + row["id"] + "/", {"first_name": blank}), 400, "invalid_request")
            self.assertIn("first_name", bad["fields"])
        self.assertEqual(Contact.objects.get().first_name, "Ana")

    def test_delete_is_204_and_a_second_delete_404(self):
        row = self.add()
        self.assertEqual(self.send("delete", self.base + row["id"] + "/").status_code, 204)
        self.assertEqual(Contact.objects.count(), 0)
        self.error(self.send("delete", self.base + row["id"] + "/"), 404, "not_found")

    def test_duplicate_number_is_409_without_the_number_and_invalid_fields_are_named(self):
        self.add()
        refused = self.post(self.base, {**BEN, "mobile": FULL_ANA})
        body = self.error(refused, 409, "already_exists")
        self.assertNotIn("9170000201", json.dumps(body))
        bad = self.error(self.post(self.base, {"first_name": "", "mobile": "12"}), 400, "invalid_request")
        self.assertEqual(set(bad["fields"]), {"first_name", "mobile"})
        self.assertNotIn("12", bad["message"])

    def test_opted_out_reads_this_stores_stop_list_only(self):
        self.add()
        self.add(BEN)
        InquirySmsOptOut.objects.create(site=self.site, mobile=FULL_ANA)
        InquirySmsOptOut.objects.create(site=self.ben_site()[2], mobile="+639170000202")  # another store's STOP
        rows = {r["first_name"]: r["opted_out"] for r in self.get(self.base).json()["results"]}
        self.assertEqual(rows, {"Ana": True, "Ben": False})
        from apps.inbox.models import DoNotText
        DoNotText.objects.create(platform="sms", mobile="+639170000202")  # HQ's list is not the store's
        rows = {r["first_name"]: r["opted_out"] for r in self.get(self.base).json()["results"]}
        self.assertFalse(rows["Ben"])

    def test_no_name_or_number_in_audit_or_logs(self):
        with self.assertNoLogs("apps", level="DEBUG"):
            row = self.add({**ANA, "groups": [self.group()["id"]]})
            self.get(self.base + row["id"] + "/")
            self.patch(self.base + row["id"] + "/", {"first_name": "Anna"})
            self.get(self.base + "?q=%2B639170000201")
            self.post(self.base, {**BEN, "mobile": FULL_ANA})  # a refusal
            self.send("delete", self.base + row["id"] + "/")
        self.assertTrue(AccountAuditEvent.objects.filter(event__startswith="store_").exists())
        self.assertNothingPersonal()

    def assertNothingPersonal(self):
        text = json.dumps([[e.event, e.detail] for e in AccountAuditEvent.objects.filter(event__startswith="store_")])
        for value in PERSONAL + ("Anna",):
            self.assertNotIn(value, text)


class GroupTests(OwnerCase):
    def test_groups_crud_counts_and_delete_keeps_contacts(self):
        group = self.group()
        self.add({**ANA, "groups": [group["id"]]})
        self.add(BEN)
        self.assertEqual(self.get(self.base + "groups/").json(), [{"id": group["id"], "name": "Regulars", "count": 1}])
        renamed = self.patch(self.base + f"groups/{group['id']}/", {"name": "VIP"})
        self.assertEqual((renamed.json()["name"], renamed.json()["count"]), ("VIP", 1))
        self.assertEqual(self.send("delete", self.base + f"groups/{group['id']}/").status_code, 204)
        self.assertEqual(Contact.objects.count(), 2)
        self.assertEqual(self.get(self.base + "groups/").json(), [])
        self.error(self.send("delete", self.base + f"groups/{group['id']}/"), 404, "not_found")

    def test_group_names_are_unique_per_store_ignoring_case_but_not_across_stores(self):
        self.group("Regulars")
        self.error(self.post(self.base + "groups/", {"name": "regulars"}), 409, "already_exists")
        self.error(self.post(self.base + "groups/", {"name": " "}), 400, "invalid_request")
        ben, other_base, _ = self.ben_site()
        response = self.post(other_base + "groups/", {"name": "Regulars"}, client=ben)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(ContactGroup.objects.filter(owner_kind="site").count(), 2)

    def test_limits(self):
        with mock.patch.object(contacts, "MAX_GROUPS", 2):
            self.group("A")
            self.group("B")
            self.error(self.post(self.base + "groups/", {"name": "C"}), 400, "group_limit")
        ids = [self.group(f"G{n}")["id"] for n in range(3)]
        with mock.patch.object(contacts, "MAX_GROUPS_PER_CONTACT", 2):
            self.error(self.post(self.base, {**ANA, "groups": ids}), 400, "invalid_request")
            self.add({**ANA, "groups": ids[:2]})
        with mock.patch.object(contacts, "MAX_STORE_CONTACTS", 1):
            self.error(self.post(self.base, BEN), 400, "contact_limit")
            self.assertEqual(self.get(self.base + "status/").json()["max"], 1)
        self.assertEqual(Contact.objects.count(), 1)


class UploadTests(OwnerCase):
    FILE = "first,last,mobile\nAna,Sample,0917 000 0301\nBen,,+639170000302\n0917 000 0303\nbad,line\nCy,X,12345\n"

    def test_check_counts_and_saves_nothing(self):
        body = self.post(self.base + "upload/check/", {"text": self.FILE}).json()
        self.assertEqual((body["new"], body["already"]), (2, 0))
        self.assertEqual([r["line"] for r in body["refused"]], [4, 5, 6])
        self.assertEqual(body["refused"][0]["reason"], "This line has no first name.")
        self.assertEqual(set(body), {"new", "already", "refused"})
        self.assertNotIn("12345", json.dumps(body))
        self.assertEqual(Contact.objects.count(), 0)

    def test_save_with_a_group_refuses_lines_without_a_first_name(self):
        group = self.group()
        old = Contact.objects.create(owner_kind="site", site=self.site, mobile="+639170000304", created_by_account=self.account)
        text = self.FILE + ",Last,0917 000 0305\n   ,Last,0917 000 0306\n0917 000 0304\n"
        checked = self.post(self.base + "upload/check/", {"text": text, "group": group["id"]}).json()
        self.assertEqual((checked["new"], checked["already"]), (2, 0))
        reasons = [r["reason"] for r in checked["refused"]]
        self.assertEqual(reasons.count("This line has no first name."), 4)
        saved = self.post(self.base + "upload/save/", {"text": text, "group": group["id"], "expected_new": checked["new"]})
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(saved.json()["added"], 2)
        self.assertEqual(saved.json()["refused"], 6)
        self.assertEqual(Contact.objects.filter(first_name="").count(), 1)  # only the one that was there before
        old.refresh_from_db()
        self.assertEqual((old.first_name, old.mobile), ("", "+639170000304"))
        self.assertEqual(ContactGroup.objects.get().contacts.count(), 2)
        self.assertFalse(Contact.objects.filter(mobile="+639170000303").exists())
        again = self.post(self.base + "upload/check/", {"text": self.FILE}).json()
        self.assertEqual((again["new"], again["already"]), (0, 2))
        event = AccountAuditEvent.objects.get(event="store_contact_uploaded")
        self.assertEqual(event.detail["added"], 2)
        self.assertNotIn("0917", json.dumps(event.detail))

    def test_a_refused_nameless_line_does_not_block_a_later_named_line_with_the_same_number(self):
        text = "09170000411\nAna,Sample,09170000411\n"
        saved = self.post(self.base + "upload/save/", {"text": text, "expected_new": 1})
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(Contact.objects.get().first_name, "Ana")

    def test_number_only_file_is_refused_line_by_line(self):
        text = "mobile\n09170000401\n09170000402\n"
        checked = self.post(self.base + "upload/check/", {"text": text}).json()
        self.assertEqual((checked["new"], len(checked["refused"])), (0, 2))
        self.assertEqual(checked["refused"][0]["reason"], "This line has no first name.")
        saved = self.post(self.base + "upload/save/", {"text": text, "expected_new": 0})
        self.assertLess(saved.status_code, 300, saved.content)
        self.assertEqual(Contact.objects.count(), 0)

    def test_expected_new_guard_group_checks_size_and_type(self):
        self.error(self.post(self.base + "upload/save/", {"text": self.FILE, "expected_new": 1}), 409, "changed")
        self.error(self.post(self.base + "upload/check/", {"text": self.FILE, "group": "grp_nope"}), 404, "not_found")
        self.error(self.post(self.base + "upload/check/", {"text": "a" * 1_048_577}), 413, "file_too_large")
        self.assertEqual(Contact.objects.count(), 0)

    def test_over_the_store_limit_is_refused(self):
        with mock.patch.object(contacts, "MAX_STORE_CONTACTS", 1):
            self.error(self.post(self.base + "upload/check/", {"text": self.FILE}), 400, "contact_limit")
            self.error(self.post(self.base + "upload/save/", {"text": self.FILE, "expected_new": 2}), 400, "contact_limit")
        self.assertEqual(Contact.objects.count(), 0)


class ScopeTests(OwnerCase):
    def test_an_owner_never_reaches_hq_rows_and_hq_never_reaches_site_rows(self):
        row = self.add()
        hq = self.hq_contact(mobile="+639170000999")
        hq_group = ContactGroup.objects.create(owner_kind="hq", name="Hq group")
        self.assertEqual([r["id"] for r in self.get(self.base).json()["results"]], [row["id"]])
        self.assertEqual(self.get(self.base + "groups/").json(), [])
        self.error(self.get(self.base + hq.public_id + "/"), 404, "not_found")
        self.error(self.patch(self.base + hq.public_id + "/", {"first_name": "X"}), 404, "not_found")
        self.error(self.send("delete", self.base + hq.public_id + "/"), 404, "not_found")
        self.error(self.patch(self.base + f"groups/{hq_group.public_id}/", {"name": "X"}), 404, "not_found")
        self.error(self.post(self.base, {**BEN, "groups": [hq_group.public_id]}), 400, "invalid_request")
        self.assertEqual(Contact.objects.get(pk=hq.pk).first_name, "Hq")
        # HQ staff routes
        staff = self.staff()
        scope = contacts.contact_scope(staff)
        with override_settings(SMS_INBOX_ENABLED=True, SMS_CONTACTS_ENABLED=True):
            scope = contacts.contact_scope(staff)
            self.assertIsNotNone(scope)
            rows, _ = contacts.list_contacts(scope)
            self.assertEqual([r["id"] for r in rows], [hq.public_id])
            self.assertIsNone(contacts.open_contact(scope, row["id"]))
            self.assertIsNone(contacts.edit_contact(scope, row["id"], BEN))
            self.assertIsNone(contacts.delete_contact(scope, row["id"]))
            self.assertEqual([g["name"] for g in contacts.list_groups(scope)], ["Hq group"])
        self.assertEqual(Contact.objects.filter(owner_kind="site").count(), 1)

    def test_one_stores_rows_are_not_another_stores(self):
        row = self.add()
        group = self.group()
        ben, other_base, _ = self.ben_site()
        self.assertEqual(self.get(other_base, client=ben).json()["count"], 0)
        self.error(self.get(other_base + row["id"] + "/", client=ben), 404, "not_found")
        self.error(self.send("delete", other_base + row["id"] + "/", client=ben), 404, "not_found")
        self.error(self.post(other_base, {**BEN, "groups": [group["id"]]}, client=ben), 400, "invalid_request")
        self.error(self.patch(other_base + f"groups/{group['id']}/", {"name": "X"}, client=ben), 404, "not_found")
        self.add(ANA, base=other_base, client=ben)  # the same number may be a contact of another store
        self.assertEqual(Contact.objects.filter(mobile=FULL_ANA).count(), 2)
        self.assertEqual(self.get(self.base).json()["count"], 1)
        self.error(self.get(other_base, client=self.client), 404, "not_found")


class ConstraintAndRetentionTests(OwnerCase):
    def test_database_rules(self):
        def make(**kwargs):
            with transaction.atomic():
                return Contact.objects.create(first_name="X", **kwargs)

        with self.assertRaises(IntegrityError):
            make(owner_kind="site", site=self.site, mobile="+639170000501")  # no created_by_account
        with self.assertRaises(IntegrityError):
            make(owner_kind="hq", mobile="+639170000501", created_by_account=self.account)  # hq needs created_by
        make(owner_kind="site", site=self.site, mobile="+639170000501", created_by_account=self.account)
        with self.assertRaises(IntegrityError):
            make(owner_kind="site", site=self.site, mobile="+639170000501", created_by_account=self.account)
        with transaction.atomic():
            ContactGroup.objects.create(owner_kind="site", site=self.site, name="Vip")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContactGroup.objects.create(owner_kind="site", site=self.site, name="vIP")

    def test_erasing_a_sites_data_deletes_its_contacts_and_groups_only(self):
        group = self.group()
        self.add({**ANA, "groups": [group["id"]]})
        ben, other_base, _ = self.ben_site()
        self.add(BEN, base=other_base, client=ben)
        hq = self.hq_contact()
        ContactGroup.objects.create(owner_kind="hq", name="Hq group")
        counts = inbox_retention.erase_site_inbox(Site.objects.filter(pk=self.site.pk), dry_run=True)
        self.assertEqual((counts["inbox_contacts"], counts["inbox_groups"]), (1, 1))
        self.assertEqual(Contact.objects.count(), 3)
        counts = inbox_retention.erase_site_inbox(Site.objects.filter(pk=self.site.pk))
        self.assertEqual((counts["inbox_contacts"], counts["inbox_groups"]), (1, 1))
        self.assertFalse(Contact.objects.filter(site=self.site).exists())
        self.assertFalse(ContactGroup.objects.filter(site=self.site).exists())
        self.assertTrue(Contact.objects.filter(pk=hq.pk).exists())
        self.assertEqual(Contact.objects.filter(owner_kind="site").count(), 1)
        self.assertEqual(ContactGroup.objects.count(), 1)  # HQ's own group stays


class PickerQueryTests(OwnerCase):
    def test_q_matches_first_name_last_name_or_part_of_the_number(self):
        self.add(ANA)
        self.add(BEN)
        for query, found in (("an", 1), ("sam", 1), ("exam", 1), ("0917", 2), ("0201", 1), ("%2B63917000020", 2), ("917 000 0202", 1), ("zz", 0)):
            with self.subTest(query=query):
                self.assertEqual(self.get(self.base + f"?q={query}").json()["count"], found)
        self.error(self.get(self.base + "?q=a"), 400, "invalid_request")
