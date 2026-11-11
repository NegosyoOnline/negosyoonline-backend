"""An account disposal while a subscription exists: `prepare_disposal` (plan D13, 6.6)."""
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts import retention as rules
from apps.accounts.models import Account
from apps.billing import services
from apps.billing.models import BillingCycle, Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, T0, TRIAL_END, days, fresh, make_active, make_attention,
    make_subscription, make_suspended,
)
from apps.billing.tests.test_transitions import codes, snapshot
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site

State = Subscription.State
IN_TRIAL = T0 + days(1)
IN_TERM = TRIAL_END + days(1)
SUSPENDED_AT = CYCLE_1_END + days(7)


def term_running(site, until=CYCLE_1_END):
    return {"reason": "paid_term_running", "site": site.public_id, "until": until}


def last_transition(subscription):
    return subscription.transitions.order_by("-pk").first()


@override_settings(BILLING_ENABLED=True)
class PrepareDisposalTests(TestCase):
    def step(self, subscription, now, **extra):
        return services.prepare_disposal(subscription.site.account, now, **extra)

    def assert_dry_run_matches(self, subscription, now):
        """A dry run writes nothing and names what the real run then does."""
        before = snapshot()
        dry = self.step(subscription, now, dry_run=True)
        self.assertEqual(snapshot(), before)
        real = self.step(subscription, now)
        self.assertEqual(dry, real)
        return real

    def test_a_trial_is_cancelled_by_the_operator_and_the_disposal_proceeds(self):
        subscription = make_subscription()

        step = self.assert_dry_run_matches(subscription, IN_TRIAL)

        self.assertEqual((step.cancelled, step.reasons), ((subscription.site.public_id,), ()))
        subscription = fresh(subscription)
        self.assertEqual(
            (subscription.state, subscription.service_ended_at), (State.CANCELLED, IN_TRIAL)
        )
        cancelled = last_transition(subscription)
        self.assertEqual(
            (cancelled.code, cancelled.actor, cancelled.effective_at),
            ("cancelled", "operator", IN_TRIAL),
        )

    def test_attention_is_cancelled_at_once_and_the_pending_fee_is_not_due(self):
        subscription = make_attention()
        now = CYCLE_1_END + days(1)

        step = self.assert_dry_run_matches(subscription, now)

        self.assertEqual((step.cancelled, step.reasons), ((subscription.site.public_id,), ()))
        subscription = fresh(subscription)
        self.assertEqual((subscription.state, subscription.service_ended_at), (State.CANCELLED, now))
        self.assertEqual(subscription.deletion_due_at, now + days(90))
        self.assertEqual(subscription.cycles.get(sequence=2).settlement, "lapsed")
        self.assertEqual(last_transition(subscription).actor, "operator")

    def test_a_suspended_one_is_cancelled_and_keeps_its_dates(self):
        subscription = make_suspended()
        now = SUSPENDED_AT + days(3)

        step = self.assert_dry_run_matches(subscription, now)

        self.assertEqual((step.cancelled, step.reasons), ((subscription.site.public_id,), ()))
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, State.CANCELLED)
        self.assertEqual(
            (subscription.service_ended_at, subscription.download_until,
             subscription.deletion_due_at),
            (SUSPENDED_AT, SUSPENDED_AT + days(30), SUSPENDED_AT + days(90)),
        )

    def test_an_unswept_suspension_is_caught_up_first_and_keeps_the_rules_dates(self):
        subscription = make_attention()  # the sweep never ran after the 7 days
        now = SUSPENDED_AT + days(3)

        step = self.assert_dry_run_matches(subscription, now)

        self.assertEqual(step.cancelled, (subscription.site.public_id,))
        self.assertEqual(
            codes(subscription)[-2:], ["suspended_after_attention", "cancelled"]
        )
        self.assertEqual(fresh(subscription).service_ended_at, SUSPENDED_AT)

    def test_inside_a_paid_term_it_cancels_to_the_end_of_the_term_and_refuses_for_now(self):
        subscription = make_active()
        site = subscription.site

        step = self.assert_dry_run_matches(subscription, IN_TERM)

        self.assertEqual((step.cancelled, step.reasons), ((site.public_id,), (term_running(site),)))
        subscription = fresh(subscription)
        self.assertEqual(
            (subscription.state, subscription.cancel_requested_at, subscription.service_ended_at),
            (State.CANCELLED, IN_TERM, CYCLE_1_END),
        )
        self.assertTrue(services.position(site, at=CYCLE_1_END - SECOND).storefront_served)

    def test_a_second_run_before_the_date_writes_no_second_cancellation(self):
        subscription = make_active()
        site = subscription.site
        self.step(subscription, IN_TERM)
        before = snapshot()

        for dry_run in (True, False):
            step = self.step(subscription, CYCLE_1_END - SECOND, dry_run=dry_run)
            self.assertEqual((step.cancelled, step.reasons), ((), (term_running(site),)))

        self.assertEqual(snapshot(), before)
        self.assertEqual(subscription.transitions.filter(code="cancelled").count(), 1)

    def test_at_the_end_of_the_term_the_disposal_may_proceed(self):
        subscription = make_active()
        self.step(subscription, IN_TERM)
        before = snapshot()

        step = self.assert_dry_run_matches(subscription, CYCLE_1_END)

        self.assertEqual((step.cancelled, step.reasons), ((), ()))
        self.assertEqual(snapshot(), before)

    def test_a_paid_period_that_ended_with_no_outcome_is_cancelled_at_once(self):
        subscription = make_active()
        now = CYCLE_1_END + days(1)  # D9

        step = self.assert_dry_run_matches(subscription, now)

        self.assertEqual((step.cancelled, step.reasons), ((subscription.site.public_id,), ()))
        self.assertEqual(fresh(subscription).service_ended_at, now)

    def test_an_ended_trial_is_left_as_it_is(self):
        swept = make_subscription(now=TRIAL_END)
        unswept = make_subscription("ben@example.test", "bens-store")
        before = snapshot()

        self.assertEqual(self.step(swept, TRIAL_END + days(1)), services.DisposalStep())
        self.assertEqual(snapshot(), before)
        self.assertEqual(
            self.step(unswept, TRIAL_END + days(1), dry_run=True), services.DisposalStep()
        )
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.step(unswept, TRIAL_END + days(1)), services.DisposalStep())
        # The stored rows were caught up with the time; nothing was cancelled.
        self.assertEqual(codes(unswept), ["trial_started", "trial_ended_no_method"])

    def test_a_cancelled_subscription_past_its_service_end_is_left_as_it_is(self):
        subscription = make_subscription()
        services.cancel(subscription, now=IN_TRIAL)
        before = snapshot()

        self.assertEqual(self.step(subscription, IN_TRIAL + days(1)), services.DisposalStep())

        self.assertEqual(snapshot(), before)

    def test_an_exempt_subscription_is_left_as_it_is(self):
        subscription = make_active()
        operator = get_user_model().objects.create_user(username="coach", is_staff=True)
        services.set_exempt(subscription.site, operator=operator, reason="approved test site")
        before = snapshot()

        for dry_run in (True, False):
            self.assertEqual(
                self.step(subscription, IN_TERM, dry_run=dry_run), services.DisposalStep()
            )

        self.assertEqual(snapshot(), before)

    def test_an_account_with_no_subscription_has_nothing_to_do(self):
        account = make_account()
        make_site(account)

        self.assertEqual(services.prepare_disposal(account, IN_TRIAL), services.DisposalStep())
        self.assertFalse(Subscription.objects.exists())

    def test_it_reaches_only_the_accounts_own_subscriptions(self):
        mine = make_subscription()
        theirs = make_subscription("ben@example.test", "bens-store")
        before = list(Subscription.objects.filter(pk=theirs.pk).values())

        self.step(mine, IN_TRIAL)

        self.assertEqual(list(Subscription.objects.filter(pk=theirs.pk).values()), before)
        self.assertEqual(codes(theirs), ["trial_started"])

    @override_settings(BILLING_ENABLED=False)
    def test_switched_off_it_does_nothing(self):
        with override_settings(BILLING_ENABLED=True):
            subscription = make_subscription()
        before = snapshot()

        self.assertEqual(self.step(subscription, IN_TRIAL), services.DisposalStep())

        self.assertEqual(snapshot(), before)


@override_settings(BILLING_ENABLED=True, RETENTION_ENABLED=True)
class DisposeAccountWithBillingTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_user(
            username="coach", password="x", is_staff=True
        )

    def dispose(self, subscription, now, **extra):
        return rules.dispose_account(
            subscription.site.account, now=now, rule="request", actor=self.operator,
            request_ref="DPO-2026-001", **extra,
        )

    def personal_rows(self, subscription):
        site = subscription.site
        return (
            list(Account.objects.filter(pk=site.account_id).values()),
            list(Site.objects.filter(pk=site.pk).values()),
        )

    def is_disposed(self, subscription):
        return rules.is_disposed(Account.objects.get(pk=subscription.site.account_id))

    def test_a_trial_is_cancelled_and_the_account_is_disposed_of(self):
        subscription = make_subscription()

        counts = self.dispose(subscription, IN_TRIAL)

        self.assertEqual(counts["subscriptions_cancelled"], 1)
        self.assertTrue(self.is_disposed(subscription))
        subscription = fresh(subscription)
        self.assertEqual(
            (subscription.state, subscription.service_ended_at), (State.CANCELLED, IN_TRIAL)
        )
        self.assertEqual(last_transition(subscription).actor, "operator")
        self.assertIsNotNone(Site.objects.get(pk=subscription.site_id).deleted_at)
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        self.assertEqual(entry.detail["counts"]["subscriptions_cancelled"], 1)

    def test_attention_and_suspended_are_cancelled_and_the_disposal_proceeds(self):
        attention = make_attention()
        suspended = make_suspended(email="ben@example.test", name="bens-store")

        self.dispose(attention, CYCLE_1_END + days(1))
        self.dispose(suspended, SUSPENDED_AT + days(3))

        for subscription in (attention, suspended):
            self.assertTrue(self.is_disposed(subscription))
            self.assertEqual(fresh(subscription).state, State.CANCELLED)
        self.assertEqual(fresh(suspended).service_ended_at, SUSPENDED_AT)

    def test_inside_a_paid_term_it_cancels_refuses_and_blanks_nothing(self):
        subscription = make_active()
        site = subscription.site
        before = self.personal_rows(subscription)

        with self.assertRaises(rules.NotDisposable) as refused:
            self.dispose(subscription, IN_TERM)

        self.assertEqual(refused.exception.reasons, (term_running(site),))
        self.assertEqual(refused.exception.cancelled, (site.public_id,))
        self.assertEqual(self.personal_rows(subscription), before)
        self.assertFalse(OperatorAuditEntry.objects.filter(action="account_disposed").exists())
        subscription = fresh(subscription)  # the cancellation is kept
        self.assertEqual(
            (subscription.state, subscription.service_ended_at), (State.CANCELLED, CYCLE_1_END)
        )

    def test_run_again_before_the_date_it_is_refused_and_writes_nothing(self):
        subscription = make_active()
        with self.assertRaises(rules.NotDisposable):
            self.dispose(subscription, IN_TERM)
        before = (snapshot(), self.personal_rows(subscription))

        with self.assertRaises(rules.NotDisposable) as refused:
            self.dispose(subscription, CYCLE_1_END - SECOND)

        self.assertEqual(refused.exception.cancelled, ())
        self.assertEqual(refused.exception.reasons, (term_running(subscription.site),))
        self.assertEqual((snapshot(), self.personal_rows(subscription)), before)

    def test_run_again_after_the_date_it_proceeds(self):
        subscription = make_active()
        with self.assertRaises(rules.NotDisposable):
            self.dispose(subscription, IN_TERM)
        before = snapshot()

        counts = self.dispose(subscription, CYCLE_1_END)

        self.assertNotIn("subscriptions_cancelled", counts)
        self.assertTrue(self.is_disposed(subscription))
        self.assertEqual(snapshot(), before)

    def test_the_dry_run_names_the_cancellation_and_the_refusal_and_changes_nothing(self):
        subscription = make_active()
        before = (snapshot(), self.personal_rows(subscription))

        counts = self.dispose(subscription, IN_TERM, dry_run=True)

        self.assertEqual(counts["subscriptions_cancelled"], 1)
        self.assertEqual(counts["refused_for_now"], [term_running(subscription.site)])
        self.assertEqual((snapshot(), self.personal_rows(subscription)), before)

    def test_the_dry_run_of_a_trial_names_the_cancellation_and_no_refusal(self):
        subscription = make_subscription()
        before = snapshot()

        counts = self.dispose(subscription, IN_TRIAL, dry_run=True)

        self.assertEqual(counts["subscriptions_cancelled"], 1)
        self.assertNotIn("refused_for_now", counts)
        self.assertEqual(snapshot(), before)

    def test_ended_exempt_and_no_subscription_are_disposed_of_as_before(self):
        ended = make_subscription(now=TRIAL_END)
        exempt = make_active(email="ben@example.test", name="bens-store")
        services.set_exempt(exempt.site, operator=self.operator, reason="approved test site")
        bare = make_site(make_account("cy@example.test"), name="cys-store")
        before = snapshot()

        for account, now in (
            (ended.site.account, TRIAL_END + days(1)),
            (exempt.site.account, IN_TERM),
            (bare.account, IN_TRIAL),
        ):
            counts = rules.dispose_account(
                account, now=now, rule="request", actor=self.operator, request_ref="DPO-1"
            )
            self.assertNotIn("subscriptions_cancelled", counts)
            self.assertTrue(rules.is_disposed(Account.objects.get(pk=account.pk)))

        self.assertEqual(snapshot(), before)

    def test_no_billing_row_is_deleted_or_blanked(self):
        subscription = make_attention()
        rows = (Subscription.objects.count(), BillingCycle.objects.count())
        transitions = list(subscription.transitions.order_by("pk").values())

        self.dispose(subscription, CYCLE_1_END + days(1))

        self.assertEqual((Subscription.objects.count(), BillingCycle.objects.count()), rows)
        after = list(subscription.transitions.order_by("pk").values())
        self.assertEqual(after[:-1], transitions)  # one row added, the `cancelled`; none changed
        self.assertEqual(after[-1]["code"], "cancelled")
        kept = fresh(subscription)
        self.assertEqual(kept.site_id, subscription.site_id)
        self.assertEqual(kept.trial_start_at, T0)

    def test_another_accounts_subscription_is_untouched(self):
        mine = make_subscription()
        theirs = make_active(email="ben@example.test", name="bens-store")
        before = list(Subscription.objects.filter(pk=theirs.pk).values())

        self.dispose(mine, IN_TERM)

        self.assertEqual(list(Subscription.objects.filter(pk=theirs.pk).values()), before)
        self.assertFalse(self.is_disposed(theirs))

    @override_settings(RETENTION_ENABLED=False)
    def test_with_retention_off_nothing_is_cancelled(self):
        subscription = make_subscription()
        before = snapshot()

        with self.assertRaises(rules.SwitchedOff):
            self.dispose(subscription, IN_TRIAL)

        self.assertEqual(snapshot(), before)


@override_settings(BILLING_ENABLED=True, RETENTION_ENABLED=True)
class DisposeAccountCommandWithBillingTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user(username="coach", password="x", is_staff=True)
        patcher = mock.patch("apps.accounts.management.commands.dispose_account.timezone")
        self.clock = patcher.start()
        self.clock.localtime.side_effect = timezone.localtime
        self.addCleanup(patcher.stop)

    def run_command(self, subscription, now, *args):
        self.clock.now.return_value = now
        out = StringIO()
        try:
            call_command(
                "dispose_account", subscription.site.account.public_id, *args, stdout=out,
                actor="coach", request_ref="DPO-2026-001",
            )
        except CommandError as refused:
            return out.getvalue(), str(refused)
        return out.getvalue(), None

    def test_a_trial_is_cancelled_and_disposed_of(self):
        subscription = make_subscription()

        out, refused = self.run_command(subscription, IN_TRIAL)

        self.assertIsNone(refused)
        self.assertIn("Disposed of account", out)
        self.assertIn("subscriptions_cancelled: 1", out)

    def test_inside_a_paid_term_it_prints_the_cancellation_and_exits_with_an_error(self):
        subscription = make_active()
        site = subscription.site

        out, refused = self.run_command(subscription, IN_TERM)

        self.assertIn(f"Cancelled the subscription of site {site.public_id}.", out)
        self.assertIn("was not disposed of and nothing was blanked.", refused)
        self.assertIn(f"Site {site.public_id} is inside a paid term", refused)
        self.assertIn(CYCLE_1_END.isoformat(), refused)
        self.assertIn("(paid_term_running)", refused)
        self.assertNotIn("ana@example.test", out + refused)
        self.assertFalse(rules.is_disposed(Account.objects.get(pk=site.account_id)))

    def test_the_dry_run_prints_what_a_real_run_would_do(self):
        subscription = make_active()
        before = snapshot()

        out, refused = self.run_command(subscription, IN_TERM, "--dry-run")

        self.assertIsNone(refused)
        self.assertIn("Dry run: nothing was changed.", out)
        self.assertIn("subscriptions_cancelled: 1", out)
        self.assertIn("A real run would be refused for now.", out)
        self.assertIn("(paid_term_running)", out)
        self.assertNotIn("refused_for_now", out)
        self.assertEqual(snapshot(), before)
