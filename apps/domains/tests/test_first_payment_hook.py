"""The first-payment hook and the safety net (ruling SD1): the gate is the first confirmed
subscription payment of any plan, never a trial; an early first payment counts; a late first
transfer (settled by `reactivated`) counts; a domain bug never fails a Finance confirmation."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Subscription
from apps.billing.tests.base import days, make_operator, submit, transfers_on
from apps.domains import services
from apps.domains.models import DomainEvent
from apps.domains.tests.base import DomainsTestCase


class HookTests(DomainsTestCase):
    def setUp(self):
        super().setUp()
        on = transfers_on()
        self.store = on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.operator = make_operator()
        self.site_ = self.site()
        self.subscription = Subscription.objects.get(site=self.site_)
        mail.outbox.clear()

    def confirm(self, at=None, plan_code="monthly", subscription=None):
        subscription = subscription or Subscription.objects.get(pk=self.subscription.pk)
        at = at or timezone.now()
        payment = submit(subscription, at, plan_code=plan_code)
        with self.captureOnCommitCallbacks(execute=True):
            billing.confirm_transfer(payment, self.operator, amount_received_centavos=payment.amount_centavos, now=at)
        return payment

    def eligible(self):
        return DomainEvent.objects.filter(kind="eligible", site=self.site_).count()

    def test_a_trial_alone_makes_the_site_eligible_for_nothing(self):
        self.assertEqual(services.on_first_payment(self.site_), "not_eligible")
        self.assertEqual(self.eligible(), 0)

    def test_an_early_first_payment_marks_the_site_eligible_and_notifies_once(self):
        self.confirm()
        self.assertEqual(self.eligible(), 1)
        self.assertEqual([m.subject for m in mail.outbox if "domain" in m.subject.lower()], ["Choose your domain"])

    def test_the_hook_is_idempotent_across_every_later_confirmation(self):
        payment = self.confirm()
        self.assertEqual(services.on_first_payment(self.site_), "already")
        self.assertEqual(services.on_first_payment(self.site_), "already")
        with self.captureOnCommitCallbacks(execute=True) as callbacks:  # confirming it again is a no-op
            billing.confirm_transfer(payment, self.operator, amount_received_centavos=payment.amount_centavos)
        self.assertEqual(callbacks, [])
        self.assertEqual(self.eligible(), 1)
        self.assertEqual(len([m for m in mail.outbox if m.subject == "Choose your domain"]), 1)

    def test_a_late_first_transfer_settled_by_reactivated_also_unlocks(self):
        trial_end = self.subscription.trial_end_at
        billing.sweep(now=trial_end + days(1))
        self.assertEqual(Subscription.objects.get(pk=self.subscription.pk).state, "cancelled_no_payment_method")
        self.confirm(at=trial_end + days(3))
        subscription = Subscription.objects.get(pk=self.subscription.pk)
        self.assertEqual(subscription.transitions.filter(code="reactivated").count(), 1)
        self.assertFalse(subscription.transitions.filter(code="first_payment_succeeded").exists())
        self.assertEqual(self.eligible(), 1)

    def test_a_domain_bug_never_fails_a_finance_confirmation(self):
        with mock.patch.object(services, "on_first_payment", side_effect=RuntimeError("bug")), \
                self.assertLogs("apps.domains.services", level="ERROR"):
            payment = self.confirm()
        payment.refresh_from_db()
        self.assertEqual(payment.status, "confirmed")
        self.assertEqual(self.eligible(), 0)

    def test_a_refused_confirmation_marks_nothing(self):
        subscription = Subscription.objects.get(pk=self.subscription.pk)
        payment = submit(subscription, timezone.now())
        with self.captureOnCommitCallbacks(execute=True) as callbacks, self.assertRaises(Exception):
            billing.confirm_transfer(payment, self.operator, amount_received_centavos=1, now=timezone.now())
        self.assertEqual(callbacks, [])
        self.assertEqual(self.eligible(), 0)

    @override_settings(DOMAINS_ENABLED=False)
    def test_with_the_switch_off_the_hook_reads_and_writes_nothing(self):
        self.confirm()
        self.assertEqual(DomainEvent.objects.count(), 0)
        self.assertEqual(services.safety_net(), 0)

    def test_an_exempt_site_is_never_eligible(self):
        self.pay(self.site_)
        Subscription.objects.filter(pk=self.subscription.pk).update(billing_exempt=True)
        self.assertEqual(services.on_first_payment(self.site_), "not_eligible")
        self.assertEqual(services.safety_net(), 0)
        self.assertEqual(self.eligible(), 0)

    def test_the_safety_net_finds_a_site_whose_hook_was_skipped(self):
        paid = self.site(paid=True)  # paid without a hook
        trial = self.site()
        self.assertEqual(DomainEvent.objects.filter(kind="eligible").count(), 0)
        self.assertEqual(services.safety_net(), 1)
        self.assertEqual(list(DomainEvent.objects.filter(kind="eligible").values_list("site_id", flat=True)), [paid.pk])
        self.assertEqual(services.safety_net(), 0)  # nothing twice
        self.assertEqual(DomainEvent.objects.filter(site=trial).count(), 0)
        self.assertEqual(len([m for m in mail.outbox if m.subject == "Choose your domain"]), 1)
