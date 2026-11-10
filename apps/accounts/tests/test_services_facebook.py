import re

from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts import services
from apps.accounts.facebook import FacebookProfile
from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity, PolicyAcceptance
from apps.accounts.sessions import find_session, start_session
from apps.common.errors import ApiError

EMAIL = "ana@example.test"
IP = "203.0.113.9"
PROFILE = FacebookProfile(user_id="fb-1", name="Ana Reyes", email=EMAIL)


def last_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class FacebookServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.request = RequestFactory().post("/", REMOTE_ADDR=IP)

    def confirm(self, user_id="fb-1", email=EMAIL, accept_policies=True, code=None):
        if code is None:
            services.send_facebook_email_code(user_id, email, IP)
            code = last_code()
        return services.confirm_facebook_email(
            user_id, "Ana Reyes", email, code, accept_policies, self.request
        )

    def events(self):
        return list(AccountAuditEvent.objects.values_list("event", flat=True).order_by("id"))

    def test_a_facebook_user_not_seen_before_is_not_signed_in(self):
        self.assertIsNone(services.sign_in_with_facebook(PROFILE, self.request))
        self.assertFalse(Account.objects.exists())

    def test_the_code_goes_to_the_normalised_email(self):
        sent_to = services.send_facebook_email_code("fb-1", "  Ana@Example.TEST ", IP)

        self.assertEqual(sent_to, EMAIL)
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        self.assertRegex(last_code(), r"^\d{6}$")
        with self.assertRaises(ApiError) as raised:
            services.send_facebook_email_code("fb-1", "not-an-email", IP)
        self.assertIn("email", raised.exception.fields)

    def test_confirming_creates_the_account_and_later_sign_ins_need_no_code(self):
        account, _session, first_token, created = self.confirm()

        self.assertTrue(created)
        self.assertEqual((account.email, account.name), (EMAIL, "Ana Reyes"))
        self.assertIsNotNone(account.email_verified_at)
        identity = ExternalIdentity.objects.get(account=account)
        self.assertEqual((identity.provider, identity.provider_user_id), ("facebook", "fb-1"))
        self.assertEqual(PolicyAcceptance.objects.get(account=account).ip, IP)
        self.assertEqual(self.events(), ["account_created", "identity_linked", "signed_in"])

        again, _session, token = services.sign_in_with_facebook(PROFILE, self.request)

        self.assertEqual(again, account)
        self.assertIsNotNone(find_session(token))
        self.assertIsNotNone(find_session(first_token))

    def test_new_account_must_accept_policies_and_the_code_survives(self):
        services.send_facebook_email_code("fb-1", EMAIL, IP)
        code = last_code()

        with self.assertRaises(ApiError) as raised:
            self.confirm(accept_policies=False, code=code)

        self.assertEqual(raised.exception.code, "policies_required")
        self.assertFalse(Account.objects.exists())
        _account, _session, _token, created = self.confirm(code=code)
        self.assertTrue(created)

    def test_existing_account_is_linked_and_its_earlier_sessions_end(self):
        existing = Account.objects.create(email=EMAIL, name="Ana")
        _earlier, earlier_token = start_session(existing, self.request)

        account, _session, token, created = self.confirm(accept_policies=False)

        self.assertFalse(created)
        self.assertEqual(account, existing)
        self.assertEqual(account.name, "Ana")
        self.assertIsNotNone(account.email_verified_at)
        self.assertTrue(existing.identities.filter(provider="facebook").exists())
        self.assertIsNone(find_session(earlier_token))
        self.assertIsNotNone(find_session(token))
        self.assertEqual(self.events(), ["identity_linked", "signed_in"])

    def test_a_wrong_code_or_a_plain_sign_in_code_is_refused(self):
        services.send_facebook_email_code("fb-1", EMAIL, IP)
        wrong = "000000" if last_code() != "000000" else "111111"
        services.request_signin_code(EMAIL, IP)
        signin_code = last_code()

        for code in (wrong, signin_code):
            with self.assertRaises(ApiError) as raised:
                self.confirm(code=code)
            self.assertEqual(raised.exception.code, "invalid_code")
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_an_account_linked_to_another_facebook_user_is_refused(self):
        self.confirm()

        with self.assertRaises(ApiError) as raised:
            self.confirm(user_id="fb-2")

        self.assertEqual(
            (raised.exception.code, raised.exception.status), ("facebook_account_mismatch", 409)
        )
        self.assertEqual(ExternalIdentity.objects.count(), 1)

    def test_closed_account_cannot_sign_in_or_be_linked(self):
        account, _session, _token, _ = self.confirm()
        Account.objects.filter(pk=account.pk).update(closed_at=timezone.now())

        with self.assertRaises(ApiError) as known:
            services.sign_in_with_facebook(PROFILE, self.request)
        ExternalIdentity.objects.all().delete()
        cache.clear()  # the first confirm started the one-minute wait for a new code
        with self.assertRaises(ApiError) as linking:
            self.confirm()

        self.assertEqual((known.exception.code, known.exception.status), ("account_closed", 403))
        self.assertEqual(linking.exception.code, "account_closed")
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_removing_the_identity_keeps_the_account_and_records_it(self):
        account, _session, token, _ = self.confirm()

        self.assertTrue(services.remove_facebook_identity("fb-1"))
        self.assertFalse(services.remove_facebook_identity("fb-1"))

        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())
        self.assertIsNotNone(find_session(token))
        event = AccountAuditEvent.objects.get(event="identity_removed")
        self.assertEqual(event.detail, {"provider": "facebook", "reason": "data_deletion_request"})
        self.assertIsNone(services.sign_in_with_facebook(PROFILE, self.request))
