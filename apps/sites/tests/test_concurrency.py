"""Simultaneous requests. These need real row locks, so they run on PostgreSQL only (CI's
"PostgreSQL and Redis" job): SQLite's in-memory test database cannot be shared across threads."""
import threading
from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase

from apps.accounts.models import Account
from apps.common.errors import ApiError
from apps.sites import services
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.base import make_account, make_site


def run_together(*calls):
    """Run each call in its own thread and connection, released at the same moment."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def worker(index, call):
        try:
            barrier.wait(timeout=10)
            results[index] = call()
        except Exception as exc:  # the test inspects what each call raised
            results[index] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=item) for item in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def create(account_pk, subdomain):
    def call():
        return services.create_first_site(
            Account.objects.get(pk=account_pk),
            template="sante", subdomain=subdomain, display_name="Store",
        )
    return call


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
class ConcurrencyTests(TransactionTestCase):
    def test_two_simultaneous_creates_for_one_account_make_one_site(self):
        account = make_account()

        results = run_together(create(account.pk, "store-one"), create(account.pk, "store-two"))

        sites = [result for result in results if isinstance(result, Site)]
        errors = [result for result in results if isinstance(result, ApiError)]
        self.assertEqual((len(sites), len(errors)), (1, 1), results)
        self.assertEqual(errors[0].code, "site_limit_reached")
        self.assertEqual(Site.objects.count(), 1)
        self.assertEqual(Subdomain.objects.count(), 1)
        self.assertEqual(SiteBusinessProfile.objects.count(), 1)
        self.assertEqual(Onboarding.objects.get().first_site, sites[0])

    def test_many_simultaneous_identical_creates_make_one_site(self):
        account = make_account()

        results = run_together(*[create(account.pk, "same-store") for _ in range(5)])

        self.assertEqual(sum(isinstance(result, Site) for result in results), 1, results)
        self.assertEqual(Site.objects.count(), 1)
        self.assertEqual(Subdomain.objects.count(), 1)

    def test_two_accounts_racing_for_one_subdomain_make_one_site(self):
        ana, ben = make_account(), make_account("ben@example.test")

        results = run_together(create(ana.pk, "same-store"), create(ben.pk, "same-store"))

        sites = [result for result in results if isinstance(result, Site)]
        errors = [result for result in results if isinstance(result, ApiError)]
        self.assertEqual((len(sites), len(errors)), (1, 1), results)
        self.assertEqual(errors[0].code, "subdomain_taken")
        self.assertEqual(Site.objects.count(), 1)
        self.assertEqual(Subdomain.objects.get().site, sites[0])
        loser = ben if sites[0].account_id == ana.pk else ana
        self.assertFalse(Site.objects.filter(account=loser).exists())
        self.assertFalse(Onboarding.objects.filter(account=loser, first_site__isnull=False).exists())

    def test_simultaneous_updates_leave_one_consistent_row_and_one_stamp(self):
        account = make_account()
        site = make_site(account)

        def contacts():
            return services.update_site(
                Account.objects.get(pk=account.pk), site,
                {"contact_email": "a@example.test", "contact_mobile": "09171234567"},
            )

        def business_name():
            return services.update_business_profile(
                Account.objects.get(pk=account.pk), site, {"business_name": "Ana Trading"}
            )

        results = run_together(contacts, business_name, contacts, business_name)

        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        site.refresh_from_db()
        self.assertEqual(site.contact_mobile, "+639171234567")
        self.assertEqual(site.business_profile.business_name, "Ana Trading")
        self.assertIsNotNone(Onboarding.objects.get().minimum_completed_at)
