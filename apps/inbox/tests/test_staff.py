"""The six inbox routes under /staff/v1/inbox/: closed while the inbox is off, closed to a
staff user without access, and never carrying a full number."""
import ast
import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.billing import staff as billing_staff
from apps.inbox import bulk_staff, contacts_staff, staff, storing
from apps.inbox.models import Conversation, InboxAccess
from apps.inbox.tests.base import SENDER, inbox_on
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.tests.base import gateway_on, online_phone, set_policy
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import STAFF, StaffTestCase, on_staff
from apps.sites.tests.base import make_account, make_site

BASE = "/staff/v1/"
PATHS = tuple(staff.ROUTES)
REQUEST = "request-id-0000001"
TEXT = "Yes, we are open until 6 PM."
NEW_NUMBER = "+639170000042"
FORMS = (SENDER, "9189990000", "0918999", NEW_NUMBER, "9170000042", "0917000")


@on_staff
@gateway_on
@inbox_on
class InboxRouteCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.phone = online_phone(forwards_incoming=True)
        self.identity = self.enrolled()
        self.operator = self.identity.user
        self.count = 0

    def grant(self, can_send=True, user=None):
        user = user or self.operator
        return InboxAccess.objects.create(
            operator=user, platform="sms", can_send=can_send, granted_by=self.operator
        )

    def receive(self, sender=SENDER, body="Are you open?"):
        self.count += 1
        item = {"id": f"in_{self.count}", "from": sender, "text": body}
        self.assertTrue(storing.store(self.phone, {"messages": [item]}))
        return Conversation.objects.get(messages__gateway_ref=item["id"])

    def inbox(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def sms(self, path, data=None, **kwargs):
        return self.inbox(path, {"platform": "sms", **(data or {})}, **kwargs)

    def entries(self, action):
        return list(OperatorAuditEntry.objects.filter(action=action).order_by("pk"))


class InboxGateTests(InboxRouteCase):
    def test_1_the_routes_are_registered_and_none_takes_another_routes_place(self):
        paths = sorted(staff.ROUTES)
        self.assertEqual(paths, sorted([
            "inbox/status/", "inbox/conversations/list/", "inbox/conversations/find/",
            "inbox/conversations/open/", "inbox/conversations/reply/", "inbox/compose/",
        ]))
        sign_in = {"sign-in/", "sign-in/code/", "sign-in/verify/", "sign-out/", "me/", "console/"}
        for path in paths:
            self.assertTrue(path.startswith("inbox/"))
            self.assertEqual(staff_views.ROUTES[path][0], "POST")
            self.assertNotIn(path, sign_in)
            self.assertNotIn(path, billing_staff.ROUTES)
            self.assertNotIn(path, contacts_staff.ROUTES)
            self.assertNotIn(path, bulk_staff.ROUTES)
        self.assertIs(staff_views.ROUTES["me/"][1], staff_views.me)
        self.assertEqual(
            len(staff_views.ROUTES),
            len(sign_in) + len(billing_staff.ROUTES) + len(staff.ROUTES) + len(contacts_staff.ROUTES)
            + len(bulk_staff.ROUTES),
        )

    def test_2_the_module_imports_nothing_from_the_staff_routes(self):
        with open(staff.__file__) as source:
            tree = ast.parse(source.read())
        imported = [
            f"{node.module}.{name.name}" if isinstance(node, ast.ImportFrom) else name.name
            for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in node.names
        ]
        self.assertEqual([name for name in imported if name.startswith("apps.operators")], [])

    @override_settings(SMS_INBOX_ENABLED=False)
    def test_3_every_route_is_closed_while_the_inbox_is_off(self):
        self.grant()
        self.staff_sign_in()
        for path in PATHS:
            with self.subTest(path=path), mock.patch.object(staff, "_body") as body:
                with CaptureQueriesContext(connection) as queries:
                    response = self.sms(path, {"text": TEXT})
                self.assertError(response, 404, "not_found")
                body.assert_not_called()
                touched = [q["sql"] for q in queries if "inbox_" in q["sql"] or "notifications_sms" in q["sql"]]
                self.assertEqual(touched, [])

    def test_4_no_session_and_a_first_step_session_get_401(self):
        for path in PATHS:
            self.assertError(self.sms(path), 401, "unauthenticated")
        self.staff_email_step()
        for path in PATHS:
            self.assertError(self.sms(path), 401, "second_step_required")

    def test_5_no_access_row_is_not_found(self):
        self.operator.is_superuser = True
        self.operator.save()
        self.staff_sign_in()
        self.receive()
        for path in PATHS:
            with self.subTest(path=path):
                self.assertError(self.sms(path, {"to": NEW_NUMBER, "text": TEXT, "request_id": REQUEST}), 404, "not_found")
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_6_any_other_platform_is_not_found(self):
        self.grant()
        self.staff_sign_in()
        for platform in ("messenger", "instagram", "tiktok", "email", "webchat", "fax", None, 63, ["sms"]):
            for path in PATHS:
                with self.subTest(platform=platform, path=path), CaptureQueriesContext(connection) as queries:
                    response = self.inbox(path, {"platform": platform, "text": TEXT})
                    self.assertError(response, 404, "not_found")
                self.assertEqual([q["sql"] for q in queries if "inbox_" in q["sql"]], [])
        self.assertError(self.inbox("inbox/status/"), 404, "not_found")

    def test_7_another_origin_is_refused(self):
        self.grant()
        self.staff_sign_in()
        for origin in ("https://dash.example.test", "https://www.example.test", "https://preview-1.vercel.app", None):
            for path in PATHS:
                with self.subTest(origin=origin, path=path):
                    response = self.sms(path, origin=origin)
                    self.assertError(response, 403, "origin_not_allowed")
                    self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_12_a_get_is_not_allowed_and_no_route_has_a_value_in_its_address(self):
        self.grant()
        self.staff_sign_in()
        for path in PATHS:
            self.assertError(self.call("GET", BASE + path), 405, "method_not_allowed")
            self.assertRegex(path, r"^[A-Za-z/-]+$")


class InboxRouteTests(InboxRouteCase):
    def setUp(self):
        super().setUp()
        self.grant()
        self.staff_sign_in()
        self.seen = []

    def sms(self, path, data=None, **kwargs):
        response = super().sms(path, data, **kwargs)
        self.seen.append(response.content.decode())
        return response

    def test_8_every_answer_says_no_store(self):
        conversation = self.receive()
        calls = [
            (200, "inbox/status/", {}),
            (200, "inbox/conversations/list/", {}),
            (200, "inbox/conversations/open/", {"conversation": conversation.public_id}),
            (201, "inbox/conversations/reply/", {"conversation": conversation.public_id, "text": TEXT, "request_id": REQUEST}),
            (400, "inbox/compose/", {"to": NEW_NUMBER, "text": "", "request_id": REQUEST}),
            (404, "inbox/conversations/open/", {"conversation": "conv_" + "0" * 20}),
        ]
        for status, path, data in calls:
            with self.subTest(path=path):
                response = self.sms(path, data)
                self.assertEqual(response.status_code, status, response.content)
                self.assertEqual(response["Cache-Control"], "no-store")
        InboxAccess.objects.update(can_send=False)
        response = self.sms("inbox/compose/", {"to": NEW_NUMBER, "text": TEXT, "request_id": "request-id-0000002"})
        self.assertEqual((response.status_code, response["Cache-Control"]), (403, "no-store"))
        InboxAccess.objects.update(can_send=True)
        set_policy(platform_daily_limit=300)
        with self.assertLogs("apps.notifications", level="ERROR"):
            response = self.sms("inbox/compose/", {"to": NEW_NUMBER, "text": TEXT, "request_id": "request-id-0000003"})
        self.assertEqual((response.status_code, response["Cache-Control"]), (429, "no-store"))

    def test_9_status(self):
        self.receive()
        answer = self.sms("inbox/status/").json()
        self.assertEqual(
            answer,
            {"platform": "sms", "read": True, "send": True, "phone_online": True, "used_today": 0,
             "cap": 2000, "reserved_for_codes": 300, "unread": 1, "bulk_running": False},
        )
        InboxAccess.objects.update(can_send=False)
        self.assertIs(self.sms("inbox/status/").json()["send"], False)
        with override_settings(SMS_GATEWAY_ENABLED=False):
            answer = self.sms("inbox/status/").json()
        self.assertEqual(
            (answer["phone_online"], answer["used_today"], answer["cap"], answer["reserved_for_codes"]),
            (False, None, None, None),
        )

    def test_10_list_find_and_open(self):
        conversation = self.receive()
        other = Conversation.objects.create(
            owner_kind="site", site=make_site(make_account()), platform="sms", line=self.phone,
            contact=SENDER, can_reply=True, last_message_at=timezone.now(), unread_count=2,
        )
        listed = self.sms("inbox/conversations/list/", {"state": "unread", "limit": 5}).json()
        self.assertEqual(set(listed), {"results", "next"})
        self.assertEqual([r["id"] for r in listed["results"]], [conversation.public_id])
        self.assertNotIn("Are you open", json.dumps(listed))
        self.assertEqual(OperatorAuditEntry.objects.filter(action__startswith="sms_inbox").count(), 0)
        found = self.sms("inbox/conversations/find/", {"number": "0918 999 0000"}).json()
        self.assertEqual(([r["id"] for r in found["results"]], found["next"]), ([conversation.public_id], None))
        self.assertEqual(len(self.entries("sms_inbox_find")), 1)
        opened = self.sms("inbox/conversations/open/", {"conversation": conversation.public_id}).json()
        self.assertEqual(set(opened), {"id", "platform", "contact", "can_reply", "messages", "next"})
        self.assertEqual(opened["messages"][0]["body"], "Are you open?")
        self.assertEqual(len(self.entries("sms_inbox_open")), 1)
        conversation.refresh_from_db()
        self.assertEqual(conversation.unread_count, 0)
        for bad in (other.public_id, "conv_" + "0" * 20, "x", None, 5):
            self.assertError(self.sms("inbox/conversations/open/", {"conversation": bad}), 404, "not_found")
        for data in ({"limit": "5"}, {"limit": True}, {"state": "read"}, {"cursor": "conv_" + "0" * 20}, {"cursor": 5}):
            self.assertEqual(self.sms("inbox/conversations/list/", data).status_code, 400, data)

    def test_11_reply_and_compose(self):
        conversation = self.receive()
        body = {"conversation": conversation.public_id, "text": TEXT, "request_id": REQUEST}
        first = self.sms("inbox/conversations/reply/", body)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(set(first.json()), {"conversation", "message"})
        again = self.sms("inbox/conversations/reply/", body)
        self.assertEqual((again.status_code, again.json()), (200, first.json()))
        self.assertEqual(SmsMessage.objects.count(), 1)
        composed = self.sms("inbox/compose/", {"to": NEW_NUMBER, "text": TEXT, "request_id": "request-id-0000002"})
        self.assertEqual(composed.status_code, 201)
        self.assertEqual(SmsMessage.objects.count(), 2)

        InboxAccess.objects.update(can_send=False)
        read_only = self.sms("inbox/compose/", {"to": NEW_NUMBER, "text": TEXT, "request_id": "request-id-0000003"})
        self.assertError(read_only, 403, "forbidden")
        InboxAccess.objects.update(can_send=True)

        refusals = (
            (503, "sms_paused", "Text sending is stopped. Nothing was sent.", lambda: set_policy(sending_stopped=True)),
            (503, "sms_unavailable", "The gateway phone is offline. Nothing was sent.",
             lambda: SmsDevice.objects.update(last_contact_at=timezone.now() - timedelta(minutes=5))),
            (429, "limit_reached", "Today's limit for staff texts is reached. 300 texts are kept for sign-in codes.",
             lambda: set_policy(platform_daily_limit=300)),
        )
        for number, (status, code, words, arrange) in enumerate(refusals):
            cache.clear()
            set_policy(sending_stopped=False, platform_daily_limit=2000)
            SmsDevice.objects.update(last_contact_at=timezone.now())
            arrange()
            with self.assertLogs("apps.notifications", level="ERROR"):
                response = self.sms(
                    "inbox/conversations/reply/",
                    {"conversation": conversation.public_id, "text": TEXT, "request_id": f"refused-id-00000{number}"},
                )
            self.assertError(response, status, code)
            self.assertEqual(response.json()["error"]["message"], words)

    def test_13_no_answer_holds_a_full_number(self):
        conversation = self.receive()
        self.test_9_status()
        self.sms("inbox/conversations/list/")
        self.sms("inbox/conversations/find/", {"number": SENDER})
        self.sms("inbox/conversations/open/", {"conversation": conversation.public_id})
        self.sms("inbox/conversations/reply/", {"conversation": conversation.public_id, "text": TEXT, "request_id": "request-id-0000009"})
        self.sms("inbox/compose/", {"to": NEW_NUMBER, "text": TEXT, "request_id": "request-id-0000008"})
        self.sms("inbox/compose/", {"to": "not a number", "text": TEXT, "request_id": "request-id-0000007"})
        held = "\n".join(self.seen)
        for form in FORMS:
            self.assertNotIn(form, held)
        self.assertIn("+63918***0000", held)

    def test_a_large_body_and_one_that_is_not_an_object_are_read_as_empty(self):
        for raw in ("x" * 3000, "[1, 2]", "not json"):
            response = self.client.post(
                BASE + "inbox/status/", data=raw, content_type="application/json",
                secure=True, HTTP_HOST="hq.example.test", HTTP_ORIGIN=STAFF,
            )
            self.assertError(response, 404, "not_found")
