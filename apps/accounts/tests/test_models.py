from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, Session
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
