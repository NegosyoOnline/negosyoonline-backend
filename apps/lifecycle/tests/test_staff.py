"""The Lifecycle staff route under /staff/v1/lifecycle/: read only, closed while
SITE_ERASURE_ENABLED is off, staff only, and no subscriber route (plan z8uq9mb22v, sections 8, 9)."""
from unittest import mock

from django.apps import apps
from django.test import override_settings
from django.urls import get_resolver

from apps.lifecycle import dry_run, staff
from apps.lifecycle.tests.base import NOW, ended_site
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff

PATH = "/staff/v1/lifecycle/erasure/list/"


def every_row():
    return {m._meta.label: list(m._base_manager.order_by("pk").values()) for m in apps.get_models()}


def url_patterns(patterns):
    for pattern in patterns:
        yield str(pattern.pattern)
        if hasattr(pattern, "url_patterns"):
            yield from url_patterns(pattern.url_patterns)


@on_staff
@override_settings(BILLING_ENABLED=True)
class LifecycleRouteTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()
        self.sub = ended_site()
        clock = mock.patch.object(staff.timezone, "now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def test_the_route_is_in_the_table_as_a_post(self):
        self.assertIn("lifecycle/erasure/list/", staff.ROUTES)
        self.assertEqual(staff_views.ROUTES["lifecycle/erasure/list/"][0], "POST")

    def test_another_origin_is_refused(self):
        response = self.call("POST", PATH, origin="https://evil.example")
        self.assertError(response, 403, "origin_not_allowed")

    def test_no_session_and_a_session_before_the_second_step_are_refused(self):
        with override_settings(SITE_ERASURE_ENABLED=True):
            self.assertError(self.call("POST", PATH), 401, "unauthenticated")
            self.staff_email_step()
            self.assertError(self.call("POST", PATH), 401, "second_step_required")

    def test_switched_off_the_route_is_404_and_reads_nothing(self):
        self.staff_sign_in()
        with mock.patch.object(dry_run, "erasure_dry_run") as run:
            self.assertError(self.call("POST", PATH), 404, "not_found")
        run.assert_not_called()

    @override_settings(SITE_ERASURE_ENABLED=True)
    def test_switched_on_it_lists_ids_dates_and_counts_and_writes_nothing(self):
        self.staff_sign_in()
        # Sign-in wrote its own session and audit rows; compare around the call only.
        audit_before, rows_before = OperatorAuditEntry.objects.count(), every_row()
        response = self.call("POST", PATH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        body = response.json()
        self.assertTrue(body["lifecycle_checked"])
        self.assertEqual([e["site"] for e in body["eligible"]], [self.sub.site.public_id])
        self.assertNotIn(self.sub.site.account.email, response.content.decode())
        self.assertEqual(every_row(), rows_before)
        self.assertEqual(OperatorAuditEntry.objects.count(), audit_before)


class IsolationTests(StaffTestCase):
    def test_no_subscriber_or_public_route_reaches_the_lifecycle(self):
        patterns = list(url_patterns(get_resolver().url_patterns))
        self.assertFalse([p for p in patterns if "erasure" in p or "lifecycle" in p])

    @override_settings(SITE_ERASURE_ENABLED=True)
    def test_a_subscriber_session_does_not_open_the_staff_route(self):
        # The staff route reads the HQ session cookies only; a request without them is refused
        # as any other staff route refuses it, and an unknown path under the API host is 404.
        self.assertEqual(self.client.post("/api/v1/lifecycle/erasure/list/", secure=True).status_code, 404)
        with on_staff:
            self.assertError(self.call("POST", PATH), 401, "unauthenticated")
