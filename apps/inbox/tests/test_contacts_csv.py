import json

from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.common.errors import ApiError
from apps.inbox import contacts
from apps.inbox.models import Contact
from apps.inbox.tests.contacts_base import ContactsCase, without_ids

FILE = (
    "First,Last,Mobile,Notes\n"
    "Ana,Sample,0917 000 0101,secret-note-1\n"
    "Ben,Example,+639170000102,secret-note-2\n"
    "Cai,Demo,0917000\n"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,NoFirst,09170000103\n"
    "Dan,Bad,+14155550123\n"
    "Eve,Two\n"
    "Ana,Again,09170000101\n"
    "\n"
)
NAMES = ("Sample", "Example", "Cai", "Demo", "NoFirst", "Bad", "secret-note", "0101", "0102", "0103")


class UploadCase(ContactsCase):
    def setUp(self):
        super().setUp()
        self.group = contacts.add_group(self.scope(), {"name": "Launch"})
        self.before = len(self.entries())

    def check(self, text=FILE, scope=None):
        return contacts.check_upload(scope or self.scope(), text, self.group["id"])

    def save(self, text=FILE, expected=2):
        return contacts.save_upload(self.scope(), text, self.group["id"], expected)


class CheckTests(UploadCase):
    def test_1_the_check_counts_and_lists_refused_lines_by_number_and_reason_only(self):
        answer = self.check()
        self.assertEqual(
            {k: answer[k] for k in ("lines", "new", "existing", "repeated", "refused_count")},
            {"lines": 7, "new": 2, "existing": 0, "repeated": 1, "refused_count": 4},
        )
        self.assertEqual([r["line"] for r in answer["refused"]], [4, 5, 6, 7])
        text = without_ids(json.dumps(answer))
        for value in NAMES:
            self.assertNotIn(value, text)
        self.assertEqual(
            sorted({r["reason"] for r in answer["refused"]}),
            sorted([contacts.REASONS["mobile"], contacts.REASONS["first_name"], contacts.REASONS["columns"]]),
        )

    def test_2_the_check_stores_nothing_and_writes_no_entry(self):
        self.check()
        self.assertEqual(Contact.objects.count(), 0)
        self.assertEqual(len(self.entries()), self.before)

    def test_3_the_header_is_skipped_only_when_the_third_column_has_no_digit(self):
        self.assertEqual(self.check("a,b,c\nAna,S,09170000101\n")["lines"], 1)
        self.assertEqual(self.check("Ana,S,09170000101\nBen,E,09170000102\n")["lines"], 2)

    def test_4_a_number_written_two_ways_is_one_and_the_first_name_wins(self):
        answer = self.check("Ana,A,09170000101\nAna,B,+63 917 000 0101\n")
        self.assertEqual((answer["new"], answer["repeated"], answer["lines"]), (1, 1, 2))
        self.save("Ana,A,09170000101\nAna,B,+63 917 000 0101\n", 1)
        self.assertEqual(Contact.objects.get().last_name, "A")

    def test_5_numbers_already_contacts_count_as_existing(self):
        contacts.add_contact(self.scope(), {"first_name": "Old", "mobile": "09170000101"})
        answer = self.check()
        self.assertEqual((answer["new"], answer["existing"]), (1, 1))

    def test_6_a_bom_is_dropped(self):
        self.assertEqual(self.check("﻿First,Last,Mobile\nAna,S,09170000101\n")["new"], 1)

    def test_7_size_type_and_syntax(self):
        with self.assertRaises(ApiError) as caught:
            self.check("a" * (contacts.MAX_CSV_BYTES + 1))
        self.assertEqual((caught.exception.code, caught.exception.status), ("file_too_large", 413))
        self.check("A,B,junk\n" * (contacts.MAX_CSV_BYTES // 9))  # just under the limit: read
        with self.assertRaises(ApiError) as caught:
            self.check(None)
        self.assertEqual(caught.exception.code, "invalid_request")
        with self.assertRaises(ApiError) as caught:
            self.check("Ana," + "x" * 200_000 + ",0917\n")  # a field over the csv module's limit
        self.assertEqual(caught.exception.code, "invalid_file")

    def test_8_a_missing_group_is_refused(self):
        for group in ("grp_" + "0" * 20, "junk", None, 5):
            with self.assertRaises(ApiError):
                contacts.check_upload(self.scope(), FILE, group)

    def test_9_only_the_first_200_refused_lines_are_listed(self):
        text = "".join(f"A,B,junk{n}\n" for n in range(250))
        answer = self.check(text)
        self.assertEqual((len(answer["refused"]), answer["refused_count"]), (200, 250))

    def test_11_a_number_only_line_is_accepted_with_an_empty_name(self):
        text = "First,Last,Mobile\n,,09170000201\n , ,09170000202\n,Only,09170000203\n"
        self.assertEqual(self.check(text)["new"], 3)
        self.assertEqual(self.save(text, 3)["refused"], 0)
        self.assertEqual(
            list(Contact.objects.order_by("mobile").values_list("first_name", "last_name")),
            [("", ""), ("", ""), ("", "Only")],
        )

    def test_12_a_repeat_keeps_the_first_line_that_has_a_name(self):
        text = "Ana,A,09170000201\n,,09170000201\n,,09170000202\nBen,B,09170000202\n,,09170000203\n,,09170000203\n"
        answer = self.check(text)
        self.assertEqual((answer["new"], answer["repeated"]), (3, 3))
        self.save(text, 3)
        names = dict(Contact.objects.values_list("mobile", "first_name"))
        self.assertEqual(names, {"+639170000201": "Ana", "+639170000202": "Ben", "+639170000203": ""})

    def test_13_a_long_first_name_is_still_refused(self):
        answer = self.check("A" * 81 + ",B,09170000201\n")
        self.assertEqual((answer["new"], answer["refused"][0]["reason"]), (0, contacts.REASONS["first_name"]))

    def test_14_manual_add_still_needs_a_first_name(self):
        with self.assertRaises(ApiError):
            contacts.add_contact(self.scope(), {"first_name": "", "last_name": "", "mobile": "09170000204"})


class SaveTests(UploadCase):
    def test_1_the_save_stores_links_and_audits_counts_only(self):
        counts = self.save()
        self.assertEqual(counts, {"lines": 7, "added": 2, "existing": 0, "refused": 4, "repeated": 1})
        self.assertEqual(Contact.objects.count(), 2)
        self.assertEqual(self.group_count(), 2)
        entry = self.entries("contact_upload")[0]
        self.assertEqual(entry.detail, counts)
        self.assertEqual((entry.target_type, entry.target_id), ("contact_group", self.group["id"]))
        self.assertNothingPersonalIn(self.entries(), *NAMES, "Ana", "Ben", "Launch")

    def group_count(self):
        return contacts.list_groups(self.scope())[0]["count"]

    def test_2_other_columns_are_not_kept(self):
        self.save()
        stored = json.dumps(list(Contact.objects.values()), default=str)
        self.assertNotIn("secret-note", stored)

    def test_3_an_existing_number_keeps_its_name_and_joins_the_group(self):
        contacts.add_contact(self.scope(), {"first_name": "Old", "last_name": "Name", "mobile": "09170000101"})
        counts = self.save(expected=1)
        self.assertEqual((counts["added"], counts["existing"]), (1, 1))
        old = Contact.objects.get(mobile="+639170000101")
        self.assertEqual((old.first_name, old.last_name), ("Old", "Name"))
        self.assertEqual(self.group_count(), 2)

    def test_4_a_wrong_expected_count_changes_nothing(self):
        for expected in (1, 3, "2", None, True):
            with self.assertRaises(ApiError) as caught:
                self.save(expected=expected)
            self.assertIn(caught.exception.code, ("changed", "invalid_request"))
        self.assertEqual(Contact.objects.count(), 0)
        self.assertEqual(len(self.entries()), self.before)

    def test_5_a_contact_added_between_check_and_save_is_a_change(self):
        shown = self.check()["new"]
        contacts.add_contact(self.scope(), {"first_name": "Late", "mobile": "09170000101"})
        with self.assertRaises(ApiError) as caught:
            self.save(expected=shown)
        self.assertEqual((caught.exception.code, caught.exception.status), ("changed", 409))
        self.assertEqual(Contact.objects.count(), 1)

    def test_6_saving_twice_is_refused_the_second_time(self):
        self.save()
        with self.assertRaises(ApiError) as caught:
            self.save()
        self.assertEqual(caught.exception.code, "changed")
        self.assertEqual(Contact.objects.count(), 2)

    def test_7_a_large_file_saves_in_a_bounded_number_of_queries(self):
        text = "".join(f"N{n},L,0917{n:07d}\n" for n in range(5000))
        shown = self.check(text)
        self.assertEqual(shown["new"], 5000)
        with CaptureQueriesContext(connection) as queries:
            self.save(text, expected=5000)
        self.assertLess(len(queries), 150)  # grows with the 500-number chunks, not with the rows
        self.assertEqual(Contact.objects.count(), 5000)
        self.assertEqual(self.group_count(), 5000)
