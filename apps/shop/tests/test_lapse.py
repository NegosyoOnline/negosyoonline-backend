"""A request that passed the storefront check and then lost service before the lock commits
creates nothing (G4-S). The Shop counterpart of Pack's LapseDuringPlacementTests."""
from datetime import timedelta
from unittest import mock

from apps.billing import services
from apps.billing.models import Subscription
from apps.billing.tests.base import CYCLE_1_END, TRIAL_END, days, method_on_file
from apps.shop import placement
from apps.shop.models import ShopOrder, ShopProofSubmission
from apps.shop.tests.base import NOT_FOUND, ShopTestCase


class LapseDuringPlacementTests(ShopTestCase):
    def lapse_after_the_first_check(self, how):
        real = placement._store

        def store_then_lapse(site, proof):
            key = real(site, proof)  # the stored file is what a refused order must discard
            how()
            return key

        return mock.patch.object(placement, "_store", side_effect=store_then_lapse)

    def place_with(self, how):
        with self.lapse_after_the_first_check(how), self.captureOnCommitCallbacks(execute=True):
            return self.place(self.cart())

    def assert_nothing_created(self, response):
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertEqual(ShopOrder.objects.count(), 0)
        self.assertEqual(ShopProofSubmission.objects.count(), 0)
        self.assertEqual(self.proof_files(), [])

    def set_state(self, state, **extra):
        return lambda: Subscription.objects.filter(site=self.site).update(state=state, **extra)

    def test_a_suspension_after_the_first_check_creates_no_order(self):
        self.assert_nothing_created(self.place_with(self.set_state(Subscription.State.SUSPENDED)))

    def test_a_cancellation_that_has_ended_after_the_first_check_creates_no_order(self):
        ended = self.clock.now() - timedelta(seconds=1)
        how = self.set_state(Subscription.State.CANCELLED, service_ended_at=ended)
        self.assert_nothing_created(self.place_with(how))

    def test_an_operator_hold_after_the_first_check_creates_no_order(self):
        how = lambda: type(self.site).objects.filter(pk=self.site.pk).update(operator_hold=True)  # noqa: E731
        self.assert_nothing_created(self.place_with(how))

    def test_the_same_request_without_a_lapse_is_placed(self):
        response = self.place_with(lambda: None)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ShopOrder.objects.count(), 1)

    def subscription(self):
        return Subscription.objects.get(site=self.site)

    def assert_places(self):
        before = ShopOrder.objects.count()
        response = self.place_with(lambda: None)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ShopOrder.objects.count(), before + 1)

    def test_a_trial_site_still_places(self):
        self.assert_places()

    def test_a_paid_cycle_still_places(self):
        with method_on_file():
            services.first_payment_succeeded(self.subscription(), paid_at=TRIAL_END, reference="first-shop")
        self.at(TRIAL_END + days(1))
        self.assertEqual(self.subscription().state, Subscription.State.ACTIVE)
        self.assert_places()

    def test_payment_requires_attention_still_places(self):
        with method_on_file():
            services.first_payment_succeeded(self.subscription(), paid_at=TRIAL_END, reference="first-shop")
        services.renewal_failed(self.subscription(), at=CYCLE_1_END)
        self.at(CYCLE_1_END + days(1))
        self.assertEqual(self.subscription().state, Subscription.State.PAYMENT_REQUIRES_ATTENTION)
        self.assert_places()

    def test_a_billing_exempt_site_still_places_even_when_its_state_has_ended(self):
        Subscription.objects.filter(site=self.site).update(
            billing_exempt=True, state=Subscription.State.SUSPENDED
        )
        response = self.place_with(lambda: None)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ShopOrder.objects.count(), 1)
