"""`billing_exempt`: an approved test site is never billed (plan D7). Set once, never cleared."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing import services
from apps.billing.management.commands.billing_exempt import Command
from apps.billing.models import Subscription
from apps.billing.tests.base import T0, TRIAL_END, days, fresh, make_subscription
from apps.billing.tests.test_transitions import snapshot
from apps.common.errors import ApiError
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site

REASON = "approved test site"


def entries():
    return OperatorAuditEntry.objects.filter(action="billing_exempt_set")


class ExemptTestCase(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_user(
            username="coach", password="x", is_staff=True
        )
        self.subscription = make_subscription()
        self.site = self.subscription.site


@override_settings(BILLING_ENABLED=True)
class SetExemptTests(ExemptTestCase):
    def test_it_sets_the_flag_and_writes_the_admin_audit_entry(self):
        self.assertTrue(services.set_exempt(self.site, operator=self.operator, reason=REASON))

        self.assertTrue(fresh(self.subscription).billing_exempt)
        entry = entries().get()
        self.assertEqual(
            (entry.operator, entry.target_type, entry.target_id, entry.reason, entry.detail),
            (self.operator, "site", self.site.public_id, REASON, {"state": "trial_active"}),
        )

    def test_it_needs_an_operator_and_a_reason(self):
        before = snapshot()

        for operator, reason in ((None, REASON), (self.operator, ""), (self.operator, "   "),
                                 (self.operator, None)):
            with self.subTest(operator=operator, reason=reason), self.assertRaises(ValueError):
                services.set_exempt(self.site, operator=operator, reason=reason)

        self.assertEqual(snapshot(), before)
        self.assertFalse(entries().exists())

    def test_a_second_run_changes_nothing(self):
        services.set_exempt(self.site, operator=self.operator, reason=REASON)
        before = snapshot()

        self.assertFalse(services.set_exempt(self.site, operator=self.operator, reason="again"))

        self.assertEqual(snapshot(), before)
        self.assertEqual(entries().count(), 1)

    def test_a_site_with_no_subscription_is_refused(self):
        bare = make_site(make_account("ben@example.test"), name="bens-store")

        with self.assertRaises(ValueError):
            services.set_exempt(bare, operator=self.operator, reason=REASON)

        self.assertFalse(entries().exists())

    def test_nothing_clears_it(self):
        """No write of the lifecycle, the sweep, the backfill or a disposal step clears it."""
        services.set_exempt(self.site, operator=self.operator, reason=REASON)
        late = TRIAL_END + days(40)
        before = snapshot()

        for write in (
            lambda: services.cancel(self.subscription, now=late),
            lambda: services.withdraw_cancellation(self.subscription, now=late),
            lambda: services.reactivated(self.subscription, paid_at=late, reference="pay-1"),
        ):
            with self.assertRaises(ApiError) as refused:
                write()
            self.assertEqual(refused.exception.code, "billing_exempt")
        self.assertEqual(services.sweep(now=late), {})
        self.assertEqual(services.backfill(now=late, apply=True), {})
        step = services.prepare_disposal(self.site.account, late)

        self.assertEqual((step.cancelled, step.reasons), ((), ()))
        self.assertEqual(snapshot(), before)
        self.assertTrue(fresh(self.subscription).billing_exempt)
        self.assertTrue(services.position(self.site, at=late).storefront_served)

    def test_the_command_has_no_option_that_clears_it(self):
        def options(command):
            return {action.dest for action in command.create_parser("manage.py", "x")._actions}

        self.assertEqual(options(Command()) - options(BaseCommand()), {"site", "actor", "reason"})


@override_settings(BILLING_ENABLED=True)
class ExemptCommandTests(ExemptTestCase):
    def run_command(self, site=None, **options):
        out = StringIO()
        options.setdefault("actor", "coach")
        options.setdefault("reason", REASON)
        call_command("billing_exempt", site or self.site.public_id, stdout=out, **options)
        return out.getvalue()

    def refused(self, **options):
        before = snapshot()
        with self.assertRaises(CommandError) as raised:
            self.run_command(**options)
        self.assertEqual(snapshot(), before)
        self.assertFalse(entries().exists())
        return str(raised.exception)

    def test_it_marks_the_site_and_names_the_operator(self):
        out = self.run_command()

        self.assertIn(f"Site {self.site.public_id} is now exempt from billing.", out)
        self.assertTrue(fresh(self.subscription).billing_exempt)
        self.assertEqual(entries().get().operator, self.operator)

    def test_a_second_run_says_so_and_writes_nothing(self):
        self.run_command()

        out = self.run_command(reason="again")

        self.assertIn("was already exempt. Nothing was changed.", out)
        self.assertEqual(entries().count(), 1)

    def test_the_actor_and_the_reason_are_required(self):
        for missing in ("actor", "reason"):
            given = {"actor": "coach", "reason": REASON}
            del given[missing]
            with self.subTest(missing=missing), self.assertRaises(CommandError):
                call_command("billing_exempt", self.site.public_id, stdout=StringIO(), **given)
        self.assertIn("needs an operator and a reason", self.refused(reason="   "))

    def test_the_actor_must_be_an_active_staff_user(self):
        users = get_user_model().objects
        users.create_user(username="visitor", password="x", is_staff=False)
        users.create_user(username="left", password="x", is_staff=True, is_active=False)

        for actor in ("nobody", "visitor", "left"):
            with self.subTest(actor=actor):
                self.assertIn("--actor must be an active staff user.", self.refused(actor=actor))

    def test_an_unknown_or_removed_site_is_refused(self):
        self.assertIn("No such site.", self.refused(site="site_00000000000000000000"))
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.assertIn("No such site.", self.refused())

    @override_settings(BILLING_ENABLED=False)
    def test_switched_off_it_refuses(self):
        self.assertIn("BILLING_ENABLED is off", self.refused())


@override_settings(BILLING_ENABLED=True)
class ExemptServedTests(ExemptTestCase):
    def test_an_exempt_site_is_served_long_after_its_trial(self):
        services.set_exempt(self.site, operator=self.operator, reason=REASON)

        for moment in (T0, TRIAL_END, TRIAL_END + days(400)):
            position = services.position(self.site, at=moment)
            self.assertEqual(
                (position.storefront_served, position.billing_only), (True, False), moment
            )
