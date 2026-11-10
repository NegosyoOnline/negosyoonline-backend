"""Helpers shared by the sites tests."""
from django.utils import timezone

from apps.accounts.models import Account
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain


def make_account(email="ana@example.test", verified=True):
    return Account.objects.create(
        email=email, email_verified_at=timezone.now() if verified else None
    )


def make_site(account, name="anas-store", display_name="Ana's Store", first=True, **fields):
    """A site with its ledger row and profile, written directly (no service rules applied)."""
    site = Site.objects.create(
        account=account, template="sante", display_name=display_name, **fields
    )
    Subdomain.objects.create(name=name, site=site)
    SiteBusinessProfile.objects.create(site=site)
    if first:
        Onboarding.objects.update_or_create(account=account, defaults={"first_site": site})
    return site
