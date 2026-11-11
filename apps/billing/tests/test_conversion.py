"""What a charge at the first renewal of a Launch Promo term may do (Billing Build B3, pull
request 4). It only decides: nothing here charges, opens a cycle or writes."""
from unittest import mock

from django.test import override_settings

from apps.billing import clock, methods, services
from apps.billing.models import BankTransferPayment, BillingCycle, RenewalConsent
from apps.billing.tests.base import (
    JPEG, T0, TRIAL_END, days, fresh, make_active, make_subscription, manila, submit,
)
from apps.billing.tests.test_transfer import TransferCase, confirm

PROMO, MONTHLY = 499_900, 249_900


@override_settings(BILLING_LAUNCH_PROMO_ENABLED=True)
class ConversionTests(TransferCase):
    def setUp(self):
        super().setUp()
        self.subscription = make_subscription()
        self.payment = submit(self.subscription, T0 + days(3), plan_code="launch_promo")
        confirm(self.payment, self.operator, T0 + days(5), amount=PROMO)
        self.end = manila(2027, 5, 25, 15, 0)

    def decide(self, at=None):
        return services.conversion_decision(fresh(self.subscription), at or self.end)

    def test_it_is_manual_at_launch_because_no_method_can_be_charged(self):
        decision = self.decide()
        self.assertEqual((decision["action"], decision["reason"]), ("manual", "no_reusable_method"))
        self.assertEqual(
            (decision["terms"]["plan"], decision["terms"]["amount_centavos"],
             decision["terms"]["due_at"]),
            ("monthly", MONTHLY, self.end),
        )

    def test_before_the_promo_ends_there_is_nothing_to_decide(self):
        self.assertEqual(self.decide(self.end - days(1))["action"], "none")

    def test_no_consent_on_the_promo_payment_fails_safely(self):
        # A row whose terms differ from what is due next cannot authorise it either.
        RenewalConsent.objects.filter(pk=RenewalConsent.objects.get().pk).update(
            then_price_centavos=199_900
        )
        decision = self.decide()
        self.assertEqual((decision["action"], decision["reason"]), ("manual", "no_valid_consent"))
        RenewalConsent.objects.all().delete()  # a missing row, as for a payment never consented
        decision = self.decide()
        self.assertEqual((decision["action"], decision["reason"]), ("manual", "no_valid_consent"))

    def test_with_consent_and_a_reusable_method_it_is_a_charge_with_a_stable_key(self):
        with mock.patch.object(methods, "reusable", return_value=True):
            first, second = self.decide(), self.decide()
        self.assertEqual(first["action"], "charge")
        self.assertEqual(first["key"], f"renewal:{self.subscription.pk}:{2}")
        self.assertEqual(first, second)

    def test_a_decision_writes_nothing(self):
        before = (BillingCycle.objects.count(), BankTransferPayment.objects.count())
        with mock.patch.object(methods, "reusable", return_value=True):
            self.decide()
        self.assertEqual((BillingCycle.objects.count(), BankTransferPayment.objects.count()), before)

    def test_a_cancelled_or_exempt_subscription_is_never_charged(self):
        services.cancel(fresh(self.subscription), now=TRIAL_END + days(20))
        self.assertEqual(self.decide()["action"], "none")

    def test_a_site_that_already_paid_monthly_ahead_is_not_a_promo_renewal(self):
        confirm(submit(self.subscription, self.end - days(3)), self.operator,
                self.end - days(2))
        self.assertEqual(self.decide()["action"], "none")

    def test_a_monthly_cycle_is_not_a_promo_term(self):
        subscription = make_active(email="mo@example.test", name="mo-store")
        self.assertEqual(
            services.conversion_decision(subscription, subscription.cycles.last().end_at)["action"],
            "none",
        )


@override_settings(BILLING_LAUNCH_PROMO_ENABLED=True)
class ChainTests(TransferCase):
    """The promo, then Monthly cycles: each starts where the one before ended."""

    def run_chain(self, start, number):
        subscription = make_subscription(email=f"chain{number}@example.test", name=f"chain-{number}",
                                         start=start)
        confirm(
            submit(subscription, start + days(1), plan_code="launch_promo"), self.operator,
            start + days(2), amount=PROMO,
        )
        for _ in range(6):
            latest = fresh(subscription).cycles.filter(settlement="paid").order_by("-sequence").first()
            at = latest.end_at - days(2)
            slip = JPEG + at.isoformat().encode()  # a different file for each proof
            confirm(submit(subscription, at, content=slip), self.operator, at + days(1))
        return list(fresh(subscription).cycles.filter(settlement="paid").order_by("sequence"))

    def test_the_promo_then_six_monthly_terms_never_overlap_or_leave_a_gap(self):
        # trial end days: 8, 29, 30, 31 and the leap day 29 Feb 2028
        starts = (manila(2026, 10, 25, 10, 0), manila(2026, 8, 15, 10, 0),
                  manila(2026, 8, 16, 10, 0), manila(2026, 8, 17, 10, 0),
                  manila(2028, 2, 15, 10, 0))
        for number, start in enumerate(starts):
            with self.subTest(start=start):
                cycles = self.run_chain(start, number)
                self.assertEqual(len(cycles), 7)
                self.assertEqual([c.plan_code for c in cycles], ["launch_promo"] + ["monthly"] * 6)
                for before, after in zip(cycles, cycles[1:]):
                    self.assertEqual(after.start_at, before.end_at)
                anchor = clock.anchor_day(cycles[0].start_at)
                self.assertEqual(cycles[0].end_at, clock.cycle_end(cycles[0].start_at, anchor, 6))
