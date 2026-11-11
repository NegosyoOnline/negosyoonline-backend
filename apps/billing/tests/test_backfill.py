"""The backfill: a subscription for each site made before billing (plan D6)."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account
from apps.billing import services
from apps.billing.models import Subscription
from apps.billing.tests.base import T0, days, make_subscription
from apps.billing.tests.test_transitions import codes, snapshot
from apps.sites.tests.base import make_account, make_site

State = Subscription.State
NOW = T0 + days(20)
COUNTS = {"sites_without_subscription": 2, "trial_still_running": 1, "trial_already_ended": 1}


def site_of(email, name, created_at, **fields):
    """A site made before billing: it has no subscription."""
    account = make_account(email)
    Account.objects.filter(pk=account.pk).update(created_at=created_at)
    account.refresh_from_db()
    return make_site(account, name=name, **fields)


@override_settings(BILLING_ENABLED=True)
class BackfillTests(TestCase):
    def setUp(self):
        self.old = site_of("old@example.test", "old-store", T0)
        self.young = site_of("young@example.test", "young-store", NOW - days(3))
        self.removed = site_of(
            "gone@example.test", "gone-store", T0, deleted_at=timezone.now()
        )
        self.has_one = make_subscription("has@example.test", "has-store", start=NOW - days(1))

    def test_the_dry_run_counts_and_writes_nothing(self):
        before = snapshot()

        self.assertEqual(services.backfill(now=NOW), COUNTS)

        self.assertEqual(snapshot(), before)

    def test_apply_creates_one_for_each_site_without_one(self):
        self.assertEqual(services.backfill(now=NOW, apply=True), COUNTS)

        self.assertEqual(Subscription.objects.count(), 3)
        young = Subscription.objects.get(site=self.young)
        self.assertEqual(young.state, State.TRIAL_ACTIVE)
        self.assertEqual(young.trial_start_at, self.young.account.created_at)
        self.assertEqual(young.trial_end_at, self.young.account.created_at + days(14))
        self.assertEqual(codes(young), ["trial_started"])
        self.assertEqual(
            set(young.transitions.values_list("actor", flat=True)), {"backfill"}
        )
        self.assertEqual(
            list(young.cycles.values_list("sequence", "kind", "settlement")),
            [(0, "trial", "not_billable")],
        )

    def test_an_old_accounts_site_is_ended_at_day_14_with_no_dates(self):
        services.backfill(now=NOW, apply=True)

        old = Subscription.objects.get(site=self.old)
        self.assertEqual(old.state, State.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(old.trial_start_at, T0)
        self.assertEqual(old.service_ended_at, T0 + days(14))  # not the backfill's own time
        self.assertEqual((old.download_until, old.deletion_due_at), (None, None))
        self.assertEqual(codes(old), ["trial_started", "trial_ended_no_method"])
        self.assertEqual(
            list(old.transitions.order_by("pk").values_list("actor", "effective_at")),
            [("backfill", T0), ("backfill", T0 + days(14))],
        )
        self.assertFalse(services.position(self.old, at=NOW).storefront_served)

    def test_a_removed_site_gets_none(self):
        services.backfill(now=NOW, apply=True)

        self.assertFalse(Subscription.objects.filter(site=self.removed).exists())

    def test_a_site_that_has_one_is_left_as_it_is(self):
        before = list(Subscription.objects.filter(pk=self.has_one.pk).values())

        services.backfill(now=NOW, apply=True)

        self.assertEqual(list(Subscription.objects.filter(pk=self.has_one.pk).values()), before)
        self.assertEqual(codes(self.has_one), ["trial_started"])

    def test_a_second_apply_creates_none(self):
        services.backfill(now=NOW, apply=True)
        before = snapshot()

        self.assertEqual(services.backfill(now=NOW, apply=True), {})
        self.assertEqual(services.backfill(now=NOW), {})

        self.assertEqual(snapshot(), before)

    @override_settings(BILLING_ENABLED=False)
    def test_switched_off_it_reads_and_writes_nothing(self):
        before = snapshot()

        self.assertEqual(services.backfill(now=NOW, apply=True), {})

        self.assertEqual(snapshot(), before)


@override_settings(BILLING_ENABLED=True)
class BackfillCommandTests(TestCase):
    def setUp(self):
        self.old = site_of("old@example.test", "old-store", T0)
        self.young = site_of("young@example.test", "young-store", NOW - days(3))
        patcher = mock.patch("apps.billing.services.timezone")
        patcher.start().now.return_value = NOW
        self.addCleanup(patcher.stop)

    def run_command(self, *args):
        out = StringIO()
        call_command("billing_backfill_subscriptions", *args, stdout=out)
        return out.getvalue()

    def test_without_apply_it_prints_the_counts_and_writes_nothing(self):
        out = self.run_command()

        self.assertIn("Dry run. Nothing was written.", out)
        for name, count in COUNTS.items():
            self.assertIn(f"{name}: {count}", out)
        self.assertFalse(Subscription.objects.exists())

    def test_with_apply_it_writes_them_and_a_second_run_finds_none(self):
        out = self.run_command("--apply")

        self.assertIn("Backfill done.", out)
        self.assertIn("sites_without_subscription: 2", out)
        self.assertEqual(Subscription.objects.count(), 2)
        self.assertIn("Every site already has a subscription.", self.run_command("--apply"))
        self.assertEqual(Subscription.objects.count(), 2)

    def test_the_output_holds_counts_only(self):
        out = self.run_command("--apply")

        for value in ("old@example.test", "old-store", self.old.public_id, "Ana's Store"):
            self.assertNotIn(value, out)

    @override_settings(BILLING_ENABLED=False)
    def test_switched_off_it_says_so_and_writes_nothing(self):
        out = self.run_command("--apply")

        self.assertIn("BILLING_ENABLED is off.", out)
        self.assertFalse(Subscription.objects.exists())
