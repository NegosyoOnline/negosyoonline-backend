"""The twelve routes under /staff/v1/contacts/: closed unless both switches are on, closed to a
staff user without access, never carrying a full number outside `open`."""
import ast
import json
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing import staff as billing_staff
from apps.inbox import bulk_staff, contacts, contacts_staff
from apps.inbox import staff as inbox_staff
from apps.inbox.models import Contact, InboxAccess
from apps.inbox.tests.base import inbox_on
from apps.inbox.tests.contacts_base import without_ids
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff

BASE = "/staff/v1/"
PATHS = tuple(contacts_staff.ROUTES)
FULL = "+639170000101"


@on_staff
@inbox_on
@override_settings(SMS_CONTACTS_ENABLED=True)
class ContactsRouteCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()
        self.operator = self.identity.user

    def grant(self, can_send=True):
        return InboxAccess.objects.create(
            operator=self.operator, platform="sms", can_send=can_send, granted_by=self.operator
        )

    def post(self, path, data=None, **kwargs):
        return self.call("POST", BASE + "contacts/" + path, data, **kwargs)

    def route(self, path, data=None, **kwargs):
        """A route by its full registered path, as the gate tests list them."""
        return self.call("POST", BASE + path, data, **kwargs)

    def open_session(self, can_send=True):
        self.grant(can_send)
        self.staff_sign_in()


class GateTests(ContactsRouteCase):
    def test_1_twelve_routes_registered_and_none_takes_another_routes_place(self):
        self.assertEqual(len(PATHS), 12)
        others = set(inbox_staff.ROUTES) | set(billing_staff.ROUTES) | set(bulk_staff.ROUTES) | {
            "sign-in/", "sign-in/code/", "sign-in/verify/", "sign-out/", "me/", "console/",
        }
        for path in PATHS:
            self.assertTrue(path.startswith("contacts/"))
            self.assertRegex(path, r"^[a-z/]+$")
            self.assertEqual(staff_views.ROUTES[path][0], "POST")
            self.assertNotIn(path, others)
        self.assertIs(staff_views.ROUTES["me/"][1], staff_views.me)

    def test_2_the_module_imports_nothing_from_the_staff_routes(self):
        with open(contacts_staff.__file__) as source:
            tree = ast.parse(source.read())
        imported = [
            node.module if isinstance(node, ast.ImportFrom) else name.name
            for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in getattr(node, "names", [None])
        ]
        self.assertEqual([m for m in imported if m and m.startswith("apps.operators")], [])

    def test_3_every_route_is_closed_while_either_switch_is_off(self):
        self.open_session()
        for setting in ({"SMS_CONTACTS_ENABLED": False}, {"SMS_INBOX_ENABLED": False}):
            with override_settings(**setting):
                for path in PATHS:
                    with self.subTest(path=path, setting=setting), CaptureQueriesContext(connection) as queries:
                        response = self.route(path, {"csv": "a,b,0917"})
                        self.assertError(response, 404, "not_found")
                        self.assertEqual(response["Cache-Control"], "no-store")
                    self.assertEqual([q["sql"] for q in queries if "inbox_contact" in q["sql"]], [])

    def test_4_every_route_is_closed_while_the_table_is_missing(self):
        self.open_session()
        with mock.patch("apps.inbox.contacts.contacts_tables_exist", return_value=False):
            for path in PATHS:
                self.assertError(self.route(path), 404, "not_found")

    def test_5_no_session_and_a_first_step_session_get_401(self):
        for path in PATHS:
            self.assertError(self.route(path), 401, "unauthenticated")
        self.staff_email_step()
        for path in PATHS:
            self.assertError(self.route(path), 401, "second_step_required")

    def test_6_no_access_row_is_not_found(self):
        self.staff_sign_in()
        for path in PATHS:
            self.assertError(self.route(path, {"first_name": "A", "mobile": FULL}), 404, "not_found")
        self.assertEqual(Contact.objects.count(), 0)

    def test_7_another_origin_is_refused_and_a_get_is_not_allowed(self):
        self.open_session()
        for origin in ("https://dash.example.test", None):
            for path in PATHS:
                response = self.route(path, origin=origin)
                self.assertError(response, 403, "origin_not_allowed")
        for path in PATHS:
            self.assertError(self.call("GET", BASE + path), 405, "method_not_allowed")


class RouteTests(ContactsRouteCase):
    def setUp(self):
        super().setUp()
        self.open_session()
        self.seen = []

    def post(self, path, data=None, **kwargs):
        response = super().post(path, data, **kwargs)
        self.seen.append(response.content.decode())
        return response

    def add(self, **extra):
        return self.post("add/", {"first_name": "Ana", "last_name": "Sample", "mobile": "0917 000 0101", **extra})

    def test_1_status(self):
        self.assertEqual(self.post("status/").json(), {"read": True, "change": True})
        InboxAccess.objects.update(can_send=False)
        self.assertEqual(self.post("status/").json(), {"read": True, "change": False})

    def test_2_the_whole_round_and_only_open_shows_the_full_number(self):
        group = self.post("groups/add/", {"name": "Resellers"})
        self.assertEqual(group.status_code, 201)
        gid = group.json()["id"]
        added = self.add(groups=[gid])
        self.assertEqual(added.status_code, 201)
        cid = added.json()["id"]
        listed = self.post("list/", {}).json()
        self.assertEqual([r["id"] for r in listed["results"]], [cid])
        self.assertEqual(listed["next"], None)
        self.assertEqual(self.post("list/", {"group": gid, "query": "Sam"}).json()["results"][0]["id"], cid)
        edited = self.post("edit/", {"contact": cid, "first_name": "Anna", "mobile": FULL, "groups": [gid]})
        self.assertEqual((edited.status_code, edited.json()["first_name"]), (200, "Anna"))
        self.assertEqual(self.post("groups/list/").json()["results"], [{"id": gid, "name": "Resellers", "count": 1}])
        self.assertEqual(self.post("groups/rename/", {"group": gid, "name": "Dealers"}).json()["name"], "Dealers")
        before = len(self.seen)
        opened = self.post("open/", {"contact": cid})
        self.assertEqual(opened.json()["mobile"], FULL)
        self.assertEqual(self.post("delete/", {"contact": cid}).status_code, 204)
        self.assertEqual(self.post("groups/remove/", {"group": gid}).status_code, 204)
        for body in self.seen[:before]:
            self.assertNotIn("917000", without_ids(body))
            self.assertNotIn(FULL, without_ids(body))
        self.assertEqual(Contact.objects.count(), 0)

    def test_hq_keeps_its_rules_for_nameless_lines_and_edits(self):
        gid = self.post("groups/add/", {"name": "G"}).json()["id"]
        text = ",Q,09170000231\nAna,S,09170000232\n"
        checked = self.post("upload/check/", {"group": gid, "csv": text}).json()
        self.assertEqual((checked["new"], checked["refused_count"]), (2, 0))
        nameless = Contact.objects.create(owner_kind="hq", mobile="+639170000233", created_by=self.operator)
        done = contacts.edit_contact(contacts.contact_scope(self.operator), nameless.public_id, {"last_name": "Q"}, partial=True)
        self.assertEqual(done["last_name"], "Q")

    def test_3_the_errors(self):
        self.add()
        duplicate = self.add(first_name="Ben")
        self.assertError(duplicate, 409, "already_exists")
        self.assertIn("contact", duplicate.json()["error"]["fields"])
        self.assertError(self.post("add/", {"first_name": "", "mobile": "1"}), 400, "invalid_request")
        self.assertError(self.post("open/", {"contact": "cont_" + "0" * 20}), 404, "not_found")
        self.assertError(self.post("edit/", {"contact": "x", "first_name": "A", "mobile": FULL}), 404, "not_found")
        self.assertError(self.post("delete/", {}), 404, "not_found")
        self.assertError(self.post("list/", {"cursor": 5}), 400, "invalid_cursor")
        self.assertError(self.post("list/", {"limit": "x"}), 400, "invalid_request")
        self.assertError(self.post("list/", {"group": 5}), 400, "invalid_request")
        self.assertError(self.post("groups/remove/", {"group": "x"}), 404, "not_found")

    def test_4_every_answer_says_no_store(self):
        cid = self.add().json()["id"]
        for path, data in [
            ("status/", {}), ("list/", {}), ("open/", {"contact": cid}), ("open/", {"contact": "x"}),
            ("delete/", {"contact": cid}), ("add/", {}), ("groups/list/", {}), ("upload/check/", {}),
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.post(path, data)["Cache-Control"], "no-store")

    def test_5_a_read_only_user_reads_and_cannot_change(self):
        cid = self.add().json()["id"]
        gid = self.post("groups/add/", {"name": "G"}).json()["id"]
        InboxAccess.objects.update(can_send=False)
        for path, data in [
            ("status/", {}), ("list/", {}), ("open/", {"contact": cid}), ("groups/list/", {}),
        ]:
            self.assertEqual(self.post(path, data).status_code, 200, path)
        changes = [
            ("add/", {"first_name": "B", "mobile": "09170000102"}), ("edit/", {"contact": cid, "first_name": "B", "mobile": FULL}),
            ("delete/", {"contact": cid}), ("groups/add/", {"name": "H"}), ("groups/rename/", {"group": gid, "name": "I"}),
            ("groups/remove/", {"group": gid}), ("upload/check/", {"group": gid, "csv": "a,b,09170000103"}),
            ("upload/", {"group": gid, "csv": "a,b,09170000103", "expected_new": 1}),
        ]
        for path, data in changes:
            with self.subTest(path=path):
                self.assertError(self.post(path, data), 403, "forbidden")
        self.assertEqual(Contact.objects.count(), 1)

    def test_6_the_upload_through_the_routes(self):
        gid = self.post("groups/add/", {"name": "G"}).json()["id"]
        text = "First,Last,Mobile\nAna,S,09170000101\nBen,E,09170000102\nBad,Line,12\n"
        checked = self.post("upload/check/", {"group": gid, "csv": text})
        self.assertEqual(checked.status_code, 200)
        self.assertEqual((checked.json()["new"], checked.json()["refused_count"]), (2, 1))
        for name in ("Ana", "Ben", "Bad", "0101"):
            self.assertNotIn(name, without_ids(checked.content.decode()))
        saved = self.post("upload/", {"group": gid, "csv": text, "expected_new": 2})
        self.assertEqual((saved.status_code, saved.json()["added"]), (201, 2))
        self.assertError(self.post("upload/", {"group": gid, "csv": text, "expected_new": 2}), 409, "changed")
        self.assertError(self.post("upload/check/", {"group": gid, "csv": "x" * 1_048_577}), 413, "file_too_large")

    def test_7_only_the_upload_routes_take_a_large_body(self):
        big = "x" * 10_000
        for path in PATHS:
            if path.startswith("contacts/upload"):
                continue
            with self.subTest(path=path):
                response = self.post(path[len("contacts/"):], {"name": big, "first_name": big, "query": big})
                self.assertNotIn(response.status_code, (201,))
                self.assertEqual(Contact.objects.count(), 0)
        gid = self.post("groups/add/", {"name": "G"}).json()["id"]
        text = "Ana,S,09170000101\n" * 10_000  # 180 KB, over the small limit
        self.assertEqual(self.post("upload/check/", {"group": gid, "csv": text}).status_code, 200)

    def test_8_a_body_that_is_not_an_object_is_read_as_empty(self):
        response = self.call("POST", BASE + "contacts/list/", [1, 2])
        self.assertEqual(response.status_code, 200)

    def test_9_no_audit_entry_or_answer_holds_a_name(self):
        self.add()
        self.post("list/", {"query": "0917 000 0101"})
        text = without_ids(json.dumps([[e.detail, e.personal, e.target_id, e.reason] for e in OperatorAuditEntry.objects.all()]))
        for value in ("Ana", "Sample", "0101", "917"):
            self.assertNotIn(value, text)
