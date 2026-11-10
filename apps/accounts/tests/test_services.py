import re

from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Account, AccountAuditEvent, PolicyAcceptance, Session
from apps.accounts.sessions import find_session, start_session
from apps.common.errors import ApiError

EMAIL = "ana@example.test"
IP = "203.0.113.9"


def last_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class AccountServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.request = RequestFactory().post("/", REMOTE_ADDR=IP)

    def sign_in(self, email=EMAIL, accept_policies=True):
        cache.clear()
        services.request_signin_code(email, IP)
        return services.verify_signin_code(email, last_code(), accept_policies, self.request)

    def test_request_sends_a_code_and_creates_no_account(self):
        services.request_signin_code("  Ana@Example.TEST ", IP)

        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        self.assertRegex(last_code(), r"^\d{6}$")
        self.assertFalse(Account.objects.exists())

    def test_invalid_email_names_the_field(self):
        with self.assertRaises(ApiError) as raised:
            services.request_signin_code("not-an-email", IP)

        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertIn("email", raised.exception.fields)

    def test_first_verify_creates_the_account_and_records_policy_acceptance(self):
        account, session, token, created = self.sign_in()

        self.assertTrue(created)
        self.assertEqual(account.email, EMAIL)
        self.assertIsNotNone(account.email_verified_at)
        self.assertEqual(find_session(token), session)
        acceptance = PolicyAcceptance.objects.get(account=account)
        self.assertEqual(acceptance.policy_version, "2026-11-10")
        self.assertEqual(acceptance.policies, ["terms", "privacy"])
        self.assertEqual(acceptance.ip, IP)
        self.assertEqual(
            list(AccountAuditEvent.objects.values_list("event", flat=True).order_by("id")),
            ["account_created", "signed_in"],
        )

    def test_later_verify_signs_in_and_revokes_earlier_sessions(self):
        _account, _first, first_token, _ = self.sign_in()

        account, _second, second_token, created = self.sign_in()

        self.assertFalse(created)
        self.assertEqual(Account.objects.count(), 1)
        self.assertIsNone(find_session(first_token))
        self.assertEqual(find_session(second_token).account, account)

    def test_new_account_must_accept_policies_and_the_code_survives(self):
        services.request_signin_code(EMAIL, IP)
        code = last_code()

        with self.assertRaises(ApiError) as raised:
            services.verify_signin_code(EMAIL, code, False, self.request)

        self.assertEqual(raised.exception.code, "policies_required")
        self.assertFalse(Account.objects.exists())
        _account, _session, _token, created = services.verify_signin_code(
            EMAIL, code, True, self.request
        )
        self.assertTrue(created)

    def test_wrong_code_is_refused_and_a_used_code_cannot_be_reused(self):
        services.request_signin_code(EMAIL, IP)
        code = last_code()
        wrong = "000000" if code != "000000" else "111111"

        with self.assertRaises(ApiError) as raised:
            services.verify_signin_code(EMAIL, wrong, True, self.request)
        self.assertEqual(raised.exception.code, "invalid_code")

        services.verify_signin_code(EMAIL, code, True, self.request)
        with self.assertRaises(ApiError):
            services.verify_signin_code(EMAIL, code, True, self.request)

    def test_closed_account_cannot_sign_in(self):
        Account.objects.create(email=EMAIL, closed_at=timezone.now())

        with self.assertRaises(ApiError) as raised:
            self.sign_in()

        self.assertEqual(raised.exception.code, "account_closed")
        self.assertEqual(raised.exception.status, 403)

    def test_email_change_needs_the_code_sent_to_the_new_address(self):
        account, current, current_token, _ = self.sign_in()
        _other, other_token = start_session(account, self.request)
        cache.clear()

        services.start_email_change(account, "New@Example.test", IP)

        self.assertEqual(mail.outbox[-1].to, ["new@example.test"])
        services.confirm_email_change(account, "new@example.test", last_code(), current)
        account.refresh_from_db()
        self.assertEqual(account.email, "new@example.test")
        self.assertIsNotNone(find_session(current_token))
        self.assertIsNone(find_session(other_token))
        event = AccountAuditEvent.objects.get(event="email_changed")
        self.assertEqual(event.detail, {"old": EMAIL, "new": "new@example.test"})

    def test_email_change_to_a_taken_or_unchanged_email_is_refused(self):
        account, _session, _token, _ = self.sign_in()
        Account.objects.create(email="taken@example.test")
        cache.clear()

        with self.assertRaises(ApiError) as taken:
            services.start_email_change(account, "taken@example.test", IP)
        with self.assertRaises(ApiError) as unchanged:
            services.start_email_change(account, EMAIL, IP)

        self.assertEqual((taken.exception.code, taken.exception.status), ("email_taken", 409))
        self.assertEqual(unchanged.exception.code, "email_unchanged")

    def test_update_account_trims_the_name_and_records_it(self):
        account, _session, _token, _ = self.sign_in()

        services.update_account(account, name="  Ana Reyes ")

        account.refresh_from_db()
        self.assertEqual(account.name, "Ana Reyes")
        self.assertTrue(AccountAuditEvent.objects.filter(event="profile_updated").exists())
        self.assertEqual(Session.objects.filter(account=account).count(), 1)
