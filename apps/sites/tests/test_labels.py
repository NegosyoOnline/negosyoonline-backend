"""The derived "Santé Site Owner" label: worked out on each read, never stored, never sent out."""
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Account, AccountAuditEvent
from apps.billing import services as billing
from apps.billing.models import Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, T0, TRIAL_END, days, make_active, make_attention, make_subscription,
    make_suspended,
)
from apps.sites import labels
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_account, make_site

LABEL = "Santé Site Owner"


def flagged(account):
    """The annotated column for one account, read fresh."""
    return labels.with_sante_site_owner(Account.objects.filter(pk=account.pk)).get().sante_site_owner


class LabelRuleTests(TestCase):
    def setUp(self):
        self.ana = make_account()

    def assert_label(self, account, expected):
        self.assertEqual(labels.labels_for(account), [LABEL] if expected else [])
        self.assertIs(flagged(account), expected)

    def test_the_wording_is_one_constant(self):
        self.assertEqual(labels.SANTE_SITE_OWNER, LABEL)

    def test_one_eligible_sante_site_gives_the_label(self):
        make_site(self.ana)

        self.assert_label(self.ana, True)

    def test_no_site_gives_no_label(self):
        self.assert_label(self.ana, False)

    def test_a_removed_site_gives_no_label(self):
        make_site(self.ana, deleted_at=timezone.now())

        self.assert_label(self.ana, False)

    def test_a_held_site_gives_no_label(self):
        make_site(self.ana, operator_hold=True)

        self.assert_label(self.ana, False)

    def test_a_site_of_another_type_gives_no_label(self):
        site = make_site(self.ana)
        Site.objects.filter(pk=site.pk).update(template="basic")

        self.assert_label(self.ana, False)

    def test_a_closed_account_gives_no_label(self):
        make_site(self.ana)
        Account.objects.filter(pk=self.ana.pk).update(closed_at=timezone.now())

        self.assert_label(Account.objects.get(pk=self.ana.pk), False)

    def test_the_label_follows_the_sites_on_each_fresh_read(self):
        first = make_site(self.ana, deleted_at=timezone.now())
        second = make_site(self.ana, name="second-store", first=False)
        self.assert_label(self.ana, True)  # one removed, one eligible

        Site.objects.filter(pk=second.pk).update(deleted_at=timezone.now())
        self.assert_label(self.ana, False)  # the second is removed too

        Site.objects.filter(pk=first.pk).update(deleted_at=None, operator_hold=True)
        self.assert_label(self.ana, False)  # back, but held

        Site.objects.filter(pk=first.pk).update(operator_hold=False)
        self.assert_label(self.ana, True)  # the hold is cleared

    def test_eligible_sante_sites_holds_only_sites_meeting_all_three_conditions(self):
        eligible = make_site(self.ana)
        make_site(self.ana, name="removed-store", first=False, deleted_at=timezone.now())
        make_site(self.ana, name="held-store", first=False, operator_hold=True)
        other = make_site(self.ana, name="other-store", first=False)
        Site.objects.filter(pk=other.pk).update(template="basic")

        self.assertEqual(list(labels.eligible_sante_sites()), [eligible])

    def test_the_sites_list_column_is_no_for_removed_held_other_type_and_closed_account(self):
        eligible = make_site(self.ana)
        removed = make_site(self.ana, name="removed-store", first=False, deleted_at=timezone.now())
        held = make_site(self.ana, name="held-store", first=False, operator_hold=True)
        closed = make_site(make_account("cora@example.test"), name="coras-store")
        Account.objects.filter(pk=closed.account_id).update(closed_at=timezone.now())

        counts = {
            site.pk: site.counts_for_sante_site_owner
            for site in labels.with_counts_for_sante_site_owner(Site.objects.all())
        }

        self.assertEqual(
            counts, {eligible.pk: True, removed.pk: False, held.pk: False, closed.pk: False}
        )


class LabelTenantIsolationTests(TestCase):
    def setUp(self):
        self.ana = make_account()
        self.ben = make_account("ben@example.test")

    def test_anas_eligible_site_gives_ben_no_label(self):
        make_site(self.ana)

        self.assertEqual(labels.labels_for(self.ana), [LABEL])
        self.assertEqual(labels.labels_for(self.ben), [])
        self.assertIs(flagged(self.ben), False)

    def test_bens_eligible_site_gives_ana_no_label_after_hers_is_removed(self):
        anas = make_site(self.ana)
        make_site(self.ben, name="bens-store")
        Site.objects.filter(pk=anas.pk).update(deleted_at=timezone.now())

        self.assertEqual(labels.labels_for(self.ana), [])
        self.assertIs(flagged(self.ana), False)
        self.assertEqual(labels.labels_for(self.ben), [LABEL])

    def test_eligible_sites_filtered_to_one_account_never_hold_anothers_row(self):
        anas = make_site(self.ana)
        bens = make_site(self.ben, name="bens-store")

        self.assertEqual(list(labels.eligible_sante_sites().filter(account=self.ana)), [anas])
        self.assertEqual(list(labels.eligible_sante_sites().filter(account=self.ben)), [bens])

    def test_one_query_flags_each_account_by_its_own_sites(self):
        make_site(self.ana)
        make_site(self.ben, name="bens-store", operator_hold=True)

        with self.assertNumQueries(1):
            flags = dict(
                labels.with_sante_site_owner(Account.objects.all()).values_list(
                    "email", "sante_site_owner"
                )
            )

        self.assertEqual(flags, {"ana@example.test": True, "ben@example.test": False})


class LabelWritesNothingTests(TestCase):
    MODELS = (Account, AccountAuditEvent, Site, Subdomain, SiteBusinessProfile, Onboarding)

    def snapshot(self):
        return [list(model.objects.order_by("pk").values()) for model in self.MODELS]

    def test_reading_the_label_writes_nothing(self):
        ana = make_account()
        make_site(ana)
        ben = make_account("ben@example.test")
        before = self.snapshot()

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(labels.labels_for(ana), [LABEL])
            self.assertEqual(labels.labels_for(ben), [])
            list(labels.with_sante_site_owner(Account.objects.all()))
            list(labels.with_counts_for_sante_site_owner(Site.objects.all()))
            list(labels.eligible_sante_sites())

        self.assertEqual(self.snapshot(), before)
        self.assertTrue(queries.captured_queries)
        for query in queries.captured_queries:
            self.assertTrue(query["sql"].lstrip().upper().startswith("SELECT"), query["sql"])

    def test_no_model_has_a_column_for_the_label(self):
        for model in (Account, Site):
            names = [field.name for field in model._meta.get_fields()]
            self.assertFalse([n for n in names if "owner" in n or "label" in n], names)


def walk(value):
    """Every key and every scalar value in a JSON body, as text."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from walk(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from walk(inner)
    else:
        yield str(value)


class NoEndpointReturnsTheLabelTests(SitesApiTestCase):
    def test_account_setup_and_sites_hold_no_key_or_value_naming_the_label(self):
        self.create_site()
        self.assertEqual(labels.labels_for(self.account), [LABEL])  # Ana does have the label

        for path in ("/v1/account/", "/v1/account/setup/", "/v1/sites/"):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200, response.content)
                for text in walk(response.json()):
                    lowered = text.lower()
                    self.assertNotIn("site owner", lowered)
                    self.assertNotIn("site_owner", lowered)
                    self.assertNotIn("siteowner", lowered)
                self.assertNotIn(LABEL, response.content.decode())


BILLING_STATES = [state for state, _ in Subscription.State.choices]


@override_settings(BILLING_ENABLED=True)
class StateFilterParityTests(TestCase):
    """`billing.state_filter` is the label's one-query copy of `billing.position`. One
    subscription per row of the table in plan section 5.3; the two agree at each boundary."""

    def assert_agree(self, subscription, *instants):
        site = subscription.site
        for at in instants:
            reported = billing.position(site, at=at).state
            self.assertIsNotNone(reported)
            for state in BILLING_STATES:
                with self.subTest(at=at, state=state, reported=reported):
                    matched = Site.objects.filter(
                        billing.state_filter(at, [state]), pk=site.pk
                    ).exists()
                    self.assertIs(matched, state == reported)
            with self.subTest(at=at, states="all"):
                self.assertTrue(
                    Site.objects.filter(billing.state_filter(at, BILLING_STATES), pk=site.pk).exists()
                )
        return reported

    def test_trial_before_its_end(self):
        self.assertEqual(
            self.assert_agree(make_subscription(), T0, TRIAL_END - SECOND), "trial_active"
        )

    def test_trial_at_or_past_its_end_with_no_method_swept_or_not(self):
        subscription = make_subscription()
        self.assertEqual(
            self.assert_agree(subscription, TRIAL_END, TRIAL_END + days(30)),
            "cancelled_no_payment_method",
        )

        billing.sweep(now=TRIAL_END)
        self.assertEqual(
            self.assert_agree(subscription, TRIAL_END, TRIAL_END + days(30)),
            "cancelled_no_payment_method",
        )

    def test_trial_past_its_end_with_a_method_and_no_outcome(self):
        subscription = make_subscription()
        Subscription.objects.filter(pk=subscription.pk).update(
            method_at_trial_end=Subscription.MethodAtTrialEnd.YES
        )

        self.assertEqual(
            self.assert_agree(
                subscription, TRIAL_END - SECOND, TRIAL_END, TRIAL_END + days(7) - SECOND
            ),
            "trial_active",
        )
        self.assertEqual(
            self.assert_agree(subscription, TRIAL_END + days(7), TRIAL_END + days(8)),
            "suspended",
        )

    def test_paid_cycle_and_the_7_days_after_its_end(self):
        subscription = make_active()

        self.assertEqual(
            self.assert_agree(
                subscription, TRIAL_END, CYCLE_1_END - SECOND, CYCLE_1_END,
                CYCLE_1_END + days(7) - SECOND,
            ),
            "active",
        )
        self.assertEqual(
            self.assert_agree(subscription, CYCLE_1_END + days(7), CYCLE_1_END + days(40)),
            "suspended",
        )

    def test_payment_requires_attention_and_the_7_days_from_the_due_date(self):
        subscription = make_attention()

        self.assertEqual(
            self.assert_agree(subscription, CYCLE_1_END, CYCLE_1_END + days(7) - SECOND),
            "payment_requires_attention",
        )
        self.assertEqual(self.assert_agree(subscription, CYCLE_1_END + days(7)), "suspended")

    def test_suspended(self):
        self.assertEqual(
            self.assert_agree(make_suspended(), CYCLE_1_END + days(7), CYCLE_1_END + days(8)),
            "suspended",
        )

    def test_cancelled_before_and_after_its_service_end(self):
        subscription = make_active()
        billing.cancel(subscription, now=TRIAL_END + days(3))

        self.assertEqual(
            self.assert_agree(
                subscription, CYCLE_1_END - SECOND, CYCLE_1_END, CYCLE_1_END + days(30)
            ),
            "cancelled",
        )

    def test_a_cancelled_trial(self):
        subscription = make_subscription()
        billing.cancel(subscription, now=T0 + days(2))

        self.assertEqual(self.assert_agree(subscription, T0 + days(2), TRIAL_END), "cancelled")

    def test_exempt_reports_the_stored_state_whatever_the_time(self):
        trial = make_subscription()
        ended = make_subscription(email="ben@example.test", name="bens-store")
        billing.sweep(now=TRIAL_END)
        ended.refresh_from_db()
        Subscription.objects.filter(pk=trial.pk).update(
            billing_exempt=True, state=Subscription.State.TRIAL_ACTIVE,
            method_at_trial_end=Subscription.MethodAtTrialEnd.UNDECIDED,
        )
        Subscription.objects.filter(pk=ended.pk).update(billing_exempt=True)

        self.assertEqual(self.assert_agree(trial, T0, TRIAL_END + days(400)), "trial_active")
        self.assertEqual(
            self.assert_agree(ended, T0, TRIAL_END + days(400)), "cancelled_no_payment_method"
        )

    def test_a_site_with_no_subscription_matches_no_state(self):
        site = make_site(make_account())

        self.assertIsNone(billing.position(site, at=T0).state)
        self.assertFalse(Site.objects.filter(billing.state_filter(T0, BILLING_STATES)).exists())
        self.assertFalse(Site.objects.filter(billing.state_filter(T0, [])).exists())

    def test_each_site_is_matched_by_its_own_subscription_only(self):
        trial = make_subscription()
        active = make_active(email="ben@example.test", name="bens-store")
        at = TRIAL_END + days(1)  # Ana's trial ended with no method; Ben has paid

        self.assertEqual(
            list(Site.objects.filter(billing.state_filter(at, ["active"]))), [active.site]
        )
        self.assertEqual(
            list(Site.objects.filter(billing.state_filter(at, ["cancelled_no_payment_method"]))),
            [trial.site],
        )


@override_settings(BILLING_ENABLED=True)
class LabelWithBillingTests(TestCase):
    """With billing on, the label also needs a subscription on trial, active or needing
    payment attention at this moment. The Phase 6 rule is unchanged beside it."""

    def setUp(self):
        patcher = mock.patch("apps.sites.labels.timezone")
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.at(T0 + days(1))

    def at(self, moment):
        self.clock.now.return_value = moment

    def assert_label(self, subscription, expected):
        account = subscription.site.account
        self.assertEqual(labels.labels_for(account), [LABEL] if expected else [])
        self.assertIs(flagged(account), expected)
        counts = labels.with_counts_for_sante_site_owner(Site.objects.filter(pk=subscription.site_id))
        self.assertIs(counts.get().counts_for_sante_site_owner, expected)
        self.assertEqual(
            labels.eligible_sante_sites().filter(pk=subscription.site_id).exists(), expected
        )

    def test_on_trial(self):
        subscription = make_subscription()

        self.assert_label(subscription, True)
        self.at(TRIAL_END - SECOND)
        self.assert_label(subscription, True)

    def test_a_trial_that_ended_with_no_method_loses_it_at_the_trial_end_swept_or_not(self):
        subscription = make_subscription()
        self.at(TRIAL_END)

        self.assert_label(subscription, False)
        billing.sweep(now=TRIAL_END)
        self.assert_label(subscription, False)

    def test_active_through_its_cycle_and_the_7_days_after(self):
        subscription = make_active()

        for moment in (TRIAL_END + days(1), CYCLE_1_END, CYCLE_1_END + days(7) - SECOND):
            with self.subTest(moment=moment):
                self.at(moment)
                self.assert_label(subscription, True)

        self.at(CYCLE_1_END + days(7))
        self.assert_label(subscription, False)  # suspended by the time; the sweep never ran

    def test_needing_payment_attention(self):
        subscription = make_attention()
        self.at(CYCLE_1_END + days(7) - SECOND)

        self.assert_label(subscription, True)

        self.at(CYCLE_1_END + days(7))
        self.assert_label(subscription, False)

    def test_suspended(self):
        subscription = make_suspended()
        self.at(CYCLE_1_END + days(8))

        self.assert_label(subscription, False)

    def test_a_cancellation_does_not_count_even_inside_its_paid_period(self):
        subscription = make_active()
        self.at(TRIAL_END + days(2))
        self.assert_label(subscription, True)

        billing.cancel(subscription, now=TRIAL_END + days(3))
        self.at(TRIAL_END + days(4))

        self.assertTrue(billing.position(subscription.site, at=TRIAL_END + days(4)).storefront_served)
        self.assert_label(subscription, False)

    def test_a_site_with_no_subscription(self):
        ana = make_account()
        make_site(ana)

        self.assertEqual(labels.labels_for(ana), [])
        self.assertIs(flagged(ana), False)

    def test_an_exempt_site_counts_on_its_stored_state(self):
        on_trial = make_subscription()
        ended = make_subscription(email="ben@example.test", name="bens-store")
        Subscription.objects.filter(pk=on_trial.pk).update(billing_exempt=True)
        self.at(TRIAL_END + days(400))
        billing.sweep(now=TRIAL_END)  # skips the exempt one; ends Ben's
        Subscription.objects.filter(pk=ended.pk).update(billing_exempt=True)

        self.assert_label(on_trial, True)
        self.assert_label(ended, False)  # served, but its stored state is an ended one

    def test_the_phase_6_rule_still_holds_beside_a_running_subscription(self):
        removed = make_subscription()
        held = make_subscription(email="ben@example.test", name="bens-store")
        closed = make_subscription(email="cora@example.test", name="coras-store")
        other = make_subscription(email="dan@example.test", name="dans-store")
        Site.objects.filter(pk=removed.site_id).update(deleted_at=timezone.now())
        Site.objects.filter(pk=held.site_id).update(operator_hold=True)
        Account.objects.filter(pk=closed.site.account_id).update(closed_at=timezone.now())
        Site.objects.filter(pk=other.site_id).update(template="basic")

        for subscription in (removed, held, closed, other):
            with self.subTest(site=subscription.site.display_name, pk=subscription.pk):
                self.assertEqual(labels.labels_for(subscription.site.account), [])
                self.assertIs(flagged(subscription.site.account), False)
        # A closed account's site is still an eligible site; the account is what is refused.
        self.assertEqual(list(labels.eligible_sante_sites()), [closed.site])

    def test_one_running_site_is_enough_and_anothers_gives_nothing(self):
        ended = make_subscription()
        ana = ended.site.account
        ben = make_active(email="ben@example.test", name="bens-store").site.account
        self.at(TRIAL_END + days(1))
        self.assertEqual(labels.labels_for(ana), [])  # Ben's paid site gives Ana nothing
        self.assertEqual(labels.labels_for(ben), [LABEL])

        second = make_site(ana, name="second-store", first=False)
        Subscription.objects.filter(
            pk=billing.start_subscription(second, ana, now=TRIAL_END + days(1)).pk
        ).update(billing_exempt=True, state=Subscription.State.ACTIVE)

        self.assertEqual(labels.labels_for(ana), [LABEL])

    def test_it_is_still_one_query_and_a_read_only_one(self):
        make_suspended(email="ben@example.test", name="bens-store")  # its sweep runs first
        make_subscription()
        make_account("cora@example.test")

        with CaptureQueriesContext(connection) as queries:
            flags = dict(
                labels.with_sante_site_owner(Account.objects.all()).values_list(
                    "email", "sante_site_owner"
                )
            )

        self.assertEqual(flags, {
            "ana@example.test": True, "ben@example.test": False, "cora@example.test": False,
        })
        self.assertEqual(len(queries.captured_queries), 1)
        self.assertTrue(queries.captured_queries[0]["sql"].lstrip().upper().startswith("SELECT"))

    def test_with_the_switch_off_the_subscription_is_not_read(self):
        subscription = make_suspended()
        self.at(CYCLE_1_END + days(8))
        self.assert_label(subscription, False)

        with override_settings(BILLING_ENABLED=False):
            self.assert_label(subscription, True)
            with CaptureQueriesContext(connection) as queries:
                labels.labels_for(subscription.site.account)
            self.assertNotIn("billing_", queries.captured_queries[0]["sql"])
