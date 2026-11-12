"""The operator review of storage snapshots (Billing Build B4, PR 3): the Finance routes under
/staff/v1/finance/usage/review/ and the functions behind them."""
from unittest import mock

from django.test import override_settings

from apps.billing import plan, services, staff, usage
from apps.billing.models import ChargeLine, UsageEntry, UsageEvent
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, T0, days, fresh, make_active, make_attention,
)
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff

GB = plan.GB_BYTES
BASE = "/staff/v1/finance/usage/review/"
PATHS = ("list/", "detail/", "admit/", "dismiss/")
AT = CYCLE_1_END + days(10)


def late(subscription, ref="late-1", at=CYCLE_1_END, size=7 * GB):
    """A snapshot that arrives 4 days after its period end: it needs review."""
    return usage.record_storage_snapshot(
        subscription.site, source_reference=ref, period_end_at=at, measured_bytes=size,
        measured_at=at, now=at + days(4),
    )


@on_staff
@override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True)
class ReviewTestCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        for patcher in (
            mock.patch.object(plan, "STORAGE_METERED", True),
            mock.patch.object(staff, "_now", return_value=AT),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.identity = self.enrolled()
        self.operator = self.identity.user

    def review(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def audits(self, action):
        return list(OperatorAuditEntry.objects.filter(action=action))


class ReviewGateTests(ReviewTestCase):
    def test_signed_out_wrong_origin_and_switch_off(self):
        subscription = make_active()
        late(subscription)
        for path in PATHS:
            self.assertError(self.review(path, {"entry": "late-1"}), 401, "unauthenticated")
        self.staff_sign_in()
        for path in PATHS:
            self.assertError(
                self.review(path, {"entry": "late-1"}, origin="https://evil.example"),
                403, "origin_not_allowed",
            )
        with override_settings(BILLING_USAGE_ENABLED=False):
            for path in PATHS:
                self.assertError(
                    self.review(path, {"entry": "late-1", "reason": "x"}), 404, "not_found"
                )
        self.assertEqual(UsageEntry.objects.get().status, "needs_review")

    def test_the_routes_are_post_only_and_named_in_the_registry(self):
        from apps.operators import staff_views
        for path in PATHS:
            self.assertEqual(staff_views.ROUTES[f"finance/usage/review/{path}"][0], "POST")
        self.staff_sign_in()
        self.assertError(self.call("GET", BASE + "list/"), 405, "method_not_allowed")


class ReviewTests(ReviewTestCase):
    def test_the_list_holds_the_rows_waiting_oldest_first_without_personal_data(self):
        first, second = make_active(), make_active(email="b@example.test", name="b-store")
        late(first, "one")
        late(second, "two")
        self.staff_sign_in()
        body = self.review("list/").json()
        self.assertEqual([item["entry"] for item in body["items"]], ["one", "two"])
        self.assertEqual(body["needs_review_count"], 2)
        item = body["items"][0]
        self.assertEqual(
            (item["status"], item["reason"], item["measured_bytes"]),
            ("needs_review", "late_over_72h", 7 * GB),
        )
        self.assertNotIn("@", str(body))
        page = self.review("list/", {"limit": 1}).json()
        self.assertEqual(page["next_cursor"], "one")
        self.assertEqual(
            [i["entry"] for i in self.review("list/", {"limit": 1, "cursor": "one"}).json()["items"]],
            ["two"],
        )
        self.assertError(self.review("list/", {"cursor": "nope"}), 400, "invalid_cursor")

    def test_admit_makes_one_labelled_line_and_a_second_admit_is_a_replay(self):
        subscription = make_active()
        late(subscription)
        self.staff_sign_in()
        body = self.review("admit/", {"entry": "late-1", "reason": "Proof of upload time seen"}).json()
        self.assertEqual(body["status"], "counted")
        line = ChargeLine.objects.get()
        self.assertEqual((line.amount_centavos, line.labels, line.status), (6000, ["late_admitted"], "open"))
        again = self.review("admit/", {"entry": "late-1", "reason": "Proof of upload time seen"})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(ChargeLine.objects.count(), 1)
        self.assertEqual(len(self.audits("billing_usage_admitted")), 1)
        entry = self.audits("billing_usage_admitted")[0]
        self.assertEqual(entry.reason, "Proof of upload time seen")
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual(
            list(UsageEvent.objects.order_by("pk").values_list("code", "actor")),
            [("recorded_needs_review", "intake"), ("operator_counted", "operator")],
        )

    def test_admit_needs_a_reason_and_only_a_late_row(self):
        subscription = make_active()
        late(subscription)
        self.staff_sign_in()
        self.assertError(self.review("admit/", {"entry": "late-1"}), 400, "invalid_request")
        self.assertError(self.review("admit/", {"entry": "late-1", "reason": " "}), 400, "invalid_request")
        self.assertError(self.review("admit/", {"entry": "nope", "reason": "x"}), 404, "not_found")
        entry = UsageEntry.objects.get()
        UsageEntry.objects.filter(pk=entry.pk).update(reason="after_service_end")
        self.assertError(
            self.review("admit/", {"entry": "late-1", "reason": "x"}), 409, "not_admissible"
        )
        self.assertFalse(ChargeLine.objects.exists())
        self.assertEqual(self.audits("billing_usage_admitted"), [])

    def test_admit_into_a_pending_cycle_holds_and_into_a_closed_account_writes_off(self):
        subscription = make_attention()
        cycle = subscription.cycles.order_by("-sequence").first()
        late(subscription, "p", at=cycle.end_at)
        self.staff_sign_in()
        # The pending cycle has lapsed by the time the row is decided: cycle_lapsed, no line.
        body = self.review("admit/", {"entry": "p", "reason": "ok"}).json()
        self.assertIn(body["status"], ("held", "written_off"))
        self.assertFalse(ChargeLine.objects.exists())

    def test_dismiss_writes_off_with_the_reason_kept_and_is_idempotent(self):
        subscription = make_active()
        late(subscription)
        self.staff_sign_in()
        body = self.review("dismiss/", {"entry": "late-1", "reason": "Test upload"}).json()
        self.assertEqual((body["status"], body["reason"]), ("written_off", "reviewed_not_billable"))
        self.assertEqual(self.review("dismiss/", {"entry": "late-1", "reason": "Test upload"}).status_code, 200)
        self.assertEqual(len(self.audits("billing_usage_dismissed")), 1)
        self.assertFalse(ChargeLine.objects.exists())
        self.assertEqual(
            self.review("admit/", {"entry": "late-1", "reason": "x"}).json()["error"]["code"],
            "not_under_review",
        )
        self.assertEqual(fresh(subscription).state, "active")  # never reactivates or changes service

    def test_the_detail_shows_the_history(self):
        subscription = make_active()
        late(subscription)
        self.staff_sign_in()
        body = self.review("detail/", {"entry": "late-1"}).json()
        self.assertEqual([h["code"] for h in body["history"]], ["recorded_needs_review"])
