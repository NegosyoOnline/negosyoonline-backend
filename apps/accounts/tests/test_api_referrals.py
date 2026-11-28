from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL, ApiTestCase

NEW_EMAIL = "ben@example.test"
MISS = {"valid": False, "referrer_first_name": ""}


def verify(case, email, referral_code=None, client=None):
    cache.clear()
    case.post("/v1/auth/email-code/request/", {"email": email}, client=client)
    body = {"email": email, "code": case.last_code(), "accept_policies": True}
    if referral_code is not None:
        body["referral_code"] = referral_code
    return case.post("/v1/auth/email-code/verify/", body, client=client)


@override_settings(BILLING_REFERRALS_ENABLED=True)
class ReferralLookupTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.referrer = Account.objects.create(email=EMAIL, name="Ana Cruz")

    def lookup(self, code):
        return self.get(f"/v1/referrals/{code}/")

    def test_a_live_code_answers_valid_with_the_first_name(self):
        response = self.lookup(self.referrer.referral_code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"valid": True, "referrer_first_name": "Ana"})

    def test_the_code_is_case_insensitive(self):
        self.assertTrue(self.lookup(self.referrer.referral_code.upper()).json()["valid"])

    def test_every_miss_has_the_same_status_and_body(self):
        closed = Account.objects.create(email="old@example.test", closed_at=timezone.now())
        misses = [
            self.lookup("ZZZZZZZZ"),
            self.lookup(closed.referral_code),
            self.lookup("x" * 200),
            self.lookup("%20"),
            self.lookup("a-b_c"),
        ]
        for response in misses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), MISS)

    def test_it_needs_no_session(self):
        self.assertEqual(self.lookup(self.referrer.referral_code).status_code, 200)

    def test_a_blank_name_gives_an_empty_first_name(self):
        other = Account.objects.create(email="b@example.test", name="")

        self.assertEqual(
            self.lookup(other.referral_code).json(), {"valid": True, "referrer_first_name": ""}
        )

    def test_it_is_rate_limited_per_address_for_hits_and_misses(self):
        for _ in range(30):
            self.assertEqual(self.lookup("ZZZZZZZZ").status_code, 200)

        for code in ("ZZZZZZZZ", self.referrer.referral_code):
            response = self.lookup(code)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json()["error"]["code"], "rate_limited")

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_switch_off_every_code_is_invalid(self):
        response = self.lookup(self.referrer.referral_code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), MISS)


@override_settings(BILLING_REFERRALS_ENABLED=True)
class SignupReferrerTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.referrer = Account.objects.create(email=EMAIL, name="Ana")

    def test_a_valid_code_sets_referred_by_at_creation(self):
        response = verify(self, NEW_EMAIL, self.referrer.referral_code)

        self.assertTrue(response.json()["created"])
        self.assertEqual(Account.objects.get(email=NEW_EMAIL).referred_by, self.referrer)

    def test_no_code_or_a_blank_code_leaves_it_empty(self):
        verify(self, NEW_EMAIL)
        verify(self, "c@example.test", "")

        self.assertFalse(Account.objects.filter(referred_by__isnull=False).exists())

    def test_an_unknown_or_closed_code_is_ignored_and_signup_succeeds(self):
        closed = Account.objects.create(email="old@example.test", closed_at=timezone.now())
        for index, code in enumerate(["ZZZZZZZZ", closed.referral_code, "x" * 40]):
            email = f"n{index}@example.test"
            response = verify(self, email, code)

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["created"])
            self.assertIsNone(Account.objects.get(email=email).referred_by)

    def test_a_code_never_changes_an_existing_account(self):
        other = Account.objects.create(email="third@example.test")
        verify(self, NEW_EMAIL, self.referrer.referral_code)
        again = verify(self, NEW_EMAIL, other.referral_code, client=Client())

        self.assertFalse(again.json()["created"])
        self.assertEqual(Account.objects.get(email=NEW_EMAIL).referred_by, self.referrer)

    def test_an_existing_account_signing_in_with_a_code_stays_unreferred(self):
        other = Account.objects.create(email="third@example.test")
        response = verify(self, EMAIL, other.referral_code)

        self.assertFalse(response.json()["created"])
        self.assertIsNone(Account.objects.get(email=EMAIL).referred_by)

    def test_self_referral_is_refused(self):
        # A code that resolves to the very email being created is dropped (defence in depth: a
        # new account's own code cannot be known beforehand), so test the guard at the service.
        same = Account(email="same@example.test", referral_code="SAMECODE")
        request = RequestFactory().post("/")
        with mock.patch.object(services, "find_referrer", return_value=same):
            account = services._create_account(
                "same@example.test", request, method="email_code", referral_code="SAMECODE"
            )

        self.assertIsNone(account.referred_by)

    def test_one_level_only_each_account_keeps_its_own_direct_referrer(self):
        verify(self, NEW_EMAIL, self.referrer.referral_code)
        middle = Account.objects.get(email=NEW_EMAIL)
        verify(self, "leaf@example.test", middle.referral_code)

        leaf = Account.objects.get(email="leaf@example.test")
        self.assertEqual(leaf.referred_by, middle)
        self.assertEqual(middle.referred_by, self.referrer)

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_switch_off_signup_ignores_the_code(self):
        response = verify(self, NEW_EMAIL, self.referrer.referral_code)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["created"])
        self.assertIsNone(Account.objects.get(email=NEW_EMAIL).referred_by)
        self.assertEqual(len(mail.outbox), 1)
