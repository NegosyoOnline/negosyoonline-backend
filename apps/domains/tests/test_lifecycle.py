from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.billing.models import Subscription
from apps.billing.tests.base import CYCLE_1_END, TRIAL_END, days, make_active
from apps.domains import lifecycle, machine
from apps.domains.models import ClaimState, Domain, DomainRegistration
from apps.storefront.tests.base import SurfaceTestCase
from apps.domains.tests.base import PLATFORM, PSL


@override_settings(DOMAINS_ENABLED=True, DOMAINS_PSL_FILE=PSL, DOMAINS_PLATFORM_ZONES=PLATFORM)
class LifecycleTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.sub = make_active()
        self.site_ = self.sub.site
        DomainRegistration.objects.create(site=self.site_, name="brand.com", state="succeeded",
                                          register_sent_at=timezone.now(), send_count=1)
        self.claim_ = Domain.objects.create(site=self.site_, name="brand.com", state=ClaimState.LIVE)

    def state(self):
        return Domain.objects.get(pk=self.claim_.pk).state

    def test_a_paid_up_site_keeps_its_domain(self):
        self.at(TRIAL_END + days(2))
        self.assertEqual(lifecycle.sweep(), 0)
        self.assertEqual(self.state(), ClaimState.LIVE)

    def test_a_suspended_site_is_dark_not_removed(self):
        self.at(CYCLE_1_END + days(8))
        self.assertEqual(lifecycle.sweep(), 0)

    def test_the_deletion_date_removes(self):
        Subscription.objects.filter(pk=self.sub.pk).update(state=Subscription.State.SUSPENDED, deletion_due_at=timezone.now() - timedelta(days=1))
        self.assertEqual(lifecycle.sweep(), 1)
        self.assertEqual(self.state(), ClaimState.REMOVAL_PENDING)

    def test_cancelled_past_service_end_removes(self):
        Subscription.objects.filter(pk=self.sub.pk).update(
            state=Subscription.State.CANCELLED, service_ended_at=timezone.now() - timedelta(days=1))
        self.assertEqual(lifecycle.sweep(), 1)

    def test_cancelled_but_still_paid_is_kept(self):
        Subscription.objects.filter(pk=self.sub.pk).update(
            state=Subscription.State.CANCELLED, service_ended_at=timezone.now() + timedelta(days=5))
        self.assertEqual(lifecycle.sweep(), 0)

    def test_cancelled_without_a_payment_method_removes(self):
        Subscription.objects.filter(pk=self.sub.pk).update(state=Subscription.State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(lifecycle.sweep(), 1)

    def test_a_deleted_site_removes(self):
        type(self.site_).objects.filter(pk=self.site_.pk).update(deleted_at=timezone.now())
        self.assertEqual(lifecycle.sweep(), 1)

    def test_the_registration_is_never_deleted_and_an_alert_is_raised(self):
        type(self.site_).objects.filter(pk=self.site_.pk).update(deleted_at=timezone.now())
        with self.assertLogs("domains", level="ERROR") as logs:
            lifecycle.sweep()
        self.assertIn("auto_renew_review", logs.output[0])
        self.assertTrue(DomainRegistration.objects.filter(site=self.site_).exists())
        with self.assertRaises(Exception):
            DomainRegistration.objects.filter(site=self.site_).delete()

    def test_a_returning_subscriber_can_re_attach_the_same_registration(self):
        """After removal the registration stays `succeeded`; a new claim for it is allowed (the
        one-active-claim index is on non-ended claims only)."""
        Domain.objects.filter(pk=self.claim_.pk).update(state=ClaimState.ENDED)
        again = Domain.objects.create(site=self.site_, name="brand.com", state=ClaimState.REGISTERED)
        self.assertEqual(again.registration().name, "brand.com")

    def test_a_site_with_no_subscription_loses_its_domain_but_only_while_billing_is_read(self):
        from apps.sites.tests.base import make_account, make_site
        bare = make_site(make_account("bare@example.test"), name="bare-store")
        DomainRegistration.objects.create(site=bare, name="bare.com", state="succeeded",
                                          register_sent_at=timezone.now(), send_count=1)
        Domain.objects.create(site=bare, name="bare.com", state=ClaimState.LIVE)
        with override_settings(BILLING_ENABLED=False):
            self.assertEqual(lifecycle.sweep(), 0)
        self.assertEqual(lifecycle.sweep(), 1)
