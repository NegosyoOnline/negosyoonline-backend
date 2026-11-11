"""Simultaneous writes. These need real row locks, so they run on PostgreSQL only (CI's
"PostgreSQL and Redis" job)."""
from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts import retention as rules
from apps.accounts.models import Account
from apps.operators.models import OperatorAuditEntry
from apps.sante import services
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sante.tests.base import operator
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site
from apps.sites.tests.test_concurrency import run_together


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(SANTE_SETTINGS_ENABLED=True, RETENTION_ENABLED=True)
class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.op = operator()
        self.account = make_account()
        self.a = make_site(self.account, "site-a", "A")
        self.b = make_site(make_account("ben@example.test"), "site-b", "B")

    def set_owner(self, site_pk):
        def call():
            return services.set_catalog_owner(Site.objects.get(pk=site_pk), operator=self.op)
        return call

    def test_concurrent_swaps_leave_exactly_one_owner(self):
        results = run_together(self.set_owner(self.a.pk), self.set_owner(self.b.pk))
        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual(SanteCatalogOwner.objects.count(), 1)
        self.assertIn(services.catalog_owner_site(), [self.a, self.b])
        entries = OperatorAuditEntry.objects.filter(action="sante_catalog_owner_set").order_by("pk")
        self.assertEqual(entries.count(), 2)
        # The second writer saw the first one's owner: the two entries form one chain.
        self.assertEqual(entries[1].detail["old_site"], entries[0].detail["new_site"])

    def test_two_first_writes_of_a_sponsor_id_make_one_row(self):
        def write(value):
            def call():
                account = Account.objects.get(pk=self.account.pk)
                return services.update_settings(account, self.a, {"sponsor_id": value})
            return call

        results = run_together(write("ONE-1"), write("TWO-2"))
        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        row = SanteSiteSettings.objects.get(site=self.a)
        self.assertIn(row.sponsor_id, ("ONE-1", "TWO-2"))

    def test_a_disposal_and_naming_its_site_the_owner_never_leave_a_removed_owner(self):
        def dispose():
            account = Account.objects.get(pk=self.account.pk)
            return rules.dispose_account(account, now=timezone.now(), rule=rules.REQUEST)

        results = run_together(dispose, self.set_owner(self.a.pk))
        owner = services.catalog_owner_site()
        site = Site.objects.get(pk=self.a.pk)
        if owner is None:  # the disposal won: the site is closed and was refused as the owner
            self.assertIsNotNone(site.deleted_at)
            self.assertIsInstance(results[1], ValueError)
        else:  # the operator won: the disposal was refused and the site is still live
            self.assertEqual(owner, site)
            self.assertIsNone(site.deleted_at)
            self.assertIsInstance(results[0], rules.NotDisposable)
