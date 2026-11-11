import dataclasses
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import override_settings

from apps.common.errors import ApiError
from apps.inbox import contacts
from apps.inbox.models import Contact, ContactGroup
from apps.inbox.tests.contacts_base import ANA, BEN, ContactsCase

PERSONAL = ("Ana", "Sample", "Ben", "Example", "Resellers", "917000", "0101")


class ScopeTests(ContactsCase):
    def test_1_a_scope_needs_both_switches_the_table_and_an_access_row(self):
        self.assertIsNotNone(contacts.contact_scope(self.staff))
        with override_settings(SMS_CONTACTS_ENABLED=False):
            self.assertIsNone(contacts.contact_scope(self.staff))
        with override_settings(SMS_INBOX_ENABLED=False):
            self.assertIsNone(contacts.contact_scope(self.staff))
        from unittest import mock
        with mock.patch("apps.inbox.contacts.contacts_tables_exist", return_value=False):
            self.assertIsNone(contacts.contact_scope(self.staff))
        nobody = get_user_model().objects.create_user("nobody", password="pw", is_staff=True)
        self.assertIsNone(contacts.contact_scope(nobody))
        self.assertIsNone(contacts.contact_scope(None))

    def test_2_a_user_who_is_not_staff_or_not_active_has_no_scope(self):
        self.staff.is_active = False
        self.staff.save()
        self.assertIsNone(contacts.contact_scope(self.staff))


class ContactTests(ContactsCase):
    def test_1_add_normalises_the_number_and_audits_without_values(self):
        row = contacts.add_contact(self.scope(), ANA)
        self.assertEqual(row["first_name"], "Ana")
        self.assertEqual(Contact.objects.get().mobile, "+639170000101")
        self.assertNotIn("0101", row["mobile"].replace("***", "")[6:-4] or "x")
        self.assertTrue(row["mobile"].startswith("+63917***"))
        entries = self.entries("contact_add")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].detail, {"groups": 0})
        self.assertNothingPersonalIn(self.entries(), *PERSONAL)

    def test_2_a_number_written_another_way_is_the_same_number(self):
        first = contacts.add_contact(self.scope(), ANA)
        for form in ("09170000101", "+63 917 000 0101", "9170000101"):
            with self.assertRaises(ApiError) as caught:
                contacts.add_contact(self.scope(), {**BEN, "mobile": form})
            self.assertEqual(caught.exception.code, "already_exists")
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(caught.exception.fields, {"contact": first["id"]})
        self.assertEqual(Contact.objects.count(), 1)

    def test_3_the_fields_are_checked(self):
        bad = [
            {**ANA, "first_name": ""}, {**ANA, "first_name": "x" * 81}, {**ANA, "last_name": "y" * 81},
            {**ANA, "first_name": "A\x00na"}, {**ANA, "mobile": "0917"}, {**ANA, "mobile": None},
            {**ANA, "first_name": 5},
        ]
        for data in bad:
            with self.assertRaises(ApiError) as caught:
                contacts.add_contact(self.scope(), data)
            self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(Contact.objects.count(), 0)
        self.assertEqual(self.entries(), [])
        row = contacts.add_contact(self.scope(), {"first_name": "  Ana ", "mobile": ANA["mobile"]})
        self.assertEqual((row["first_name"], row["last_name"]), ("Ana", ""))

    def test_4_groups_are_chosen_from_the_list(self):
        group = contacts.add_group(self.scope(), {"name": "Resellers"})
        row = contacts.add_contact(self.scope(), {**ANA, "groups": [group["id"], group["id"]]})
        self.assertEqual(row["groups"], [{"id": group["id"], "name": "Resellers"}])
        for groups in (["grp_nope"], "x", [1], ["grp_" + "0" * 20], ["g"] * 51):
            with self.assertRaises(ApiError):
                contacts.add_contact(self.scope(), {**BEN, "groups": groups})

    def test_5_open_shows_the_full_number_and_audits_once(self):
        added = contacts.add_contact(self.scope(), ANA)
        opened = contacts.open_contact(self.scope(), added["id"])
        self.assertEqual(opened["mobile"], "+639170000101")
        entries = self.entries("contact_open")
        self.assertEqual((len(entries), entries[0].target_id, entries[0].detail), (1, added["id"], {}))
        self.assertIsNone(contacts.open_contact(self.scope(), "cont_" + "0" * 20))
        self.assertIsNone(contacts.open_contact(self.scope(), "junk"))
        self.assertEqual(len(self.entries("contact_open")), 1)
        self.assertNothingPersonalIn(self.entries(), *PERSONAL)

    def test_6b_edit_answers_already_exists_when_another_contact_wins_the_race(self):
        a = contacts.add_contact(self.scope(), ANA)
        b = contacts.add_contact(self.scope(), BEN)
        # The pre-check cannot see the other contact, as when it is added a moment later.
        blind = dataclasses.replace(self.scope(), contacts=Contact.objects.exclude(public_id=b["id"]))
        with mock.patch.object(Contact, "save", side_effect=IntegrityError):
            with self.assertRaises(ApiError) as caught:
                contacts.edit_contact(blind, a["id"], {**ANA, "mobile": BEN["mobile"]})
        self.assertEqual(caught.exception.code, "already_exists")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.fields, {"contact": b["id"]})
        self.assertEqual(Contact.objects.get(public_id=a["id"]).mobile, "+639170000101")

    def test_6_edit_changes_audits_the_field_names_and_refuses_another_contacts_number(self):
        a = contacts.add_contact(self.scope(), ANA)
        b = contacts.add_contact(self.scope(), BEN)
        row = contacts.edit_contact(self.scope(), a["id"], {**ANA, "first_name": "Anna"})
        self.assertEqual(row["first_name"], "Anna")
        entry = self.entries("contact_edit")[0]
        self.assertEqual(entry.detail, {"changed": ["first_name"], "groups": 0})
        with self.assertRaises(ApiError) as caught:
            contacts.edit_contact(self.scope(), a["id"], {**ANA, "mobile": BEN["mobile"]})
        self.assertEqual(caught.exception.fields, {"contact": b["id"]})
        self.assertEqual(Contact.objects.get(public_id=a["id"]).mobile, "+639170000101")
        self.assertNothingPersonalIn(self.entries(), *PERSONAL, "Anna")

    def test_7_an_edit_that_changes_nothing_writes_nothing_and_a_missing_contact_is_none(self):
        a = contacts.add_contact(self.scope(), ANA)
        contacts.edit_contact(self.scope(), a["id"], ANA)
        self.assertEqual(self.entries("contact_edit"), [])
        self.assertIsNone(contacts.edit_contact(self.scope(), "cont_" + "0" * 20, ANA))

    def test_8_edit_replaces_the_groups(self):
        one = contacts.add_group(self.scope(), {"name": "One"})
        two = contacts.add_group(self.scope(), {"name": "Two"})
        a = contacts.add_contact(self.scope(), {**ANA, "groups": [one["id"]]})
        row = contacts.edit_contact(self.scope(), a["id"], {**ANA, "groups": [two["id"]]})
        self.assertEqual([g["id"] for g in row["groups"]], [two["id"]])
        self.assertEqual(self.entries("contact_edit")[0].detail, {"changed": ["groups"], "groups": 1})

    def test_9_delete_removes_the_contact_and_its_links_and_keeps_the_group(self):
        group = contacts.add_group(self.scope(), {"name": "One"})
        a = contacts.add_contact(self.scope(), {**ANA, "groups": [group["id"]]})
        self.assertIs(contacts.delete_contact(self.scope(), a["id"]), True)
        self.assertEqual(Contact.objects.count(), 0)
        self.assertEqual(ContactGroup.objects.count(), 1)
        self.assertEqual(self.entries("contact_delete")[0].detail, {"groups": 1})
        self.assertIsNone(contacts.delete_contact(self.scope(), a["id"]))
        self.assertNothingPersonalIn(self.entries(), *PERSONAL)

    def test_10_a_read_only_user_cannot_change_anything_and_nothing_is_written(self):
        a = contacts.add_contact(self.scope(), ANA)
        reader = self.read_only()
        before = len(self.entries())
        group = contacts.add_group(self.scope(), {"name": "One"})
        calls = [
            lambda: contacts.add_contact(reader, BEN),
            lambda: contacts.edit_contact(reader, a["id"], BEN),
            lambda: contacts.delete_contact(reader, a["id"]),
            lambda: contacts.add_group(reader, {"name": "Two"}),
            lambda: contacts.rename_group(reader, group["id"], {"name": "Two"}),
            lambda: contacts.remove_group(reader, group["id"]),
            lambda: contacts.check_upload(reader, "a,b,0917000010", group["id"]),
            lambda: contacts.save_upload(reader, "a,b,0917000010", group["id"], 1),
        ]
        for call in calls:
            with self.assertRaises(ApiError) as caught:
                call()
            self.assertEqual((caught.exception.code, caught.exception.status), ("forbidden", 403))
        self.assertEqual(len(self.entries()), before + 1)  # only the group added above
        self.assertEqual(Contact.objects.count(), 1)
        self.assertEqual(contacts.open_contact(reader, a["id"])["id"], a["id"])
        self.assertEqual(len(contacts.list_contacts(reader)[0]), 1)


class ListTests(ContactsCase):
    def setUp(self):
        super().setUp()
        self.group = contacts.add_group(self.scope(), {"name": "Resellers"})
        self.a = contacts.add_contact(self.scope(), {**ANA, "groups": [self.group["id"]]})
        self.b = contacts.add_contact(self.scope(), BEN)
        self.before = len(self.entries())

    def ids(self, **kwargs):
        rows, following = contacts.list_contacts(self.scope(), **kwargs)
        return [row["id"] for row in rows]

    def test_1_the_list_never_carries_a_full_number_and_is_oldest_first(self):
        rows, following = contacts.list_contacts(self.scope())
        self.assertEqual([r["id"] for r in rows], [self.a["id"], self.b["id"]])
        self.assertIsNone(following)
        for row in rows:
            self.assertIn("***", row["mobile"])
            self.assertNotIn("000010", row["mobile"])
        self.assertEqual(len(self.entries()), self.before)

    def test_2_paging_by_cursor(self):
        rows, following = contacts.list_contacts(self.scope(), limit=1)
        self.assertEqual((len(rows), following), (1, self.a["id"]))
        rows, following = contacts.list_contacts(self.scope(), limit=1, cursor=following)
        self.assertEqual(([r["id"] for r in rows], following), ([self.b["id"]], None))
        with self.assertRaises(ApiError) as caught:
            contacts.list_contacts(self.scope(), cursor="cont_" + "0" * 20)
        self.assertEqual(caught.exception.code, "invalid_cursor")

    def test_3_filter_by_group(self):
        self.assertEqual(self.ids(group=self.group["id"]), [self.a["id"]])
        with self.assertRaises(ApiError) as caught:
            contacts.list_contacts(self.scope(), group="grp_" + "0" * 20)
        self.assertEqual(caught.exception.status, 404)

    def test_4_a_name_search_is_not_audited(self):
        self.assertEqual(self.ids(query="Sam"), [self.a["id"]])
        self.assertEqual(self.ids(query="BEN"), [self.b["id"]])
        self.assertEqual(self.ids(query="zzz"), [])
        self.assertEqual(len(self.entries()), self.before)

    def test_5_a_number_search_is_audited_with_a_count_only(self):
        self.assertEqual(self.ids(query="09170000101"), [self.a["id"]])
        self.assertEqual(self.ids(query="+63 917 000 0999"), [])
        found = self.entries("contact_find")
        self.assertEqual([e.detail for e in found], [{"found": 1}, {"found": 0}])
        self.assertNothingPersonalIn(self.entries(), *PERSONAL)

    def test_6_a_query_must_be_two_characters_or_a_number(self):
        for query in ("a", 5, "x" * 81):
            with self.assertRaises(ApiError) as caught:
                contacts.list_contacts(self.scope(), query=query)
            self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(len(self.ids(query="  ")), 2)
        self.assertEqual(len(self.ids(query=None)), 2)

    def test_7_the_number_of_queries_does_not_grow_with_the_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        scope = self.scope()
        with CaptureQueriesContext(connection) as small:
            contacts.list_contacts(scope)
        for n in range(5):
            contacts.add_contact(scope, {"first_name": "X", "mobile": f"091700002{n:02d}"})
        with CaptureQueriesContext(connection) as large:
            contacts.list_contacts(scope)
        self.assertEqual(len(small), len(large))


class GroupTests(ContactsCase):
    def test_1_add_list_rename_remove(self):
        scope = self.scope()
        group = contacts.add_group(scope, {"name": "  Resellers "})
        self.assertEqual(group["name"], "Resellers")
        contacts.add_contact(scope, {**ANA, "groups": [group["id"]]})
        self.assertEqual(contacts.list_groups(scope), [{"id": group["id"], "name": "Resellers", "count": 1}])
        renamed = contacts.rename_group(scope, group["id"], {"name": "Dealers"})
        self.assertEqual((renamed["name"], renamed["count"]), ("Dealers", 1))
        contacts.remove_group(scope, group["id"])
        self.assertEqual(contacts.list_groups(scope), [])
        self.assertEqual(Contact.objects.count(), 1)
        self.assertEqual(self.entries("contact_group_remove")[0].detail, {"contacts": 1})
        self.assertEqual(len(self.entries("contact_group_add", "contact_group_rename")), 2)
        self.assertNothingPersonalIn(self.entries(), "Resellers", "Dealers", *PERSONAL)

    def test_2_names_are_unique_ignoring_capitals(self):
        scope = self.scope()
        first = contacts.add_group(scope, {"name": "Resellers"})
        other = contacts.add_group(scope, {"name": "Other"})
        with self.assertRaises(ApiError) as caught:
            contacts.add_group(scope, {"name": "RESELLERS"})
        self.assertEqual((caught.exception.code, caught.exception.fields), ("already_exists", {"group": first["id"]}))
        with self.assertRaises(ApiError):
            contacts.rename_group(scope, other["id"], {"name": "resellers"})
        contacts.rename_group(scope, first["id"], {"name": "RESELLERS"})  # its own name, other capitals

    def test_3_names_are_checked(self):
        for name in ("", "  ", "x" * 61, None, 5, "a\x00b"):
            with self.assertRaises(ApiError) as caught:
                contacts.add_group(self.scope(), {"name": name})
            self.assertEqual(caught.exception.code, "invalid_request")

    def test_4_a_missing_group_is_not_found(self):
        for call in (contacts.rename_group, ):
            with self.assertRaises(ApiError) as caught:
                call(self.scope(), "grp_" + "0" * 20, {"name": "x"})
            self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(ApiError):
            contacts.remove_group(self.scope(), "bad")

    def test_5_the_limit(self):
        ContactGroup.objects.bulk_create(
            [ContactGroup(owner_kind="hq", name=f"g{n}") for n in range(contacts.MAX_GROUPS)]
        )
        with self.assertRaises(ApiError) as caught:
            contacts.add_group(self.scope(), {"name": "one more"})
        self.assertEqual(caught.exception.code, "group_limit")
