from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, ExternalIdentity, Session
from apps.common.ids import is_public_id


class AccountModelTests(TestCase):
    def test_account_gets_a_public_id_and_a_referral_code(self):
        account = Account.objects.create(email="ana@example.test")

        self.assertTrue(is_public_id(account.public_id, "acc"))
        self.assertEqual(len(account.referral_code), 8)
        self.assertIsNone(account.email_verified_at)
        self.assertIsNone(account.closed_at)

    def test_email_is_unique(self):
        Account.objects.create(email="ana@example.test")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(email="ana@example.test")

    def test_session_gets_a_public_id(self):
        account = Account.objects.create(email="ana@example.test")
        now = timezone.now()

        session = Session.objects.create(
            account=account, token_hash="0" * 64, expires_at=now, last_used_at=now
        )

        self.assertTrue(is_public_id(session.public_id, "ses"))


class ExternalIdentityModelTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(email="ana@example.test")
        ExternalIdentity.objects.create(
            account=self.account, provider="google", provider_user_id="g-1"
        )

    def test_a_provider_user_links_to_one_account_only(self):
        other = Account.objects.create(email="ben@example.test")

        with self.assertRaises(IntegrityError), transaction.atomic():
            ExternalIdentity.objects.create(
                account=other, provider="google", provider_user_id="g-1"
            )

    def test_an_account_holds_one_identity_per_provider(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExternalIdentity.objects.create(
                account=self.account, provider="google", provider_user_id="g-2"
            )

        ExternalIdentity.objects.create(
            account=self.account, provider="facebook", provider_user_id="f-1"
        )
        self.assertEqual(self.account.identities.count(), 2)
