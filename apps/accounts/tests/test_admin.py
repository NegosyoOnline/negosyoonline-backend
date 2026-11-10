from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    ExternalIdentity,
    PolicyAcceptance,
    Session,
)

EMAIL = "ana.reyes@example.test"
MASKED = "a***@example.test"


class AccountsAdminMaskingTests(TestCase):
    """The admin lists show a subscriber's email masked, never in full."""

    def setUp(self):
        operator = get_user_model().objects.create_superuser("op", "op@operators.test", "pw")
        self.client.force_login(operator)
        now = timezone.now()
        self.account = Account.objects.create(email=EMAIL, name="Ana")
        Session.objects.create(
            account=self.account, token_hash="0" * 64, expires_at=now + timedelta(days=1),
            last_used_at=now,
        )
        PolicyAcceptance.objects.create(
            account=self.account, policy_version="2026-11-10", policies=["terms", "privacy"]
        )
        AccountAuditEvent.objects.create(account=self.account, event="signed_in")
        ExternalIdentity.objects.create(
            account=self.account, provider="google", provider_user_id="g-1"
        )

    def test_every_list_shows_the_email_masked(self):
        for model in ("account", "session", "policyacceptance", "accountauditevent",
                      "externalidentity"):
            with self.subTest(model=model):
                url = reverse(f"admin:accounts_{model}_changelist")
                response = self.client.get(url, secure=True)
                self.assertContains(response, MASKED)
                self.assertNotContains(response, EMAIL)

    def test_search_by_full_email_still_finds_the_account_and_stays_masked(self):
        url = reverse("admin:accounts_account_changelist")
        response = self.client.get(url, {"q": EMAIL}, secure=True)
        self.assertContains(response, MASKED)
        self.assertContains(response, self.account.public_id)
        # The search box echoes what the operator typed; the result rows must not repeat it.
        self.assertEqual(response.content.decode().count(EMAIL), 1)

    def test_the_masked_columns_sort_by_the_real_email(self):
        Account.objects.create(email="zed@example.test")
        url = reverse("admin:accounts_account_changelist")
        response = self.client.get(url, {"o": "1"}, secure=True)
        body = response.content.decode()
        self.assertLess(body.index(MASKED), body.index("z***@example.test"))
