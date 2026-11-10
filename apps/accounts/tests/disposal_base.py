"""Rows on every table an account disposal touches, and a copy of them to compare with."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    AccountMobile,
    ExternalIdentity,
    MobileOtpChallenge,
    PolicyAcceptance,
    Session,
)
from apps.notifications.models import EmailMessage
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.base import make_site

EMAIL = "ana@example.test"
OTHER_EMAIL = "ben@example.test"
MOBILE = "+639171234567"  # both test accounts hold this same number
TABLES = (
    Account, Session, ExternalIdentity, PolicyAcceptance, AccountAuditEvent,
    AccountMobile, MobileOtpChallenge,
    Site, SiteBusinessProfile, Subdomain, Onboarding, EmailMessage, OperatorAuditEntry,
)
# Every personal value `full_account("ana@example.test", "ana")` writes. None may survive a
# disposal, and none may reach an audit entry or a command's output.
ANA_VALUES = (
    EMAIL, "Ana Reyes", "old-ana@example.test", "203.0.113.7", "ana-store@example.test",
    "+639171234567", "facebook.com/ana", "instagram.com/ana", "tiktok.com/@ana",
    "Ana's Store", "Ana Trading", "Ana Reyes Trading", "123-456-789-000", "BN7654321",
    "113RC20260000017598", "1 Mabini St, Davao City", "Ana R.", "ana-privacy@example.test",
)


def snapshot():
    return {
        model._meta.label: list(model.objects.order_by("pk").values()) for model in TABLES
    }


def full_account(email, slug, *, now, verified=True, with_site=True):
    """An account with a row on every table a disposal reaches."""
    account = Account.objects.create(
        email=email, name="Ana Reyes", email_verified_at=now if verified else None
    )
    for label in ("phone", "laptop"):
        Session.objects.create(
            account=account, token_hash=f"{slug}-{label}", device_label=label,
            masked_ip="203.0.113.0", last_used_at=now, expires_at=now + timedelta(days=30),
        )
    ExternalIdentity.objects.create(
        account=account, provider="google", provider_user_id=f"g-{slug}"
    )
    PolicyAcceptance.objects.create(
        account=account, policy_version="2026-11", policies=["terms", "privacy"], ip="203.0.113.7"
    )
    AccountAuditEvent.objects.create(
        account=account, event="signed_in", detail={"method": "email_code", "session": "ses_1"}
    )
    AccountAuditEvent.objects.create(
        account=account, event="email_changed", detail={"old": f"old-{email}", "new": email}
    )
    AccountAuditEvent.objects.create(account=account, event="mobile_verified")
    AccountMobile.objects.create(account=account, number=MOBILE, verified_at=now)
    for label in ("first", "second"):
        MobileOtpChallenge.objects.create(
            account=account, number=MOBILE, code_hash=f"{slug}-{label}-code-hash",
            expires_at=now + timedelta(minutes=10), sms_public_id=f"sms_{slug}_{label}",
        )
    for category in ("signin_code", "admin_signin_code"):
        EmailMessage.objects.create(recipient=email, category=category)
    if with_site:
        site = make_site(
            account, name=f"{slug}s-store", display_name="Ana's Store",
            contact_email=f"{slug}-store@example.test", contact_mobile="+639171234567",
            facebook_url=f"https://www.facebook.com/{slug}",
            instagram_url=f"https://www.instagram.com/{slug}",
            tiktok_url=f"https://www.tiktok.com/@{slug}",
            accent_color="#112233",
        )
        SiteBusinessProfile.objects.filter(site=site).update(
            business_name="Ana Trading", registered_name="Ana Reyes Trading",
            tin="123-456-789-000", dti_business_number="BN7654321", bir_rsn="113RC20260000017598",
            business_address="1 Mabini St, Davao City", complaints_contact_name="Ana R.",
            privacy_contact_email=f"{slug}-privacy@example.test",
            shipping_estimates_confirmed=True,
        )
    return account


class DisposalTestCase(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.operator = get_user_model().objects.create_user(
            username="coach", password="x", is_staff=True
        )
        self.account = full_account(EMAIL, "ana", now=self.now)
        self.other = full_account(OTHER_EMAIL, "ben", now=self.now)

    def assert_no_personal_value(self, text):
        for value in ANA_VALUES:
            self.assertNotIn(value, text)

    def rows_of_other(self):
        """The other account's rows on every table, to prove a disposal never reaches them."""
        other = self.other
        return {
            "account": list(Account.objects.filter(pk=other.pk).values()),
            "sessions": list(Session.objects.filter(account=other).values()),
            "identities": list(ExternalIdentity.objects.filter(account=other).values()),
            "mobile": list(AccountMobile.objects.filter(account=other).values()),
            "challenges": list(MobileOtpChallenge.objects.filter(account=other).values()),
            "acceptances": list(PolicyAcceptance.objects.filter(account=other).values()),
            "events": list(AccountAuditEvent.objects.filter(account=other).values()),
            "sites": list(Site.objects.filter(account=other).values()),
            "profiles": list(SiteBusinessProfile.objects.filter(site__account=other).values()),
            "names": list(Subdomain.objects.filter(site__account=other).values()),
            "emails": list(EmailMessage.objects.filter(recipient=other.email).values()),
        }
