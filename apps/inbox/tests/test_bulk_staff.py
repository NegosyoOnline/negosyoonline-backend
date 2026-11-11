"""The seven routes under /staff/v1/bulk/: closed unless both switches are on and the user has the
send and bulk permissions, never carrying a full number outside the left-out file."""
import json
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing import staff as billing_staff
from apps.inbox import bulk_staff, contacts_staff
from apps.inbox import staff as inbox_staff
from apps.inbox.models import BulkBatch, InboxAccess
from apps.inbox.tests.base import inbox_on
from apps.notifications.tests.base import gateway_on, online_phone, set_policy
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff

BASE = "/staff/v1/"
PATHS = tuple(bulk_staff.ROUTES)
TEXT = "Our shop opens Monday"
N1, N2, N3 = "+639170000401", "+639170000402", "+639170000403"


@on_staff
@inbox_on
@gateway_on
@override_settings(SMS_BULK_ENABLED=True, SMS_CONTACTS_ENABLED=True)
class BulkRouteCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()
        self.operator = self.identity.user
        self.phone = online_phone(forwards_incoming=True)
        self.seen = []

    def grant(self, can_send=True, can_bulk=True):
        return InboxAccess.objects.create(
            operator=self.operator, platform="sms", can_send=can_send, can_bulk=can_bulk, granted_by=self.operator
        )

    def route(self, path, data=None, **kwargs):
        response = self.call("POST", BASE + path, data, **kwargs)
        self.seen.append(response.content.decode())
        return response

    def post(self, path, data=None, **kwargs):
        return self.route("bulk/" + path, data, **kwargs)

    def open_session(self, **kwargs):
        self.grant(**kwargs)
        self.staff_sign_in()

    def source(self, *numbers, **extra):
        return {"text": TEXT, "source": "pasted", "numbers": "\n".join(numbers), **extra}


class GateTests(BulkRouteCase):
    def test_1_seven_routes_registered_and_none_takes_another_routes_place(self):
        self.assertEqual(len(PATHS), 7)
        others = set(inbox_staff.ROUTES) | set(billing_staff.ROUTES) | set(contacts_staff.ROUTES) | {
            "sign-in/", "sign-in/code/", "sign-in/verify/", "sign-out/", "me/", "console/",
        }
        for path in PATHS:
            self.assertTrue(path.startswith("bulk/"))
            self.assertRegex(path, r"^[a-z/-]+$")
            self.assertEqual(staff_views.ROUTES[path][0], "POST")
            self.assertNotIn(path, others)

    def test_2_closed_with_a_switch_off_or_tables_missing(self):
        self.open_session()
        cases = [
            override_settings(SMS_BULK_ENABLED=False), override_settings(SMS_INBOX_ENABLED=False),
        ]
        for case in cases:
            with case:
                for path in PATHS:
                    with self.subTest(path=path), CaptureQueriesContext(connection) as queries:
                        response = self.post(path[len("bulk/"):], self.source(N1))
                        self.assertError(response, 404, "not_found")
                        self.assertEqual(response["Cache-Control"], "no-store")
                    self.assertEqual([q["sql"] for q in queries if "inbox_bulk" in q["sql"]], [])
        with mock.patch("apps.inbox.bulk.bulk_tables_exist", return_value=False):
            for path in PATHS:
                self.assertError(self.post(path[len("bulk/"):]), 404, "not_found")
            self.assertEqual(self.route("inbox/status/", {"platform": "sms"}).status_code, 200)

    def test_3_closed_without_the_permissions(self):
        self.staff_sign_in()
        for path in PATHS:  # no access row
            self.assertError(self.route(path, self.source(N1)), 404, "not_found")
        for kwargs in ({"can_send": False, "can_bulk": False}, {"can_send": True, "can_bulk": False}):
            InboxAccess.objects.all().delete()
            self.grant(**kwargs)
            for path in PATHS:
                with self.subTest(path=path, kwargs=kwargs):
                    response = self.route(path, self.source(N1))
                    self.assertError(response, 404, "not_found")
                    self.assertEqual(response["Cache-Control"], "no-store")

    def test_4_no_session_and_first_step_session_get_401(self):
        for path in PATHS:
            self.assertError(self.route(path), 401, "unauthenticated")
        self.staff_email_step()
        for path in PATHS:
            self.assertError(self.route(path), 401, "second_step_required")

    def test_5_another_origin_is_refused_and_a_get_is_not_allowed(self):
        self.open_session()
        for origin in ("https://dash.example.test", None):
            for path in PATHS:
                self.assertError(self.route(path, origin=origin), 403, "origin_not_allowed")
        for path in PATHS:
            self.assertError(self.call("GET", BASE + path), 405, "method_not_allowed")


class RouteTests(BulkRouteCase):
    def setUp(self):
        super().setUp()
        self.open_session()

    def test_1_status(self):
        answer = self.post("status/").json()
        self.assertEqual(
            answer,
            {"bulk": True, "running": False, "used_today": 0, "left_today": 1700, "reserved_for_codes": 300,
             "opt_out_line": " Reply STOP to opt out", "text_max": 138},
        )

    def test_2_the_whole_round_and_no_answer_but_left_out_holds_a_full_number(self):
        checked = self.post("check/", self.source(N1, N2, N3, "bad")).json()
        self.assertEqual((checked["size"], checked["refused_count"], checked["blocked"]), (3, 1, ""))
        confirmed = self.post("confirm/", self.source(N1, N2, N3, count=3, request_id="request-id-route-0001"))
        self.assertEqual(confirmed.status_code, 201)
        batch = confirmed.json()["batch"]
        repeat = self.post("confirm/", self.source(N1, N2, N3, count=3, request_id="request-id-route-0001"))
        self.assertEqual((repeat.status_code, repeat.json()["batch"]), (200, batch))
        listed = self.post("list/", {}).json()
        self.assertEqual([r["batch"] for r in listed["results"]], [batch])
        opened = self.post("open/", {"batch": batch}).json()
        self.assertEqual([r["state"] for r in opened["results"]], ["waiting"] * 3)
        self.assertEqual(self.route("inbox/status/", {"platform": "sms"}).json()["bulk_running"], True)
        self.assertEqual(self.post("stop/", {"batch": batch}).json()["state"], "stopped")
        self.assertEqual(self.route("inbox/status/", {"platform": "sms"}).json()["bulk_running"], False)
        for body in self.seen:
            self.assertNotIn(N1, body)
            self.assertNotIn(TEXT, body)
            self.assertNotIn("917000040", body.replace("+63917***", ""))

    def test_3_left_out_is_the_only_answer_with_full_numbers(self):
        set_policy(platform_daily_limit=301)
        response = self.post("left-out/", self.source(N1, N2))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.content.decode(), f"{N2}\n")
        entry = OperatorAuditEntry.objects.get(action="sms_inbox_bulk_left_out")
        self.assertNotIn(N2, json.dumps(entry.detail))
        set_policy(platform_daily_limit=2000)
        from django.core.cache import cache
        cache.clear()
        self.assertError(self.post("left-out/", self.source(N1)), 409, "nothing_left_out")

    def test_4_the_errors(self):
        self.assertError(self.post("check/", {"text": TEXT, "source": "nope"}), 400, "invalid_request")
        self.assertError(self.post("check/", {"text": "x" * 139, "source": "pasted", "numbers": N1}), 400, "invalid_text")
        self.assertError(self.post("confirm/", self.source(N1, count=2, request_id="request-id-route-0002")), 409, "changed")
        self.assertError(self.post("open/", {"batch": "x"}), 404, "not_found")
        self.assertError(self.post("stop/", {}), 404, "not_found")
        self.assertError(self.post("list/", {"cursor": 5}), 400, "invalid_cursor")
        self.assertError(self.post("list/", {"limit": "x"}), 400, "invalid_request")
        self.assertError(self.post("open/", {"batch": "bulk_" + "0" * 20, "cursor": "x"}), 404, "not_found")

    def test_5_every_answer_says_no_store(self):
        for path, data in [
            ("status/", {}), ("list/", {}), ("check/", self.source(N1)), ("open/", {"batch": "x"}),
            ("stop/", {}), ("confirm/", {}), ("left-out/", self.source(N1)),
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.post(path, data)["Cache-Control"], "no-store")

    def test_6_a_body_over_the_limit_is_read_as_empty_except_for_the_source_routes(self):
        big = "x" * 5000
        self.assertError(self.post("open/", {"batch": "bulk_" + "0" * 20, "pad": big}), 404, "not_found")
        # the same size is fine where a source is expected
        self.assertEqual(self.post("check/", self.source(N1, pad=big)).status_code, 200)
        self.assertError(self.post("stop/", {"batch": "x", "pad": big}), 404, "not_found")

    def test_7_open_pages_with_a_valid_cursor_and_refuses_a_bad_one(self):
        numbers = [f"+6391700005{i:02d}" for i in range(5)]
        batch = self.post(
            "confirm/", self.source(*numbers, count=5, request_id="request-id-route-0003")
        ).json()["batch"]
        first = self.post("open/", {"batch": batch, "limit": 2}).json()
        self.assertEqual((len(first["results"]), first["next"]), (2, "1"))
        second = self.post("open/", {"batch": batch, "limit": 2, "cursor": first["next"]}).json()
        self.assertEqual((len(second["results"]), second["next"]), (2, "3"))
        last = self.post("open/", {"batch": batch, "limit": 2, "cursor": second["next"]}).json()
        self.assertEqual((len(last["results"]), last["next"]), (1, None))
        self.assertError(self.post("open/", {"batch": batch, "cursor": "abc"}), 400, "invalid_cursor")
        self.assertEqual(BulkBatch.objects.count(), 1)
