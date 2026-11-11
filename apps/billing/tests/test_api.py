"""The billing endpoints over HTTP. The billing clock is set by each test; none reads the real one."""
from unittest import mock

from django.test import Client, override_settings
from django.utils.dateparse import parse_datetime

from apps.accounts.models import AccountAuditEvent
from apps.accounts.tests.base import ORIGIN
from apps.billing import api, plan, services
from apps.billing.models import Subscription
from apps.billing.tests.base import SECOND, days, method_on_file
from apps.billing.tests.test_transitions import snapshot
from apps.operators.models import User
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_site

State = Subscription.State
KEYS = {
    "state", "storefront_served", "billing_only", "plan", "trial_start_at", "trial_end_at",
    "current_cycle", "next_due_at", "attention_ends_at", "cancel_requested_at", "service_end_at",
    "download_until", "deletion_due_at", "pay_by", "clocks_started", "billing_exempt",
    "outstanding_centavos", "can_cancel", "can_reactivate",
}
EVENTS = ("subscription_cancelled", "subscription_cancellation_withdrawn")


@override_settings(BILLING_ENABLED=True)
class BillingApiTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.subscription = Subscription.objects.get(site=self.site)
        self.start = self.subscription.trial_start_at
        self.trial_end = self.subscription.trial_end_at
        self.path = f"/v1/sites/{self.site_id}/billing/"
        patcher = mock.patch("apps.billing.services.timezone")
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.at(self.start + days(1))

    def at(self, moment):
        self.clock.now.return_value = moment
        return moment

    def pay_first_cycle(self):
        with method_on_file():
            services.first_payment_succeeded(
                self.subscription, paid_at=self.trial_end, reference="first-1"
            )
        return self.subscription.cycles.get(sequence=1)

    def events(self):
        return list(
            AccountAuditEvent.objects.filter(event__in=EVENTS).values_list("event", "detail")
        )

    def same(self, value, moment):
        """A time in a response is the instant `moment`, to the millisecond JSON carries."""
        expected = moment.replace(microsecond=moment.microsecond // 1000 * 1000)
        self.assertEqual(parse_datetime(value), expected)

    def test_the_trial_answers_the_whole_position(self):
        body = self.get(self.path).json()

        self.assertEqual(set(body), KEYS)
        self.assertEqual(
            (body["state"], body["storefront_served"], body["billing_only"]),
            ("trial_active", True, False),
        )
        self.assertEqual(body["plan"], {
            "name": plan.PLAN_NAME, "price_centavos": 249900, "currency": "PHP",
            "interval": "month", "trial_days": 14,
        })
        self.assertEqual(set(body["current_cycle"]), {"sequence", "start_at", "end_at", "settlement"})
        self.assertEqual(
            (body["current_cycle"]["sequence"], body["current_cycle"]["settlement"]),
            (0, "not_billable"),
        )
        self.same(body["trial_start_at"], self.start)
        self.same(body["trial_end_at"], self.start + days(14))
        self.same(body["next_due_at"], self.trial_end)
        for name in ("attention_ends_at", "cancel_requested_at", "service_end_at",
                     "download_until", "deletion_due_at", "pay_by"):
            self.assertIsNone(body[name], name)
        self.assertEqual(
            (body["clocks_started"], body["billing_exempt"], body["outstanding_centavos"],
             body["can_cancel"], body["can_reactivate"]),
            (False, False, 0, True, False),
        )

    def test_the_read_reports_the_time_and_writes_nothing(self):
        self.at(self.trial_end)
        before = snapshot()

        body = self.get(self.path).json()

        self.assertEqual(
            (body["state"], body["storefront_served"], body["billing_only"]),
            ("cancelled_no_payment_method", False, True),
        )
        self.same(body["service_end_at"], self.trial_end)
        # Never paid, and collection is not live: no dates and no last date to pay (D15).
        self.assertEqual(
            (body["download_until"], body["deletion_due_at"], body["pay_by"],
             body["clocks_started"], body["can_cancel"], body["can_reactivate"]),
            (None, None, None, False, False, False),
        )
        self.assertEqual(snapshot(), before)

    def test_signed_out_is_401_on_every_endpoint(self):
        stranger = Client()
        before = snapshot()

        self.assertEqual(self.get(self.path, client=stranger).status_code, 401)
        for action in ("cancel/", "reactivate/"):
            self.assertEqual(self.post(self.path + action, client=stranger).status_code, 401)
        self.assertEqual(snapshot(), before)

    @override_settings(BILLING_ENABLED=False)
    def test_switched_off_every_endpoint_is_404_and_writes_nothing(self):
        before = snapshot()

        self.error(self.get(self.path), 404, "not_found")
        for action in ("cancel/", "reactivate/"):
            self.error(self.post(self.path + action), 404, "not_found")
        self.assertEqual(snapshot(), before)

    def test_a_site_with_no_subscription_is_404(self):
        bare = make_site(self.account, name="anas-second", first=False)
        path = f"/v1/sites/{bare.public_id}/billing/"

        self.error(self.get(path), 404, "not_found")
        self.error(self.post(path + "cancel/"), 404, "not_found")
        self.error(self.post(path + "reactivate/"), 404, "not_found")

    def test_a_trial_cancel_ends_service_at_once_and_is_recorded(self):
        now = self.at(self.start + days(3))

        response = self.post(self.path + "cancel/")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), KEYS)
        self.assertEqual(
            (body["state"], body["storefront_served"], body["billing_only"], body["can_cancel"],
             body["can_reactivate"]),
            ("cancelled", False, True, False, False),
        )
        self.same(body["service_end_at"], now)
        self.same(body["cancel_requested_at"], now)
        self.assertEqual(self.events(), [("subscription_cancelled", {"site": self.site_id})])
        self.assertEqual(body, self.get(self.path).json())

    def test_a_second_cancel_is_409_and_records_nothing(self):
        self.post(self.path + "cancel/")
        before = snapshot()

        self.error(self.post(self.path + "cancel/"), 409, "already_cancelled")

        self.assertEqual(snapshot(), before)
        self.assertEqual(len(self.events()), 1)

    def test_a_cancel_on_day_15_of_an_unswept_trial_is_already_cancelled(self):
        self.at(self.trial_end + days(1))

        self.error(self.post(self.path + "cancel/"), 409, "already_cancelled")

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(self.events(), [])

    def test_a_paid_cycle_cancel_keeps_service_to_the_period_end_and_can_be_withdrawn(self):
        cycle = self.pay_first_cycle()
        self.at(self.trial_end + days(2))

        cancelled = self.post(self.path + "cancel/").json()

        self.assertEqual(
            (cancelled["state"], cancelled["storefront_served"], cancelled["billing_only"],
             cancelled["can_reactivate"]),
            ("cancelled", True, False, True),
        )
        self.same(cancelled["service_end_at"], cycle.end_at)
        self.same(cancelled["download_until"], cycle.end_at + days(30))
        self.same(cancelled["pay_by"], cycle.end_at + days(90))
        self.assertEqual(cancelled["pay_by"], cancelled["deletion_due_at"])
        self.assertTrue(cancelled["clocks_started"])

        self.at(cycle.end_at - SECOND)
        response = self.post(self.path + "reactivate/")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["state"], body["can_cancel"], body["can_reactivate"]),
                         ("active", True, False))
        for name in ("cancel_requested_at", "service_end_at", "download_until", "pay_by"):
            self.assertIsNone(body[name], name)
        self.same(body["next_due_at"], cycle.end_at)
        self.assertEqual(self.events(), [
            ("subscription_cancelled", {"site": self.site_id}),
            ("subscription_cancellation_withdrawn", {"site": self.site_id}),
        ])

    def test_reactivate_needs_a_payment_everywhere_else(self):
        self.error(self.post(self.path + "reactivate/"), 409, "payment_required")  # on trial
        self.post(self.path + "cancel/")
        self.error(self.post(self.path + "reactivate/"), 409, "payment_required")  # trial cancelled
        self.assertEqual(len(self.events()), 1)

    def test_reactivate_after_the_paid_period_ended_needs_a_payment(self):
        cycle = self.pay_first_cycle()
        self.at(self.trial_end + days(2))
        self.post(self.path + "cancel/")
        self.at(cycle.end_at)

        self.error(self.post(self.path + "reactivate/"), 409, "payment_required")
        body = self.get(self.path).json()
        self.assertEqual((body["state"], body["storefront_served"], body["billing_only"]),
                         ("cancelled", False, True))

    def test_attention_shows_when_it_ends(self):
        cycle = self.pay_first_cycle()
        services.renewal_failed(self.subscription, at=cycle.end_at)
        self.at(cycle.end_at + days(1))

        body = self.get(self.path).json()

        self.assertEqual((body["state"], body["storefront_served"]),
                         ("payment_requires_attention", True))
        self.same(body["next_due_at"], cycle.end_at)
        self.same(body["attention_ends_at"], cycle.end_at + days(7))
        self.assertEqual(body["current_cycle"]["settlement"], "pending")

    def test_an_unknown_key_in_the_body_is_refused_and_changes_nothing(self):
        before = snapshot()

        for action in ("cancel/", "reactivate/"):
            response = self.post(self.path + action, {"site_id": "site_x"})
            self.error(response, 400, "invalid_request")
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.events(), [])

    def test_a_cancel_with_no_body_at_all_is_accepted(self):
        response = self.client.post(
            self.path + "cancel/", data="", content_type="application/json", secure=True,
            HTTP_ORIGIN=ORIGIN,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["state"], "cancelled")

    def test_a_write_without_an_allowed_origin_is_refused_and_changes_nothing(self):
        before = snapshot()

        for action in ("cancel/", "reactivate/"):
            for origin in (None, "https://anas-store.negosyotest.com"):
                with self.subTest(action=action, origin=origin):
                    response = self.post(self.path + action, HTTP_ORIGIN=origin)
                    self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.events(), [])

    def test_an_exempt_site_is_served_and_refuses_every_write(self):
        operator = User.objects.create_user(username="coach", password="x", is_staff=True)
        services.set_exempt(self.site, operator=operator, reason="approved test site")
        self.at(self.trial_end + days(40))
        before = snapshot()

        body = self.get(self.path).json()

        self.assertEqual(
            (body["billing_exempt"], body["storefront_served"], body["billing_only"],
             body["can_cancel"], body["can_reactivate"]),
            (True, True, False, False, False),
        )
        self.error(self.post(self.path + "cancel/"), 409, "billing_exempt")
        self.error(self.post(self.path + "reactivate/"), 409, "billing_exempt")
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.events(), [])

    def test_the_writes_are_limited_per_account(self):
        for _ in range(api.WRITE_LIMIT):
            self.post(self.path + "reactivate/")

        self.error(self.post(self.path + "reactivate/"), 429, "rate_limited")
        self.error(self.post(self.path + "cancel/"), 429, "rate_limited")
        self.assertEqual(Subscription.objects.get(site=self.site).state, State.TRIAL_ACTIVE)

    def test_the_paths_are_in_the_openapi_description(self):
        paths = self.get("/v1/openapi.json").json()["paths"]

        for suffix, method in (("", "get"), ("cancel/", "post"), ("reactivate/", "post")):
            self.assertIn(method, paths["/v1/sites/{site_id}/billing/" + suffix])
