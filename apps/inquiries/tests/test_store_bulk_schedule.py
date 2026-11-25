"""Schedule SMS: a store bulk batch with a future start time. Rules and refusals at create and edit,
cancel, the runner (recount, each blocked reason, a running batch, the three hooks), the lists,
tenancy, the switch and the audit entries. Every name and number is made up."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import AccountAuditEvent
from apps.inbox.models import Contact, ContactGroup
from apps.inquiries import store_bulk, store_bulk_schedule, tasks
from apps.inquiries.models import InquirySmsOptOut
from apps.inquiries.tests.test_sms_inbox import make_member
from apps.inquiries.tests import test_store_bulk_pick as pick
from apps.inquiries.tests.test_store_bulk import MANILA, NUMBERS, TEXT, BulkCase
from apps.notifications.models import StoreBulkBatch, StoreBulkRecipient
from apps.sites.models import Site

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=MANILA)
LATER = "2026-11-25T10:00:00+08:00"
LATER_AT = datetime(2026, 11, 25, 10, 0, tzinfo=MANILA)
AFTER = datetime(2026, 11, 25, 10, 5, tzinfo=MANILA)
BILLING = {"return_value": SimpleNamespace(billing_only=True, storefront_served=False)}


class ScheduleCase(BulkCase):
    def setUp(self):
        super().setUp()
        clock = mock.patch.object(store_bulk_schedule, "_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.sroot = self.root + "scheduled/"

    def schedule(self, key="key-sched-0001", at=LATER, text=TEXT, **extra):
        body = {"text": text, "audience": "all", "scheduled_at": at, "idempotency_key": key, **extra}
        return self.send("post", self.sroot, body)

    def make(self, **kw):
        response = self.schedule(**kw)
        self.assertEqual(response.status_code, 201, response.content)
        return StoreBulkBatch.objects.get(public_id=response.json()["id"])

    def code(self, response):
        return response.status_code, response.json()["error"]["code"]

    def run_due(self, now=AFTER):
        return store_bulk_schedule.start_due(now=now)


class CreateTests(ScheduleCase):
    def test_creates_a_scheduled_batch_with_no_recipients(self):
        response = self.schedule()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["state"], body["size"], body["text"], body["audience"]), ("scheduled", 3, TEXT, "all"))
        self.assertEqual(datetime.fromisoformat(body["scheduled_at"]), LATER_AT)
        self.assertFalse(StoreBulkRecipient.objects.exists())

    def test_a_time_without_a_timezone_is_manila_time(self):
        batch = self.make(at="2026-11-25T10:00:00")
        self.assertEqual(batch.scheduled_at, LATER_AT)

    def test_same_key_is_same_batch(self):
        first = self.schedule().json()
        again = self.schedule()
        self.assertEqual((again.status_code, again.json()["id"]), (200, first["id"]))
        self.assertEqual(StoreBulkBatch.objects.count(), 1)

    def test_ordinary_batches_have_a_null_scheduled_at(self):
        self.start()
        active = self.get(self.root + "status/").json()["active_batch"]
        self.assertIsNone(active["scheduled_at"])

    def test_the_window_is_08_to_22_and_the_end_is_not_included(self):
        for key, at in (("key-edge-0001", "2026-11-25T08:00:00+08:00"), ("key-edge-0002", "2026-11-25T20:00:00+08:00"),
                        ("key-edge-0003", "2026-11-25T21:45:00+08:00"), ("key-edge-0004", "2026-11-25T21:59:00+08:00")):
            with self.subTest(at=at):
                self.assertEqual(self.schedule(key=key, at=at).status_code, 201)
        for at in ("2026-11-25T22:00:00+08:00", "2026-11-25T07:59:00+08:00"):
            with self.subTest(at=at):
                self.assertEqual(self.code(self.schedule(at=at)), (422, "outside_window"))

    def test_time_rules(self):
        self.assertEqual(self.code(self.schedule(at="2026-11-24T09:09:00+08:00")), (422, "too_soon"))
        self.assertEqual(self.code(self.schedule(at="2026-11-25T22:00:00+08:00")), (422, "outside_window"))
        self.assertEqual(self.code(self.schedule(at="2026-11-25T07:59:00+08:00")), (422, "outside_window"))
        self.assertEqual(self.code(self.schedule(at="2026-12-25T10:00:00+08:00")), (422, "too_far"))
        self.assertEqual(self.code(self.schedule(at="soon")), (422, "invalid"))
        self.assertEqual(self.schedule(at="2026-11-24T09:10:00+08:00").status_code, 201)
        self.assertEqual(self.schedule(key="key-sched-0002", at="2026-12-24T09:00:00+08:00").status_code, 201)

    def test_text_rules_are_bulks(self):
        self.assertEqual(self.schedule(text="x" * 161).status_code, 422)
        self.assertEqual(self.schedule(text="See https://example.com").status_code, 422)
        self.assertEqual(self.schedule(text="   ").status_code, 422)

    def test_audience_rules_are_bulks(self):
        self.assertEqual(self.code(self.schedule(audience="everyone")), (400, "invalid_audience"))
        Contact.objects.filter(site=self.site).delete()
        self.assertEqual(self.code(self.schedule()), (409, "empty_audience"))

    def test_a_missing_null_or_blank_audience_is_refused_and_nothing_is_created(self):
        for extra in ({}, {"audience": None}, {"audience": ""}, {"audience": "  "}):
            body = {"text": TEXT, "scheduled_at": LATER, "idempotency_key": "key-missing-01", **extra}
            response = self.send("post", self.sroot, body)
            self.assertEqual(self.code(response), (400, "invalid_audience"), extra)
            self.assertIn("audience", response.json()["error"]["fields"], extra)
        self.assertFalse(StoreBulkBatch.objects.exists())

    def test_an_explicit_all_still_schedules(self):
        self.assertEqual(self.make(audience="all").audience, "all")

    def test_at_most_twenty_scheduled(self):
        for i in range(20):
            self.make(key=f"key-sched-{i:04d}")
        self.assertEqual(self.code(self.schedule(key="key-sched-9999")), (409, "schedule_limit"))
        self.assertEqual(StoreBulkBatch.objects.count(), 20)

    def test_a_scheduled_batch_does_not_block_an_ordinary_one(self):
        self.make()
        self.assertEqual(self.confirm().status_code, 201)

    def test_unknown_fields_and_bad_keys(self):
        self.assertEqual(self.schedule(extra=1).status_code, 400)
        self.assertEqual(self.schedule(key="short").status_code, 422)

    def test_audit_has_counts_and_no_text_or_number(self):
        batch = self.make()
        event = AccountAuditEvent.objects.get(event="store_bulk_scheduled")
        self.assertEqual((event.detail["batch"], event.detail["size"]), (batch.public_id, 3))
        for secret in (TEXT, *NUMBERS, "+639"):
            self.assertNotIn(secret, str(event.detail))


class ListAndTenancyTests(ScheduleCase):
    def test_list_is_soonest_first_and_pages(self):
        late = self.make(key="key-sched-0001", at="2026-11-27T10:00:00+08:00")
        soon = self.make(key="key-sched-0002", at="2026-11-25T10:00:00+08:00")
        mid = self.make(key="key-sched-0003", at="2026-11-26T10:00:00+08:00")
        body = self.get(self.sroot).json()
        self.assertEqual([i["id"] for i in body["items"]], [soon.public_id, mid.public_id, late.public_id])
        page = self.get(self.sroot + "?limit=2").json()
        self.assertEqual(len(page["items"]), 2)
        rest = self.get(self.sroot + f"?limit=2&cursor={page['next_cursor']}").json()
        self.assertEqual(([i["id"] for i in rest["items"]], rest["next_cursor"]), ([late.public_id], None))
        self.assertEqual(self.code(self.get(self.sroot + "?cursor=sbk_nope")), (422, "invalid_cursor"))

    def test_history_leaves_out_scheduled_and_cancelled_and_other_routes_do_not_open_them(self):
        a, b = self.make(key="key-sched-0001"), self.make(key="key-sched-0002")
        self.send("post", f"{self.sroot}{b.public_id}/cancel/")
        self.assertEqual(self.get(self.root + "batches/").json()["items"], [])
        self.assertEqual(self.get(self.root + f"batches/{a.public_id}/").status_code, 404)
        self.assertEqual(self.send("post", self.root + f"batches/{a.public_id}/stop/").status_code, 409)

    def test_get_one(self):
        batch = self.make()
        self.assertEqual(self.get(f"{self.sroot}{batch.public_id}/").json()["id"], batch.public_id)
        self.assertEqual(self.get(f"{self.sroot}sbk_nope/").status_code, 404)

    def test_an_ordinary_batch_is_not_a_scheduled_one(self):
        ordinary = self.start()
        self.assertEqual(self.get(f"{self.sroot}{ordinary.public_id}/").status_code, 404)
        self.assertEqual(self.get(self.sroot).json()["items"], [])

    def test_another_store_gets_404(self):
        batch = self.make()
        url = f"{self.ben_base}sms/bulk/scheduled/"
        for response in (
            self.get(url + f"{batch.public_id}/", client=self.ben),
            self.send("patch", url + f"{batch.public_id}/", {"text": "x"}, client=self.ben),
            self.send("post", url + f"{batch.public_id}/cancel/", client=self.ben),
        ):
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.get(self.sroot).json()["items"][0]["id"], batch.public_id)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "scheduled")

    def test_each_switch_off_makes_every_route_404(self):
        batch = self.make()
        for name in ("STORE_BULK_SMS_ENABLED", "INQUIRY_SMS_ENABLED", "SMS_GATEWAY_ENABLED", "SMS_SITE_LINES_ENABLED"):
            with self.subTest(name), override_settings(**{name: False}):
                for response in (
                    self.schedule(key="key-sched-off1"), self.get(self.sroot), self.get(f"{self.sroot}{batch.public_id}/"),
                    self.send("patch", f"{self.sroot}{batch.public_id}/", {"text": "x"}),
                    self.send("post", f"{self.sroot}{batch.public_id}/cancel/"),
                ):
                    self.assertEqual(response.status_code, 404, (name, response.content))
        self.assertEqual(StoreBulkBatch.objects.count(), 1)


class EditCancelTests(ScheduleCase):
    def test_edit_changes_text_time_and_recounts(self):
        batch = self.make()
        self.group_contact(self.site, "+639171000009", "New")
        InquirySmsOptOut.objects.create(site=self.site, mobile=NUMBERS[0])
        response = self.send("patch", f"{self.sroot}{batch.public_id}/", {"text": "New text.", "scheduled_at": "2026-11-26T11:00:00+08:00"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["text"], body["size"], body["state"]), ("New text.", 3, "scheduled"))
        self.assertEqual(body["left_out"]["opted_out"], 1)
        batch.refresh_from_db()
        self.assertEqual(batch.scheduled_at, datetime(2026, 11, 26, 11, 0, tzinfo=MANILA))
        event = AccountAuditEvent.objects.get(event="store_bulk_schedule_edited")
        self.assertNotIn("New text.", str(event.detail))

    def test_edit_applies_the_same_checks(self):
        batch = self.make()
        url = f"{self.sroot}{batch.public_id}/"
        for body, expected in (
            ({"scheduled_at": "2026-11-24T09:05:00+08:00"}, (422, "too_soon")),
            ({"scheduled_at": "2026-11-25T22:30:00+08:00"}, (422, "outside_window")),
            ({"scheduled_at": "2027-01-25T10:00:00+08:00"}, (422, "too_far")),
            ({"audience": "nobody"}, (400, "invalid_audience")),
            ({"audience": "pick"}, (400, "invalid_audience")),
        ):
            self.assertEqual(self.code(self.send("patch", url, body)), expected, body)
        self.assertEqual(self.send("patch", url, {"text": "x" * 161}).status_code, 422)
        batch.refresh_from_db()
        self.assertEqual((batch.text, batch.audience, batch.scheduled_at), (TEXT, "all", LATER_AT))

    def test_edit_to_an_empty_audience(self):
        batch = self.make()
        Contact.objects.filter(site=self.site).delete()
        self.assertEqual(self.code(self.send("patch", f"{self.sroot}{batch.public_id}/", {"text": "Hi."})), (409, "empty_audience"))

    def test_edit_with_a_blank_audience_is_refused_and_leaving_it_out_keeps_it(self):
        batch = self.make()
        url = f"{self.sroot}{batch.public_id}/"
        for blank in ("", "  "):
            response = self.send("patch", url, {"audience": blank})
            self.assertEqual(self.code(response), (400, "invalid_audience"), blank)
            self.assertIn("audience", response.json()["error"]["fields"])
        batch.refresh_from_db()
        self.assertEqual(batch.audience, "all")
        self.assertEqual(self.send("patch", url, {"text": "Hello again."}).status_code, 200)
        batch.refresh_from_db()
        self.assertEqual(batch.audience, "all")

    def test_cancel_and_a_second_cancel(self):
        batch = self.make()
        url = f"{self.sroot}{batch.public_id}/cancel/"
        first = self.send("post", url)
        self.assertEqual((first.status_code, first.json()["state"], first.json()["end_reason"]), (200, "cancelled", "cancelled"))
        self.assertEqual(self.send("post", url).json(), first.json())
        self.assertEqual(AccountAuditEvent.objects.filter(event="store_bulk_schedule_cancelled").count(), 1)
        self.assertEqual(self.code(self.send("patch", f"{self.sroot}{batch.public_id}/", {"text": "x"})), (409, "already_started"))
        self.run_due()
        batch.refresh_from_db()
        self.assertEqual(batch.state, "cancelled")  # never starts

    def test_edit_and_cancel_only_before_the_start(self):
        batch = self.make()
        self.run_due()
        batch.refresh_from_db()
        self.assertEqual(batch.state, "waiting")
        self.assertEqual(self.code(self.send("patch", f"{self.sroot}{batch.public_id}/", {"text": "x"})), (409, "already_started"))
        self.assertEqual(self.code(self.send("post", f"{self.sroot}{batch.public_id}/cancel/")), (409, "already_started"))


class RunnerTests(ScheduleCase):
    def test_not_due_does_nothing(self):
        batch = self.make()
        self.assertEqual(self.run_due(now=LATER_AT - timedelta(minutes=1)), 0)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "scheduled")

    def test_start_recounts_with_todays_exclusions(self):
        batch = self.make()
        InquirySmsOptOut.objects.create(site=self.site, mobile=NUMBERS[1])
        self.group_contact(self.site, "+639171000008", "Joined later")
        self.run_due()
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.size, batch.left_out["opted_out"]), ("waiting", 3, 1))
        self.assertEqual(batch.recipients.count(), 3)
        self.assertEqual(self.get(self.root + "status/").json()["active_batch"]["id"], batch.public_id)
        event = AccountAuditEvent.objects.get(event="store_bulk_schedule_started")
        for secret in (TEXT, *NUMBERS, "+639"):
            self.assertNotIn(secret, str(event.detail))

    def test_a_started_batch_sends_like_any_other_and_shows_in_history(self):
        batch = self.make()
        self.run_due()
        self.pump(4, now=AFTER)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent), ("finished", 3))
        self.assertEqual(self.get(self.root + "batches/").json()["items"][0]["id"], batch.public_id)
        self.assertEqual(self.get(self.root + f"batches/{batch.public_id}/").status_code, 200)

    def test_nobody_left_at_start(self):
        batch = self.make()
        Contact.objects.filter(site=self.site).delete()
        self.run_due()
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason, batch.recipients.count()), ("ended", "empty_audience", 0))
        self.assertEqual(self.get(f"{self.sroot}{batch.public_id}/").json()["end_message"], store_bulk.REASON_TEXTS["empty_audience"])

    def _blocked(self, reason, now=AFTER, patch=None, setup=None):
        StoreBulkBatch.objects.all().delete()
        Site.objects.filter(pk=self.site.pk).update(operator_hold=False)
        self.has_line.return_value = True
        batch = self.make()
        if setup:
            setup()
        if patch:
            with patch:
                self.run_due(now=now)
        else:
            self.run_due(now=now)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason), ("ended", reason))
        self.assertEqual((batch.recipients.count(), batch.not_sent), (0, batch.size))
        self.assertEqual(self.run_due(now=AFTER + timedelta(minutes=5)), 0)  # not retried
        self.assertTrue(self.get(f"{self.sroot}{batch.public_id}/").json()["end_message"], reason)

    def test_each_blocked_reason_ends_the_batch_and_is_not_retried(self):
        self._blocked("site_held", setup=lambda: Site.objects.filter(pk=self.site.pk).update(operator_hold=True))
        self._blocked("line_disabled", setup=lambda: setattr(self.has_line, "return_value", False))
        self._blocked("billing_only", patch=mock.patch("apps.billing.services.position", **BILLING))
        self._blocked("daily_cap", patch=mock.patch.object(store_bulk, "_at_cap", return_value=True))
        self._blocked("phone_offline", patch=mock.patch("apps.notifications.sms.gateway_online", return_value=False))
        self._blocked("outside_window", now=datetime(2026, 11, 25, 22, 30, tzinfo=MANILA))

    def test_due_while_another_batch_is_active_waits_then_starts_when_it_is_over(self):
        batch = self.make()
        running = self.start(key="key-batch-0002")
        self.run_due()
        batch.refresh_from_db()
        self.assertEqual(batch.state, "scheduled")  # tried again on the next run
        self.run_due(now=AFTER + timedelta(minutes=1))
        batch.refresh_from_db()
        self.assertEqual(batch.state, "scheduled")
        store_bulk.stop(self.site, self.account, running.public_id)
        self.run_due(now=AFTER + timedelta(minutes=2))
        batch.refresh_from_db()
        self.assertEqual(batch.state, "waiting")

    def test_due_while_another_is_active_and_the_window_has_ended(self):
        batch = self.make()
        self.start(key="key-batch-0002")
        self.run_due(now=datetime(2026, 11, 25, 22, 1, tzinfo=MANILA))
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason), ("ended", "batch_running"))

    def test_start_is_idempotent(self):
        batch = self.make()
        self.run_due()
        self.run_due()
        self.assertEqual(batch.recipients.count(), 3)

    def test_the_beat_task(self):
        batch = self.make()
        with mock.patch.object(store_bulk_schedule, "_now", return_value=AFTER):
            tasks.store_bulk_start_due()
        batch.refresh_from_db()
        self.assertEqual(batch.state, "waiting")

    def test_the_phones_pull_starts_a_due_batch_and_queues_its_first_text(self):
        batch = self.make()
        self.feed(now=AFTER)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "sending")
        self.assertEqual(self.records().count(), 1)

    def test_a_read_starts_a_due_batch(self):
        batch = self.make()
        with mock.patch.object(store_bulk_schedule, "_now", return_value=AFTER):
            self.get(self.root + "status/")
        batch.refresh_from_db()
        self.assertEqual(batch.state, "waiting")

    def test_nothing_runs_while_the_switch_is_off(self):
        batch = self.make()
        with override_settings(STORE_BULK_SMS_ENABLED=False):
            self.assertEqual(self.run_due(), 0)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "scheduled")


class DetailNamesTests(pick.PickCase):
    def setUp(self):
        super().setUp()
        clock = mock.patch.object(store_bulk_schedule, "_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.sroot = self.root + "scheduled/"

    def schedule(self, **body):
        body = {"text": TEXT, "scheduled_at": LATER, "idempotency_key": "key-sched-0001", **body}
        response = self.send("post", self.sroot, body)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def detail(self, batch_id):
        return self.get(f"{self.sroot}{batch_id}/")

    def test_pick_batch_names_in_picked_order_and_only_masked_numbers(self):
        a = self.add("Ana", "+639172000001")
        b = self.add("Ben", "+639172000002")
        batch_id = self.schedule(audience="pick", groups=[self.gid2, self.gid], contacts=[b, a])
        events = AccountAuditEvent.objects.count()
        response = self.detail(batch_id)
        body = response.json()
        self.assertEqual([g["name"] for g in body["groups"]], ["Others", "Regulars"])
        self.assertEqual([c["id"] for c in body["contacts"]], [b, a])
        self.assertEqual(set(body["contacts"][0]), {"id", "first_name", "last_name", "mobile_masked"})
        self.assertIn("*", body["contacts"][0]["mobile_masked"])
        for number in ("+639172000001", "+639172000002"):
            self.assertNotIn(number, response.content.decode())
        self.assertEqual(AccountAuditEvent.objects.count(), events)

    def test_all_batch_has_empty_lists_and_the_list_has_none(self):
        self.add("Ana", "+639172000003", [self.gid])  # All groups needs a grouped contact
        batch_id = self.schedule(audience="all")
        body = self.detail(batch_id).json()
        self.assertEqual((body["groups"], body["contacts"]), ([], []))

    def counted(self, url):
        with CaptureQueriesContext(connection) as ctx:
            response = self.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["items"], len(ctx)

    def row(self, key, audience, state, **picked):
        return StoreBulkBatch.objects.create(
            site=self.site, created_by=self.account, request_id=key, text=TEXT, audience=audience,
            picked=picked if audience == "pick" else {}, state=state, size=1,
            scheduled_at=LATER_AT if state == "scheduled" else None)

    def check_rows(self, items, a, other_group):
        pick_rows = [r for r in items if r["audience"] == "pick"]
        all_rows = [r for r in items if r["audience"] == "all"]
        self.assertTrue(pick_rows and all_rows)
        for row in all_rows:
            self.assertEqual((row["groups"], row["contacts"]), ([], []))
        for row in pick_rows:
            self.assertEqual([g["id"] for g in row["groups"]], [self.gid])
            self.assertEqual([c["id"] for c in row["contacts"]], [a])
            self.assertEqual(row["contacts"][0]["first_name"], "Ana")
            self.assertIn("*", row["contacts"][0]["mobile_masked"])

    def test_the_history_list_carries_names_with_constant_queries(self):
        a = self.add("Ana", "+639172000001", [self.gid])
        other = ContactGroup.objects.create(owner_kind="site", site=self.ben_site, name="Bens")
        picked = {"groups": [self.gid, other.public_id], "contacts": [a]}  # another store's group is never shown
        self.row("key-h-0001", "pick", "finished", **picked)
        self.row("key-h-0002", "all", "finished")
        items, small = self.counted(self.root + "batches/")
        self.check_rows(items, a, other)
        for i in range(3, 9):
            self.row(f"key-h-{i:04d}", "pick" if i % 2 else "all", "finished", **picked)
        items, big = self.counted(self.root + "batches/")
        self.assertEqual(len(items), 8)
        self.check_rows(items, a, other)
        self.assertEqual(small, big)
        body = str(items)
        self.assertNotIn("+639172000001", body)
        self.assertNotIn(other.public_id, body)

    def test_the_scheduled_list_carries_names_with_constant_queries(self):
        a = self.add("Ana", "+639172000001", [self.gid])
        other = ContactGroup.objects.create(owner_kind="site", site=self.ben_site, name="Bens")
        picked = {"groups": [self.gid, other.public_id], "contacts": [a]}
        self.row("key-s-0001", "pick", "scheduled", **picked)
        self.row("key-s-0002", "all", "scheduled")
        items, small = self.counted(self.sroot)
        self.check_rows(items, a, other)
        for i in range(3, 9):
            self.row(f"key-s-{i:04d}", "pick" if i % 2 else "all", "scheduled", **picked)
        items, big = self.counted(self.sroot)
        self.assertEqual(len(items), 8)
        self.check_rows(items, a, other)
        self.assertEqual(small, big)
        self.assertNotIn("+639172000001", str(items))
        self.assertNotIn(other.public_id, str(items))

    def test_a_deleted_group_or_contact_is_skipped(self):
        a = self.add("Ana", "+639172000001", [self.gid])
        b = self.add("Ben", "+639172000002")
        batch_id = self.schedule(audience="pick", groups=[self.gid, self.gid2], contacts=[a, b])
        Contact.objects.filter(public_id=b).delete()
        ContactGroup.objects.filter(public_id=self.gid2).delete()
        body = self.detail(batch_id).json()
        self.assertEqual(([g["id"] for g in body["groups"]], [c["id"] for c in body["contacts"]]), ([self.gid], [a]))

    def test_another_stores_ids_are_never_shown(self):
        a = self.add("Ana", "+639172000001")
        batch_id = self.schedule(audience="pick", contacts=[a])
        other = Contact.objects.get(public_id=a)
        other_group = ContactGroup.objects.create(owner_kind="site", site=self.ben_site, name="Bens")
        batch = StoreBulkBatch.objects.get(public_id=batch_id)
        batch.picked = {"groups": [other_group.public_id], "contacts": [a]}
        batch.save(update_fields=["picked"])
        Contact.objects.filter(pk=other.pk).update(site=self.ben_site)
        body = self.detail(batch_id).json()
        self.assertEqual((body["groups"], body["contacts"]), ([], []))
