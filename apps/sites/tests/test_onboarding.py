from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.sites import onboarding
from apps.sites.models import Onboarding, Site, SiteBusinessProfile
from apps.sites.tests.base import make_account, make_site

ALL = ["business_name", "site_name", "public_contact"]


def complete(site):
    Site.objects.filter(pk=site.pk).update(
        contact_email="hello@example.test", contact_mobile="+639171234567"
    )
    SiteBusinessProfile.objects.filter(site=site).update(business_name="Ana Trading")


def stamp(account):
    with transaction.atomic():
        onboarding.stamp(account)


class EvaluateTests(TestCase):
    def setUp(self):
        self.account = make_account()

    def test_a_fresh_account_is_not_started_with_all_three_missing(self):
        setup = onboarding.evaluate(self.account)

        self.assertEqual(setup.state, "not_started")
        self.assertIsNone(setup.blocked_reason)
        self.assertEqual(setup.missing, ALL)
        self.assertIsNone(setup.first_site)
        self.assertIsNone(setup.started_at)
        self.assertIsNone(setup.minimum_completed_at)

    def test_an_unverified_owner_is_blocked_before_any_other_rule(self):
        account = make_account("new@example.test", verified=False)
        self.assertEqual(onboarding.evaluate(account).blocked_reason, "owner_email_unverified")

        complete(make_site(account, name="new-store"))
        setup = onboarding.evaluate(account)

        self.assertEqual(setup.state, "blocked")
        self.assertEqual(setup.blocked_reason, "owner_email_unverified")

    def test_a_removed_first_site_is_blocked(self):
        site = make_site(self.account)
        Site.objects.filter(pk=site.pk).update(deleted_at=timezone.now())

        setup = onboarding.evaluate(self.account)

        self.assertEqual(setup.state, "blocked")
        self.assertEqual(setup.blocked_reason, "first_site_removed")
        self.assertEqual(setup.first_site, site)

    def test_a_site_without_business_name_or_contacts_is_in_progress(self):
        site = make_site(self.account)

        setup = onboarding.evaluate(self.account)

        self.assertEqual(setup.state, "in_progress")
        self.assertEqual(setup.missing, ["business_name", "public_contact"])
        self.assertEqual(setup.first_site, site)

    def test_a_blank_site_name_is_missing(self):
        make_site(self.account, display_name="")

        self.assertIn("site_name", onboarding.evaluate(self.account).missing)

    def test_public_contact_needs_both_email_and_mobile(self):
        site = make_site(self.account)
        SiteBusinessProfile.objects.filter(site=site).update(business_name="Ana Trading")

        Site.objects.filter(pk=site.pk).update(contact_email="hello@example.test")
        self.assertEqual(onboarding.evaluate(self.account).missing, ["public_contact"])

        Site.objects.filter(pk=site.pk).update(contact_email="", contact_mobile="+639171234567")
        self.assertEqual(onboarding.evaluate(self.account).missing, ["public_contact"])

    def test_all_required_fields_give_minimum_complete(self):
        complete(make_site(self.account))

        setup = onboarding.evaluate(self.account)

        self.assertEqual(setup.state, "minimum_complete")
        self.assertEqual(setup.missing, [])

    def test_evaluate_never_writes(self):
        with self.assertNumQueries(1):
            onboarding.evaluate(self.account)
        self.assertFalse(Onboarding.objects.exists())

        complete(make_site(self.account))
        before = Onboarding.objects.values().get()
        with self.assertNumQueries(1):
            onboarding.evaluate(self.account)
        self.assertEqual(Onboarding.objects.values().get(), before)
        self.assertIsNone(before["minimum_completed_at"])

    def test_evaluate_ignores_another_accounts_site(self):
        complete(make_site(make_account("ben@example.test"), name="bens-store"))

        self.assertEqual(onboarding.evaluate(self.account).state, "not_started")

    def test_a_second_later_site_is_never_the_first_site(self):
        first = make_site(self.account)
        complete(make_site(self.account, name="second-store", first=False))

        setup = onboarding.evaluate(self.account)

        self.assertEqual(setup.first_site, first)
        self.assertEqual(setup.state, "in_progress")


class StampTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.site = make_site(self.account)

    def record(self):
        return Onboarding.objects.get(account=self.account)

    def test_stamp_sets_started_at_once(self):
        stamp(self.account)
        started = self.record().started_at
        self.assertIsNotNone(started)
        self.assertIsNone(self.record().minimum_completed_at)

        stamp(self.account)

        self.assertEqual(self.record().started_at, started)

    def test_stamp_sets_minimum_completed_at_once(self):
        complete(self.site)
        stamp(self.account)
        completed = self.record().minimum_completed_at
        self.assertIsNotNone(completed)

        stamp(self.account)

        self.assertEqual(self.record().minimum_completed_at, completed)

    def test_clearing_a_contact_returns_to_in_progress_and_keeps_the_stamp(self):
        complete(self.site)
        stamp(self.account)
        completed = self.record().minimum_completed_at

        Site.objects.filter(pk=self.site.pk).update(contact_mobile="")
        stamp(self.account)
        setup = onboarding.evaluate(self.account)

        self.assertEqual(setup.state, "in_progress")
        self.assertEqual(setup.missing, ["public_contact"])
        self.assertEqual(setup.minimum_completed_at, completed)
