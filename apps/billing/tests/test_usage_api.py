"""`GET sites/{site_id}/billing/usage/` (Billing Build B4, PR 3)."""
from unittest import mock

from django.test import override_settings

from apps.billing import plan, services, usage
from apps.billing.models import Subscription
from apps.billing.tests.base import SECOND, days, method_on_file
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

GB = plan.GB_BYTES


@override_settings(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True)
class UsageReadTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.subscription = Subscription.objects.get(site=self.site)
        self.path = f"/v1/sites/{self.site_id}/billing/usage/"

    def pay(self):
        with method_on_file():
            services.first_payment_succeeded(
                self.subscription, paid_at=self.subscription.trial_end_at, reference="first"
            )
        return self.subscription.cycles.get(sequence=1)

    def at(self, moment):
        patcher = mock.patch("apps.billing.api.timezone")
        patcher.start().now.return_value = moment
        self.addCleanup(patcher.stop)

    def test_signed_out_and_switch_off_and_foreign_site(self):
        self.assertEqual(self.client_class().get(self.path, secure=True).status_code, 401)
        with override_settings(BILLING_USAGE_ENABLED=False):
            self.error(self.get(self.path), 404, "not_found")
        ben = self.other_client()
        self.error(self.get(self.path, client=ben), 404, "not_found")

    def test_a_trial_is_not_billable_and_every_figure_is_zero(self):
        body = self.get(self.path).json()
        self.assertFalse(body["billable"])
        self.assertIsNone(body["period"])
        self.assertEqual(body["due_centavos"], 0)
        self.assertEqual(
            body["storage"],
            {"metered": True, "included_gb": 5, "last_snapshot": None, "held": 0, "pending_review": 0},
        )

    def test_a_paid_cycle_shows_its_period_and_the_frozen_snapshot(self):
        cycle = self.pay()
        self.at(cycle.start_at + days(2))
        body = self.get(self.path).json()
        self.assertTrue(body["billable"])
        self.assertEqual((body["cycle"]["sequence"], body["period"]["index"]), (1, 1))
        usage.record_storage_snapshot(
            self.site, source_reference="s1", period_end_at=cycle.end_at,
            measured_bytes=7 * GB, measured_at=cycle.end_at, now=cycle.end_at + SECOND,
        )
        self.at(cycle.end_at + days(1))
        body = self.get(self.path).json()
        self.assertEqual(body["due_centavos"], 6000)
        self.assertEqual(
            body["storage"]["last_snapshot"],
            {"period_end_at": body["storage"]["last_snapshot"]["period_end_at"],
             "measured_bytes": 7 * GB, "billed_gb": 2, "amount_centavos": 6000},
        )
        self.assertNotIn("s1", str(body))  # no source reference leaks

    def test_held_and_review_rows_are_counts_never_charges(self):
        cycle = self.pay()
        usage.record_storage_snapshot(
            self.site, source_reference="late", period_end_at=cycle.end_at, measured_bytes=7 * GB,
            measured_at=cycle.end_at, now=cycle.end_at + days(4),
        )
        self.at(cycle.end_at + days(5))
        body = self.get(self.path).json()
        self.assertEqual((body["storage"]["pending_review"], body["due_centavos"]), (1, 0))
