"""The table in section 5.3 of the plan: the state is computed from the time."""
from unittest import mock

from django.test import TestCase, override_settings

from apps.billing import services
from apps.billing.models import BillingCycle, Subscription, SubscriptionTransition
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, T0, TRIAL_END, days, fresh, make_active, make_attention,
    make_subscription, make_suspended, method_on_file,
)
from apps.sites.tests.base import make_account, make_site

State = Subscription.State


def rows():
    return (SubscriptionTransition.objects.count(), BillingCycle.objects.count())


@override_settings(BILLING_ENABLED=True)
class PositionTests(TestCase):
    def report(self, subscription, at):
        position = services.position(subscription.site, at=at)
        return position.state, position.storefront_served, position.billing_only

    def test_trial_before_its_end(self):
        subscription = make_subscription()

        self.assertEqual(self.report(subscription, T0), (State.TRIAL_ACTIVE, True, False))
        self.assertEqual(
            self.report(subscription, TRIAL_END - SECOND), (State.TRIAL_ACTIVE, True, False)
        )

    def test_trial_at_its_end_with_no_method_even_if_the_sweep_never_ran(self):
        subscription = make_subscription()
        before = rows()

        self.assertEqual(
            self.report(subscription, TRIAL_END),
            (State.CANCELLED_NO_PAYMENT_METHOD, False, True),
        )
        self.assertEqual(fresh(subscription).state, State.TRIAL_ACTIVE)  # nothing was written
        self.assertEqual(rows(), before)

    def test_trial_past_its_end_with_a_method_and_no_outcome_is_served_for_7_days(self):
        subscription = make_subscription()

        with method_on_file():
            self.assertEqual(
                self.report(subscription, TRIAL_END), (State.TRIAL_ACTIVE, True, False)
            )
            self.assertEqual(
                self.report(subscription, TRIAL_END + days(7) - SECOND),
                (State.TRIAL_ACTIVE, True, False),
            )
            self.assertEqual(
                self.report(subscription, TRIAL_END + days(7)), (State.SUSPENDED, False, True)
            )

    def test_paid_cycle_before_its_end(self):
        subscription = make_active()

        self.assertEqual(
            self.report(subscription, CYCLE_1_END - SECOND), (State.ACTIVE, True, False)
        )

    def test_a_paying_site_is_not_shut_at_3pm_by_sweep_lag(self):
        subscription = make_active()
        before = rows()

        self.assertEqual(self.report(subscription, CYCLE_1_END), (State.ACTIVE, True, False))
        self.assertEqual(
            self.report(subscription, CYCLE_1_END + SECOND), (State.ACTIVE, True, False)
        )
        self.assertEqual(
            self.report(subscription, CYCLE_1_END + days(7) - SECOND),
            (State.ACTIVE, True, False),
        )
        self.assertEqual(rows(), before)

    def test_past_the_due_date_plus_7_days_it_is_suspended_even_if_the_sweep_never_ran(self):
        subscription = make_active()
        before = rows()

        self.assertEqual(
            self.report(subscription, CYCLE_1_END + days(7)), (State.SUSPENDED, False, True)
        )
        self.assertEqual(fresh(subscription).state, State.ACTIVE)
        self.assertEqual(rows(), before)

    def test_payment_requires_attention_is_served_for_7_days_from_the_due_date(self):
        subscription = make_attention()
        before = rows()

        self.assertEqual(
            self.report(subscription, CYCLE_1_END + days(7) - SECOND),
            (State.PAYMENT_REQUIRES_ATTENTION, True, False),
        )
        self.assertEqual(
            self.report(subscription, CYCLE_1_END + days(7)), (State.SUSPENDED, False, True)
        )
        self.assertEqual(rows(), before)

    def test_suspended(self):
        subscription = make_suspended()

        self.assertEqual(
            self.report(subscription, CYCLE_1_END + days(8)), (State.SUSPENDED, False, True)
        )

    def test_cancelled_is_served_until_its_service_end(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))

        self.assertEqual(
            self.report(subscription, CYCLE_1_END - SECOND), (State.CANCELLED, True, False)
        )
        self.assertEqual(self.report(subscription, CYCLE_1_END), (State.CANCELLED, False, True))

    def test_cancelled_for_no_payment_method(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)

        self.assertEqual(fresh(subscription).state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(
            self.report(subscription, TRIAL_END + days(1)),
            (State.CANCELLED_NO_PAYMENT_METHOD, False, True),
        )

    def test_an_exempt_site_is_served_whatever_its_rows_say(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)
        Subscription.objects.filter(pk=subscription.pk).update(billing_exempt=True)

        self.assertEqual(
            self.report(subscription, TRIAL_END + days(400)),
            (State.CANCELLED_NO_PAYMENT_METHOD, True, False),
        )

    def test_no_subscription_is_not_served(self):
        site = make_site(make_account())

        self.assertEqual(self.report_site(site), (None, False, False))

    def test_the_switch_off_is_not_served(self):
        subscription = make_subscription()

        with override_settings(BILLING_ENABLED=False):
            self.assertEqual(self.report_site(subscription.site), (None, False, False))

    def test_an_error_while_reading_is_not_served(self):
        subscription = make_subscription()

        with mock.patch.object(services, "_read_position", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.billing.services", level="ERROR"):
                self.assertEqual(self.report_site(subscription.site), (None, False, False))

    def report_site(self, site):
        position = services.position(site, at=T0 + days(1))
        return position.state, position.storefront_served, position.billing_only

    def test_the_next_change_is_the_moment_the_site_would_go_off(self):
        trial = make_subscription()
        active = make_active(email="ben@example.test", name="bens-store")

        self.assertEqual(services.position(trial.site, at=T0).next_change_at, TRIAL_END)
        self.assertEqual(
            services.position(active.site, at=TRIAL_END + days(1)).next_change_at,
            CYCLE_1_END + days(7),
        )
        services.cancel(active, now=TRIAL_END + days(2))
        self.assertEqual(
            services.position(active.site, at=TRIAL_END + days(3)).next_change_at, CYCLE_1_END
        )
        self.assertIsNone(services.position(active.site, at=CYCLE_1_END).next_change_at)
