from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.billing import services
from apps.billing.models import Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, T0, TRIAL_END, days, fresh, make_active, make_attention, make_subscription,
)
from apps.billing.tests.test_transitions import snapshot

State = Subscription.State


def run_command(*args):
    out = StringIO()
    call_command("billing_sweep", *args, stdout=out)
    return out.getvalue()


@override_settings(BILLING_ENABLED=True)
class SweepTests(TestCase):
    def setUp(self):
        self.trial_due = make_subscription()
        self.trial_running = make_subscription("ben@example.test", "bens-store", start=T0 + days(60))
        self.attention_due = make_attention(email="cat@example.test", name="cats-store")
        self.active_due = make_active(email="dan@example.test", name="dans-store")
        self.active_running = make_active(email="eve@example.test", name="eves-store")
        services.renewed(self.active_running, paid_at=CYCLE_1_END, reference="pay-eve-2")
        self.now = CYCLE_1_END + days(7)

    def states(self):
        return [
            fresh(subscription).state
            for subscription in (self.trial_due, self.trial_running, self.attention_due,
                                 self.active_due, self.active_running)
        ]

    def test_it_moves_every_due_subscription_and_nothing_else(self):
        counts = services.sweep(now=self.now)

        self.assertEqual(counts, {
            "trial_ended_no_method": 1, "suspended_after_attention": 1, "suspended_no_outcome": 1,
        })
        self.assertEqual(self.states(), [
            State.CANCELLED_NO_PAYMENT_METHOD, State.TRIAL_ACTIVE, State.SUSPENDED,
            State.SUSPENDED, State.ACTIVE,
        ])
        for transition in self.trial_due.transitions.exclude(code="trial_started"):
            self.assertEqual(transition.actor, "sweep")

    def test_a_second_run_changes_nothing(self):
        services.sweep(now=self.now)
        before = snapshot()

        self.assertEqual(services.sweep(now=self.now + days(1)), {})
        self.assertEqual(snapshot(), before)

    def test_one_subscription_failing_does_not_stop_the_others(self):
        real = services.catch_up

        def failing(subscription, now, **kwargs):
            if subscription.pk == self.trial_due.pk:
                raise RuntimeError("boom")
            return real(subscription, now, **kwargs)

        with mock.patch.object(services, "catch_up", side_effect=failing):
            with self.assertLogs("apps.billing.services", level="ERROR"):
                counts = services.sweep(now=self.now)

        self.assertEqual(counts, {
            "failed": 1, "suspended_after_attention": 1, "suspended_no_outcome": 1,
        })
        self.assertEqual(fresh(self.trial_due).state, State.TRIAL_ACTIVE)
        self.assertEqual(services.sweep(now=self.now), {"trial_ended_no_method": 1})

    def test_with_the_switch_off_it_writes_nothing(self):
        before = snapshot()

        with override_settings(BILLING_ENABLED=False):
            self.assertEqual(services.sweep(now=self.now), {})
            self.assertIn("BILLING_ENABLED is off", run_command())

        self.assertEqual(snapshot(), before)

    def test_a_dry_run_counts_what_is_due_and_writes_nothing(self):
        before = snapshot()

        counts = services.sweep(now=self.now, dry_run=True)

        self.assertEqual(counts, {
            "trial_ended_no_method": 1, "suspended_after_attention": 1, "suspended_no_outcome": 1,
        })
        self.assertEqual(snapshot(), before)

    def test_the_command_dry_run_writes_nothing(self):
        before = snapshot()

        with mock.patch("apps.billing.services.timezone.now", return_value=self.now):
            output = run_command("--dry-run")

        self.assertIn("Dry run", output)
        self.assertIn("trial_ended_no_method: 1", output)
        self.assertEqual(snapshot(), before)

    def test_the_command_runs_the_sweep(self):
        with mock.patch("apps.billing.services.timezone.now", return_value=TRIAL_END):
            output = run_command()

        self.assertIn("trial_ended_no_method: 1", output)
        self.assertEqual(fresh(self.trial_due).state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(fresh(self.attention_due).state, State.PAYMENT_REQUIRES_ATTENTION)
