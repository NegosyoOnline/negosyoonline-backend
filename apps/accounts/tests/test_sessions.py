import hashlib
from datetime import timedelta

from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts import sessions
from apps.accounts.models import Account, Session

CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class SessionTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(email="ana@example.test")
        self.request = RequestFactory().post(
            "/", HTTP_USER_AGENT=CHROME_MAC, REMOTE_ADDR="203.0.113.9"
        )

    def test_start_session_stores_only_the_hash_of_the_token(self):
        session, token = sessions.start_session(self.account, self.request)

        self.assertEqual(session.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, [str(value) for value in vars(session).values()])
        self.assertEqual(session.device_label, "Chrome on Mac")
        self.assertEqual(session.masked_ip, "203.0.113.x")
        self.assertAlmostEqual(
            session.expires_at, timezone.now() + timedelta(days=30), delta=timedelta(minutes=1)
        )

    def test_find_session_returns_it_for_the_token_only(self):
        session, token = sessions.start_session(self.account, self.request)

        self.assertEqual(sessions.find_session(token), session)
        self.assertIsNone(sessions.find_session("not-the-token"))
        self.assertIsNone(sessions.find_session(None))

    def test_expired_revoked_and_closed_sessions_are_not_found(self):
        expired, expired_token = sessions.start_session(self.account, self.request)
        Session.objects.filter(pk=expired.pk).update(expires_at=timezone.now())
        revoked, revoked_token = sessions.start_session(self.account, self.request)
        sessions.revoke_session(revoked)

        self.assertIsNone(sessions.find_session(expired_token))
        self.assertIsNone(sessions.find_session(revoked_token))

        _live, live_token = sessions.start_session(self.account, self.request)
        Account.objects.filter(pk=self.account.pk).update(closed_at=timezone.now())

        self.assertIsNone(sessions.find_session(live_token))

    def test_revoke_sessions_can_keep_one(self):
        keep, _ = sessions.start_session(self.account, self.request)
        sessions.start_session(self.account, self.request)
        sessions.start_session(self.account, self.request)

        revoked = sessions.revoke_sessions(self.account, keep=keep)

        self.assertEqual(revoked, 2)
        self.assertEqual(
            list(Session.objects.filter(revoked_at__isnull=True)), [keep]
        )

    def test_cookie_is_httponly_secure_lax_and_scoped_to_v1(self):
        response = HttpResponse()

        sessions.set_session_cookie(response, "token-value")

        cookie = response.cookies["no_session"]
        self.assertEqual(cookie.value, "token-value")
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/v1/")
        self.assertEqual(cookie["domain"], "")
        self.assertEqual(cookie["max-age"], 30 * 24 * 3600)

    def test_ip_masking_and_client_ip(self):
        self.assertEqual(sessions.mask_ip("2001:db8:abcd:12::1"), "2001:0db8:abcd:x")
        self.assertEqual(sessions.mask_ip("garbage"), "")
        proxied = RequestFactory().get("/", HTTP_X_REAL_IP="198.51.100.7", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(sessions.client_ip(proxied), "198.51.100.7")
        forged = RequestFactory().get("/", HTTP_X_REAL_IP="not-an-ip", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(sessions.client_ip(forged), "")
