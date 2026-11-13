import hashlib
import hmac
import json
import time

from django.test import TestCase, override_settings

from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity
from apps.accounts.tests.test_facebook import SECRET, _b64, signed_request

DELETION = "/v1/webhooks/facebook/data-deletion/"


@override_settings(FACEBOOK_APP_ID="app-id", FACEBOOK_APP_SECRET=SECRET)
class FacebookDataDeletionTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(email="ana@example.test")
        ExternalIdentity.objects.create(
            account=self.account, provider="facebook", provider_user_id="fb-1"
        )

    def send(self, value):
        return self.client.post(DELETION, {"signed_request": value}, secure=True)

    def test_a_signed_request_unlinks_facebook_and_answers_as_meta_expects(self):
        response = self.send(signed_request())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"url", "confirmation_code"})
        self.assertEqual(
            body["url"], f"https://testserver{DELETION}{body['confirmation_code']}/"
        )
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertTrue(Account.objects.filter(pk=self.account.pk).exists())
        self.assertTrue(AccountAuditEvent.objects.filter(event="identity_removed").exists())

        status = self.client.get(f"{DELETION}{body['confirmation_code']}/", secure=True)

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json(), {"status": "completed"})

    def test_an_unknown_facebook_user_gets_the_same_answer(self):
        response = self.send(signed_request(user_id="fb-unknown"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"url", "confirmation_code"})
        self.assertTrue(ExternalIdentity.objects.exists())

    def test_an_unsigned_or_oversized_request_changes_nothing(self):
        forged = self.send(signed_request(secret="someone-elses-secret"))
        missing = self.client.post(DELETION, {}, secure=True)
        huge = self.send(signed_request() + "A" * 20_000)

        self.assertEqual((forged.status_code, forged.json()["error"]["code"]), (400, "invalid_signature"))
        self.assertEqual(missing.status_code, 400)
        self.assertEqual((huge.status_code, huge.json()["error"]["code"]), (413, "payload_too_large"))
        self.assertTrue(ExternalIdentity.objects.exists())

    def test_a_made_up_confirmation_code_is_404(self):
        response = self.client.get(f"{DELETION}made-up-code/", secure=True)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    @override_settings(FACEBOOK_APP_SECRET="")
    def test_without_facebook_variables_the_callback_is_404(self):
        self.assertEqual(self.send(signed_request()).status_code, 404)

    def test_a_request_older_than_24_hours_or_ahead_of_the_clock_is_refused(self):
        for issued_at in (int(time.time()) - 24 * 3600 - 5, int(time.time()) + 6 * 60):
            with self.subTest(issued_at=issued_at):
                response = self.send(signed_request(issued_at=issued_at))

                self.assertEqual(
                    (response.status_code, response.json()["error"]["code"]), (400, "stale_request")
                )
                self.assertTrue(ExternalIdentity.objects.exists())
                self.assertFalse(AccountAuditEvent.objects.filter(event="identity_removed").exists())

    def test_a_request_just_inside_both_bounds_is_accepted(self):
        now = int(time.time())
        for issued_at in (now - 24 * 3600 + 60, now + 4 * 60):
            with self.subTest(issued_at=issued_at):
                ExternalIdentity.objects.get_or_create(
                    account=self.account, provider="facebook", provider_user_id="fb-1"
                )
                response = self.send(signed_request(issued_at=issued_at))

                self.assertEqual(response.status_code, 200)
                self.assertFalse(ExternalIdentity.objects.exists())

    def test_a_missing_string_or_bool_issued_at_is_refused_and_changes_nothing(self):
        data = {"algorithm": "HMAC-SHA256", "user_id": "fb-1"}
        payload = _b64(json.dumps(data).encode())
        signature = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
        missing = f"{_b64(signature)}.{payload}"
        values = [missing, signed_request(issued_at="1800000000"), signed_request(issued_at=True)]
        for value in values:
            with self.subTest(value=value[-12:]):
                response = self.send(value)

                self.assertEqual(
                    (response.status_code, response.json()["error"]["code"]),
                    (400, "invalid_signature"),
                )
                self.assertTrue(ExternalIdentity.objects.exists())
