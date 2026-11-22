"""Helpers for the domains tests. No network anywhere: adapters run over the fakes."""
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Subscription
from apps.billing.tests.base import method_on_file
from apps.domains import names
from apps.domains.models import (
    ClaimState, Domain, DomainHostname, DomainRegistration, RegistrationState,
)
from apps.domains.probe import ProbeResult
from apps.domains.providers import Providers
from apps.domains.providers.cloudflare_dns import CloudflareDnsAdapter
from apps.domains.providers.cloudflare_registrar import RegistrarAdapter
from apps.domains.providers.fake import FakeCloudflare, FakeRegistrar, FakeRegistrarRead, FakeVercel
from apps.domains.providers.vercel import VercelAdapter
from apps.sites.tests.base import make_account, make_site

PSL = str(Path(__file__).parent / "fixtures" / "psl_fixture.dat")
PLATFORM = ("negosyonline.com", "negosyoonline.com", "santebarleyph.com")
ACCOUNT = "acct_test"
PROJECT = "prj_test"


def make_bundle():
    vercel, cloudflare = FakeVercel(project_id=PROJECT), FakeCloudflare(account_id=ACCOUNT)
    registrar_fake = FakeRegistrar(account_id=ACCOUNT)
    return Providers(
        vercel=VercelAdapter(token="tok-vercel", project_id=PROJECT, transport=vercel),
        dns=CloudflareDnsAdapter(token="tok-dns", account_id=ACCOUNT, transport=cloudflare),
        registrar_read=FakeRegistrarRead(),
        registrar=RegistrarAdapter(token="tok-registrar", account_id=ACCOUNT, transport=registrar_fake),
        fakes={"vercel": vercel, "cloudflare": cloudflare, "registrar": registrar_fake},
    )


def probe_returning(site_id, ok=True):
    """A probe function that answers like the storefronts server would (or fails)."""
    calls = []

    def fn(hostname, nonce):
        calls.append(hostname)
        return ProbeResult(ok, site=site_id if ok else "", reason="" if ok else "unreachable")

    fn.calls = calls
    return fn


DOMAIN_SETTINGS = dict(
    DOMAINS_ENABLED=True, DOMAINS_PROVIDER="fake", DOMAINS_PLATFORM_ZONES=PLATFORM,
    DOMAINS_PSL_FILE=PSL, CLOUDFLARE_ACCOUNT_ID=ACCOUNT, SITE_PARENT_HOST="negosyotest.com",
    DOMAINS_PROVIDER_PAUSED=False, BILLING_ENABLED=True, DOMAINS_PURCHASE_ENABLED=True,
    DOMAINS_MAX_COST_CENTS=1200, DOMAINS_DAILY_PURCHASE_CAP=3, DOMAINS_DAILY_SPEND_CAP_CENTS=4000,
    DOMAINS_ALLOWED_TLDS=("com", "net"), DOMAINS_REGISTER_AUTO_RENEW=False,
)


@override_settings(**DOMAIN_SETTINGS)
class DomainsTestCase(TestCase):
    _n = 0

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        names._cache.update(key=None, rules=None)

    def site(self, label=None, paid=False):
        """A Santé site on its trial; `paid=True` confirms a first payment (state ACTIVE, one PAID cycle)."""
        DomainsTestCase._n += 1
        label = label or f"store{DomainsTestCase._n}"
        account = make_account(f"{label}@example.test")
        site = make_site(account, name=label)
        billing.start_subscription(site, account)
        if paid:
            self.pay(site)
        return site

    def pay(self, site):
        """Confirm the site's first payment (an early one, as the trial runs): state ACTIVE, one PAID cycle."""
        subscription = Subscription.objects.get(site=site)
        if not billing.has_paid_cycle(subscription):
            with method_on_file():
                billing.first_payment_succeeded(
                    subscription, paid_at=timezone.now(), reference=f"first-{subscription.pk}", early=True
                )

    def claim(self, site=None, name="brand.com", state=ClaimState.REGISTERED, registration=RegistrationState.SUCCEEDED):
        """A claim with its registration (sent exactly once when not `intent`), on a paid site."""
        site = site or self.site()
        self.pay(site)
        extra = {}
        if registration != RegistrationState.INTENT:
            extra = {"register_sent_at": timezone.now(), "send_count": 1}
        DomainRegistration.objects.create(site=site, name=name, state=registration, **extra)
        return Domain.objects.create(site=site, name=name, state=state)

    def live_claim(self, site=None, name="brand.com"):
        claim = self.claim(site, name, ClaimState.LIVE)
        DomainHostname.objects.create(claim=claim, role="www", hostname=f"www.{name}", holding=True)
        DomainHostname.objects.create(claim=claim, role="apex", hostname=name, holding=True)
        return claim
