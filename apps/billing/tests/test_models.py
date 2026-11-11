"""The constraints the migration creates."""
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.billing.models import BillingCycle, SubscriptionTransition
from apps.billing.tests.base import CYCLE_1_END, CYCLE_2_END, T0, TRIAL_END, make_subscription
from apps.sites.models import Site


@override_settings(BILLING_ENABLED=True)
class ConstraintTests(TestCase):
    def setUp(self):
        self.subscription = make_subscription()

    def refused(self, create):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create()

    def cycle(self, **fields):
        values = {
            "subscription": self.subscription, "sequence": 1, "kind": "paid",
            "start_at": TRIAL_END, "end_at": CYCLE_1_END, "settlement": "pending", **fields,
        }
        return lambda: BillingCycle.objects.create(**values)

    def test_a_site_has_one_subscription(self):
        site = Site.objects.get()
        self.refused(lambda: type(self.subscription).objects.create(
            site=site, state="trial_active", trial_start_at=T0, trial_end_at=TRIAL_END
        ))

    def test_a_trial_ends_after_it_starts(self):
        self.subscription.trial_end_at = self.subscription.trial_start_at
        self.refused(self.subscription.save)

    def test_a_transition_key_is_written_once(self):
        self.refused(lambda: SubscriptionTransition.objects.create(
            subscription=self.subscription, code="trial_started", to_state="trial_active",
            actor="sweep", idempotency_key=f"trial_started:{self.subscription.pk}",
            effective_at=T0,
        ))

    def test_a_sequence_is_used_once_per_subscription(self):
        self.refused(self.cycle(sequence=0))

    def test_a_cycle_ends_after_it_starts(self):
        self.refused(self.cycle(end_at=TRIAL_END))

    def test_a_subscription_has_at_most_one_pending_cycle(self):
        self.cycle()()
        self.refused(self.cycle(sequence=2, start_at=CYCLE_1_END, end_at=CYCLE_2_END))
