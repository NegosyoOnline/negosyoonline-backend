"""Which providers a run may use: `off` (none), `fake` (in-memory, DEBUG only) or `real`
(adapters on the real transport, the worker container only). `current()` is what the tasks call;
it returns None while the provider is off, paused or refused, and never raises.
"""
import logging
from dataclasses import dataclass

from django.conf import settings

from apps.domains.providers.cloudflare_dns import CloudflareDnsAdapter
from apps.domains.providers.cloudflare_registrar import RegistrarAdapter
from apps.domains.providers.fake import FakeCloudflare, FakeRegistrar, FakeResend, FakeVercel
from apps.domains.providers.resend_domains import ResendDomainsAdapter
from apps.domains.providers.vercel import VercelAdapter

logger = logging.getLogger(__name__)


@dataclass
class Providers:
    vercel: object
    dns: object
    registrar_read: object = None  # the registration read of the daily sweep (the Registrar adapter)
    registrar: object = None  # the Registrar adapter: check, register, status, get
    resend: object = None  # the Resend domains adapter (B3; used only while DOMAINS_EMAIL_ENABLED)
    fakes: object = None


_fake_bundle = None


def fake_bundle():
    """The in-process fake set (one per process), built over the real adapters."""
    global _fake_bundle
    if _fake_bundle is None:
        vercel, cloudflare = FakeVercel(project_id="prj_fake"), FakeCloudflare(account_id="acct_fake")
        registrar_fake = FakeRegistrar(account_id="acct_fake")
        registrar = RegistrarAdapter(token="fake", account_id="acct_fake", transport=registrar_fake)
        resend_fake = FakeResend()
        _fake_bundle = Providers(
            vercel=VercelAdapter(token="fake", project_id="prj_fake", transport=vercel),
            dns=CloudflareDnsAdapter(token="fake", account_id="acct_fake", transport=cloudflare),
            registrar_read=registrar, registrar=registrar,
            resend=ResendDomainsAdapter(token="fake", region="us-east-1", transport=resend_fake),
            fakes={"vercel": vercel, "cloudflare": cloudflare, "registrar": registrar_fake, "resend": resend_fake},
        )
    return _fake_bundle


def reset_fakes():
    global _fake_bundle
    _fake_bundle = None


def current():
    """The providers for this run, or None: off, paused, or a fake without DEBUG."""
    if not settings.DOMAINS_ENABLED or settings.DOMAINS_PROVIDER_PAUSED:
        return None
    mode = settings.DOMAINS_PROVIDER
    if mode == "fake":
        return fake_bundle() if settings.DEBUG else None
    if mode == "real":
        registrar = RegistrarAdapter()
        return Providers(vercel=VercelAdapter(), dns=CloudflareDnsAdapter(), registrar_read=registrar, registrar=registrar,
                         resend=ResendDomainsAdapter())
    return None
