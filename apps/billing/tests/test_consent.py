"""The Launch Promo's consent record (Billing Build B3, pull request 4)."""
from django.test import override_settings

from apps.billing import plan, services
from apps.billing.models import BankTransferPayment, RenewalConsent
from apps.billing.tests.base import (
    JPEG, PNG, T0, TRIAL_END, days, fresh, make_active, make_subscription, manila, submit,
)
from apps.billing.tests.test_transfer import TransferCase, confirm

promo_on = override_settings(BILLING_LAUNCH_PROMO_ENABLED=True)
OK = {"renewal_consent": True, "consent_version": plan.CONSENT_VERSION}


@promo_on
class ConsentTests(TransferCase):
    def send(self, subscription, at, **fields):
        return services.submit_transfer(
            subscription, subscription.site.account, plan_code="launch_promo", content=JPEG,
            transferred_on=at.date(), now=at, **fields,
        )

    def test_a_promo_without_consent_is_refused_and_nothing_is_stored(self):
        subscription = make_subscription()
        for fields in ({}, {"renewal_consent": False, "consent_version": plan.CONSENT_VERSION},
                       {"renewal_consent": "yes", "consent_version": plan.CONSENT_VERSION}):
            refusal = self.refused("invalid_request", self.send, subscription, T0 + days(1), **fields)
            self.assertIn("renewal_consent", refusal.fields)
        self.assertEqual(BankTransferPayment.objects.count(), 0)
        self.assertEqual(RenewalConsent.objects.count(), 0)
        self.assertEqual(self.store.files, {})

    def test_a_stale_version_is_refused(self):
        refusal = self.refused(
            "invalid_request", self.send, make_subscription(), T0 + days(1),
            renewal_consent=True, consent_version="promo-consent-0",
        )
        self.assertEqual(list(refusal.fields), ["consent_version"])
        self.assertEqual(RenewalConsent.objects.count(), 0)
        self.assertEqual(self.store.files, {})

    def test_the_consent_is_kept_with_the_proof_for_a_trial(self):
        subscription = make_subscription()
        payment = submit(subscription, T0 + days(3), plan_code="launch_promo")
        consent = RenewalConsent.objects.get()
        self.assertEqual(
            (consent.payment_id, consent.subscription_id, consent.plan_code, consent.plan_version,
             consent.then_plan_code, consent.then_price_centavos, consent.text_version,
             consent.accepted_by_id, consent.accepted_at, consent.starts_at_confirmation),
            (payment.pk, subscription.pk, "launch_promo", 1, "monthly", 249_900,
             "promo-consent-1", subscription.site.account_id, T0 + days(3), False),
        )
        self.assertEqual(consent.shown_first_due_at, manila(2027, 5, 25, 15, 0))

    def test_an_ended_site_is_shown_no_date(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)
        submit(subscription, TRIAL_END + days(2), plan_code="launch_promo")
        consent = RenewalConsent.objects.get()
        self.assertEqual((consent.shown_first_due_at, consent.starts_at_confirmation), (None, True))

    def test_one_consent_per_proof_and_a_replaced_proof_keeps_its_own(self):
        subscription = make_subscription()
        first = submit(subscription, T0 + days(3), plan_code="launch_promo")
        again = submit(subscription, T0 + days(3), plan_code="launch_promo")  # the same file
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(RenewalConsent.objects.count(), 1)
        second = submit(subscription, T0 + days(4), plan_code="launch_promo", content=PNG)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(
            sorted(RenewalConsent.objects.values_list("payment_id", flat=True)),
            sorted([first.pk, second.pk]),
        )
        self.assertEqual(BankTransferPayment.objects.get(pk=first.pk).status, "replaced")

    def test_a_consent_is_never_changed_or_deleted(self):
        submit(make_subscription(), T0 + days(3), plan_code="launch_promo")
        consent = RenewalConsent.objects.get()
        with self.assertRaises(RuntimeError):
            consent.delete()
        consent.then_price_centavos = 1
        with self.assertRaises(RuntimeError):
            consent.save()

    def test_monthly_and_annual_are_not_asked_and_ignore_the_fields(self):
        subscription = make_subscription()
        submit(subscription, T0 + days(3), renewal_consent="nonsense", consent_version="x")
        self.assertEqual(RenewalConsent.objects.count(), 0)

    def test_the_offer_carries_what_must_be_accepted(self):
        subscription = make_subscription()
        promo, monthly = services.payment_options(fresh(subscription), T0 + days(1))["offers"]
        self.assertIsNone(monthly["consent"])
        end = manila(2027, 5, 25, 15, 0)
        self.assertEqual(promo["consent"], {
            "required": True, "version": "promo-consent-1", "first_due_at": end,
            "then": {"plan": "monthly", "amount_centavos": 249_900, "interval": "month",
                     "first_due_at": end},
        })
