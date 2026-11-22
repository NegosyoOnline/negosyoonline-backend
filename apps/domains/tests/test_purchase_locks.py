"""Row locks and races for the purchase (PostgreSQL only: `select_for_update` does nothing on
SQLite). Two workers on one row send once; two workers against a cap of one send once."""
from datetime import timedelta
from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.domains import names, purchase
from apps.domains.models import ClaimState, Domain, DomainEvent, DomainRegistration
from apps.domains.tests.base import DOMAIN_SETTINGS, DomainsTestCase, make_bundle
from apps.sites.tests.test_concurrency import run_together


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(**DOMAIN_SETTINGS)
class PurchaseLockTests(TransactionTestCase):
    site = DomainsTestCase.site
    pay = DomainsTestCase.pay
    _n = 0

    def setUp(self):
        names._cache.update(key=None, rules=None)
        self.t0 = timezone.now().replace(microsecond=0)

    def prepared(self, name):
        """An unsent request with a fresh, valid Check recorded, ready for step 1."""
        site = self.site(paid=True)
        reg = DomainRegistration.objects.create(
            site=site, name=name, check_name=name, check_at=self.t0, check_registrable=True, check_tier="standard",
            check_registration_cost_cents=857, check_renewal_cost_cents=857, currency="USD",
        )
        claim = Domain.objects.create(site=site, name=name, state=ClaimState.AWAITING_GATE)
        return reg, claim

    def sent_names(self):
        return sorted(DomainEvent.objects.filter(kind="register_sent").values_list("detail__name", flat=True))

    def test_two_workers_on_the_same_row_send_exactly_once(self):
        reg, claim = self.prepared("brand.com")
        purchase.ledger()
        results = run_together(*[lambda: purchase._step1(reg.pk, claim.pk, self.t0) for _ in range(2)])
        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual(sorted(r[0] for r in results), ["sent", "wait"])
        self.assertEqual(self.sent_names(), ["brand.com"])
        reg.refresh_from_db()
        self.assertEqual(reg.send_count, 1)

    @override_settings(DOMAINS_DAILY_PURCHASE_CAP=1)
    def test_two_workers_against_a_cap_of_one_exactly_one_proceeds(self):
        rows = [self.prepared(n) for n in ("one.com", "two.com")]
        purchase.ledger()
        results = run_together(*[(lambda r=r, c=c: purchase._step1(r.pk, c.pk, self.t0)) for r, c in rows])
        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual(sorted(r[0] for r in results), ["sent", "wait"])
        self.assertEqual([r[1] for r in results if r[0] == "wait"], ["cap_count"])
        self.assertEqual(len(self.sent_names()), 1)
        waiting = [c for _, c in rows if Domain.objects.get(pk=c.pk).state == ClaimState.AWAITING_GATE]
        self.assertEqual(len(waiting), 1)

    @override_settings(DOMAINS_DAILY_PURCHASE_CAP=5, DOMAINS_DAILY_SPEND_CAP_CENTS=1000)
    def test_the_spend_cap_is_read_under_the_same_lock(self):
        rows = [self.prepared(n) for n in ("one.com", "two.com", "three.com")]
        purchase.ledger()
        results = run_together(*[(lambda r=r, c=c: purchase._step1(r.pk, c.pk, self.t0 + timedelta(seconds=1))) for r, c in rows])
        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        self.assertEqual(sorted(r[0] for r in results), ["sent", "wait", "wait"])
        self.assertEqual(len(self.sent_names()), 1)
