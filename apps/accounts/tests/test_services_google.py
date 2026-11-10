from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts import services
from apps.accounts.google import GoogleProfile
from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity, PolicyAcceptance
from apps.accounts.sessions import find_session, start_session
from apps.common.errors import ApiError

EMAIL = "ana@example.test"
IP = "203.0.113.9"
PROFILE = GoogleProfile(
    user_id="g-123", email="Ana@Example.test", email_verified=True, name="Ana Reyes"
)


class GoogleSignInServiceTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/", REMOTE_ADDR=IP)

    def sign_in(self, profile=PROFILE, accept_policies=True):
        return services.sign_in_with_google(profile, accept_policies, self.request)

    def events(self):
        return list(AccountAuditEvent.objects.values_list("event", flat=True).order_by("id"))

    def test_first_sign_in_creates_the_account_and_links_google(self):
        account, session, token, created = self.sign_in()

        self.assertTrue(created)
        self.assertEqual((account.email, account.name), (EMAIL, "Ana Reyes"))
        self.assertIsNotNone(account.email_verified_at)
        self.assertEqual(find_session(token), session)
        identity = ExternalIdentity.objects.get(account=account)
        self.assertEqual((identity.provider, identity.provider_user_id), ("google", "g-123"))
        acceptance = PolicyAcceptance.objects.get(account=account)
        self.assertEqual((acceptance.policy_version, acceptance.ip), ("2026-11-10", IP))
        self.assertEqual(self.events(), ["account_created", "identity_linked", "signed_in"])

    def test_new_account_must_accept_policies(self):
        with self.assertRaises(ApiError) as raised:
            self.sign_in(accept_policies=False)

        self.assertEqual(raised.exception.code, "policies_required")
        self.assertFalse(Account.objects.exists())
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_known_google_user_signs_in_whatever_email_google_now_reports(self):
        first, _session, first_token, _ = self.sign_in()
        moved = GoogleProfile(
            user_id="g-123", email="other@example.test", email_verified=False, name="A"
        )

        account, _session, token, created = self.sign_in(moved, accept_policies=False)

        self.assertFalse(created)
        self.assertEqual(account, first)
        self.assertEqual(account.email, EMAIL)
        self.assertEqual(Account.objects.count(), 1)
        self.assertIsNotNone(find_session(token))
        self.assertIsNotNone(find_session(first_token))

    def test_existing_account_with_that_email_is_linked_and_keeps_its_sessions(self):
        existing = Account.objects.create(email=EMAIL, name="Ana")
        _earlier, earlier_token = start_session(existing, self.request)

        account, _session, _token, created = self.sign_in(accept_policies=False)

        self.assertFalse(created)
        self.assertEqual(account, existing)
        self.assertEqual(account.name, "Ana")
        self.assertIsNotNone(account.email_verified_at)
        self.assertTrue(existing.identities.filter(provider="google").exists())
        self.assertIsNotNone(find_session(earlier_token))
        self.assertEqual(self.events(), ["identity_linked", "signed_in"])

    def test_an_email_google_has_not_verified_is_refused(self):
        unverified = GoogleProfile(
            user_id="g-123", email=EMAIL, email_verified=False, name="Ana"
        )

        with self.assertRaises(ApiError) as raised:
            self.sign_in(unverified)

        self.assertEqual(raised.exception.code, "google_email_unverified")
        self.assertFalse(Account.objects.exists())

    def test_an_account_linked_to_another_google_user_is_refused(self):
        self.sign_in()
        impostor = GoogleProfile(user_id="g-999", email=EMAIL, email_verified=True, name="X")

        with self.assertRaises(ApiError) as raised:
            self.sign_in(impostor)

        self.assertEqual(
            (raised.exception.code, raised.exception.status), ("google_account_mismatch", 409)
        )
        self.assertEqual(ExternalIdentity.objects.count(), 1)

    def test_closed_account_cannot_sign_in_by_identity_or_by_email(self):
        account, _session, _token, _ = self.sign_in()
        Account.objects.filter(pk=account.pk).update(closed_at=timezone.now())

        with self.assertRaises(ApiError) as by_identity:
            self.sign_in()

        ExternalIdentity.objects.all().delete()
        with self.assertRaises(ApiError) as by_email:
            self.sign_in()

        self.assertEqual((by_identity.exception.code, by_identity.exception.status), ("account_closed", 403))
        self.assertEqual(by_email.exception.code, "account_closed")
        self.assertFalse(ExternalIdentity.objects.exists())
