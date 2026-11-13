"""Usage stays off at launch (Coach Ed, 2026-11-13: "there will be no extra charge during lunch.
we will turn this on later"): BILLING_USAGE_ENABLED alone can never bill or show a usage amount
while `plan.STORAGE_METERED` is False."""
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.billing import plan, usage
from apps.billing.models import Subscription, UsageEntry
from apps.billing.tests.base import CYCLE_1_END, SECOND, days, make_active
from apps.billing.tests.test_usage_review import AT, BASE, PATHS, ReviewTestCase, late
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

GB = plan.GB_BYTES


class GuardTests(SimpleTestCase):
    def test_the_switch_alone_is_not_enough(self):
        self.assertFalse(plan.STORAGE_METERED)  # the launch value
        with override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True):
            self.assertFalse(plan.usage_enabled())
            with mock.patch.object(plan, "STORAGE_METERED", True):
                self.assertTrue(plan.usage_enabled())
        with mock.patch.object(plan, "STORAGE_METERED", True):
            with override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=False):
                self.assertFalse(plan.usage_enabled())
            with override_settings(BILLING_ENABLED=False, BILLING_USAGE_ENABLED=True):
                self.assertFalse(plan.usage_enabled())


@override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True)
class OwnerAndDueTests(SitesApiTestCase):
    """The switch is on and storage is not metered."""

    def setUp(self):
        super().setUp()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.subscription = Subscription.objects.get(site=self.site)

    def test_due_is_zero_even_with_an_open_charge_line(self):
        with mock.patch.object(plan, "STORAGE_METERED", True):  # a line that exists from a test
            from apps.billing import services
            from apps.billing.tests.base import method_on_file
            with method_on_file():
                services.first_payment_succeeded(
                    self.subscription, paid_at=self.subscription.trial_end_at, reference="first"
                )
            cycle = self.subscription.cycles.get(sequence=1)
            result = usage.record_storage_snapshot(
                self.site, source_reference="s1", period_end_at=cycle.end_at,
                measured_bytes=7 * GB, measured_at=cycle.end_at, now=cycle.end_at + SECOND,
            )
            self.assertEqual(usage.due(self.subscription, cycle.end_at + days(1)).centavos, 6000)
        self.assertEqual(result.status, UsageEntry.Status.COUNTED)
        self.assertEqual(usage.due(self.subscription, cycle.end_at + days(1)).centavos, 0)

    def test_the_owner_usage_route_answers_404(self):
        self.error(self.get(f"/v1/sites/{self.site_id}/billing/usage/"), 404, "not_found")

    def test_a_snapshot_is_not_charged(self):
        result = usage.record_storage_snapshot(
            self.site, source_reference="s1", period_end_at=CYCLE_1_END,
            measured_bytes=7 * GB, measured_at=CYCLE_1_END, now=CYCLE_1_END + SECOND,
        )
        self.assertEqual(result.status, "not_metered")
        self.assertFalse(UsageEntry.objects.exists())


class StaffUsageTests(ReviewTestCase):
    """The review routes with the switch on and storage not metered (the setUp patches metering
    on for the review tests; this class switches it off again)."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(plan, "STORAGE_METERED", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_staff_usage_route_answers_404(self):
        subscription = make_active()
        with mock.patch.object(plan, "STORAGE_METERED", True):
            late(subscription)  # a row waiting for review, made while metering was on
        self.staff_sign_in()
        for path in PATHS:
            with self.subTest(path=path):
                self.assertError(
                    self.review(path, {"entry": "late-1", "reason": "Proof of upload time seen"}),
                    404, "not_found",
                )

    def test_admit_cannot_make_a_charge(self):
        subscription = make_active()
        with mock.patch.object(plan, "STORAGE_METERED", True):
            late(subscription)
        self.staff_sign_in()
        with mock.patch.object(usage, "make_charge") as make_charge:
            response = self.review("admit/", {"entry": "late-1", "reason": "Proof of upload time seen"})
        self.assertError(response, 404, "not_found")
        make_charge.assert_not_called()
        self.assertEqual(UsageEntry.objects.get().status, "needs_review")
