"""The four member routes: request a code, verify it, me, sign out."""
import logging
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.accounts import codes
from apps.members import services, signin
from apps.members.models import Member, MemberSession
from apps.members.tests.base import (
    BASE, EMAIL, HOST, INVALID_CODE, MOBILE, NOT_FOUND, NOT_SIGNED_IN, OTHER_HOST, SECRET,
    VISITOR, MembersTestCase,
)
from apps.sites.models import Site
from apps.storefront.tests.base import UNAUTHENTICATED


class RequestTests(MembersTestCase):
    def test_a_member_is_emailed_a_six_digit_code_and_nothing_else_is_answered(self):
        response = self.request_code()

        self.assertEqual((response.status_code, response.json()), (200, {"status": "sent"}))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertRegex(mail.outbox[0].body, r"code is \d{6}\. It expires in 10 minutes")
        self.assertNotIn(self.last_code(), response.content.decode())

    def test_the_address_is_matched_whatever_its_case_or_spacing(self):
        self.assertEqual(self.request_code("  Maria@Buyer.TEST ").status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_an_unknown_address_gets_the_same_answer_and_no_email(self):
        known, unknown = self.request_code(), self.request_code("nobody@buyer.test")
        self.assertEqual((known.status_code, known.json()), (unknown.status_code, unknown.json()))
        self.assertEqual(len(mail.outbox), 1)

    def test_a_member_of_another_site_gets_the_same_answer_and_no_email(self):
        self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550111")
        response = self.request_code("ben@buyer.test")
        self.assertEqual((response.status_code, response.json()), (200, {"status": "sent"}))
        self.assertEqual(mail.outbox, [])

    def test_a_malformed_address_is_a_validation_error(self):
        response = self.request_code("not-an-email")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.assertEqual(mail.outbox, [])

    def test_every_answer_takes_at_least_the_floor(self):
        naps = []
        with mock.patch.object(signin, "_sleep", naps.append), mock.patch.object(
            signin, "_clock", side_effect=[100.0, 100.2]
        ):
            self.request_code()
        self.assertEqual(len(naps), 1)
        self.assertAlmostEqual(naps[0], signin.MIN_SECONDS - 0.2)

    def test_the_floor_is_the_same_for_a_member_and_a_stranger(self):
        for email in (EMAIL, "nobody@buyer.test"):
            naps = []
            with mock.patch.object(signin, "_sleep", naps.append), mock.patch.object(
                signin, "_clock", side_effect=[0.0, 0.1]
            ):
                self.request_code(email)
            self.assertEqual(len(naps), 1, email)

    def test_a_second_request_inside_the_minute_is_refused_for_everyone(self):
        for email in (EMAIL, "nobody@buyer.test"):
            with self.subTest(email=email):
                self.assertEqual(self.request_code(email).status_code, 200)
                response = self.request_code(email)
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.json()["error"]["code"], "rate_limited")

    def test_the_sixth_request_in_an_hour_is_refused_for_everyone(self):
        for email in (EMAIL, "nobody@buyer.test"):
            with self.subTest(email=email):
                cache.clear()
                for _ in range(codes.PER_EMAIL_PER_HOUR):
                    cache.delete(codes._key("cooldown", signin.purpose(self.site), email))
                    self.assertEqual(self.request_code(email).status_code, 200)
                cache.delete(codes._key("cooldown", signin.purpose(self.site), email))
                self.assertEqual(self.request_code(email).status_code, 429)

    def test_one_visitor_address_is_limited_across_addresses(self):
        for number in range(signin.IP_SEND_LIMIT[0]):
            self.assertEqual(self.request_code(f"p{number}@buyer.test").status_code, 200)
        self.assertEqual(self.request_code("last@buyer.test").status_code, 429)

    def test_the_site_flood_monitor_warns_once_and_never_refuses(self):
        key = f"members:signin-volume:{self.site.pk}:{timezone.now().strftime('%Y%m%d%H')}"
        cache.set(key, signin.SITE_MONITOR - 1, 3600)
        with self.assertLogs(signin.logger, logging.WARNING) as logged:
            for number in range(3):
                self.assertEqual(self.request_code(f"f{number}@buyer.test").status_code, 200)
        warnings = [line for line in logged.output if "More than" in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn(self.site.public_id, warnings[0])
        self.assertNotIn("buyer.test", warnings[0])

    def test_a_cache_failure_in_the_monitor_never_blocks_a_request(self):
        with mock.patch.object(signin, "cache") as broken:
            broken.add.side_effect = RuntimeError("down")
            with self.assertLogs(signin.logger, logging.ERROR):
                self.assertEqual(self.request_code().status_code, 200)

    def test_an_email_that_cannot_be_sent_is_answered_like_one_that_was(self):
        from apps.notifications.services import EmailNotSent

        with mock.patch.object(signin, "send_email", side_effect=EmailNotSent()):
            with self.assertLogs(signin.logger, logging.WARNING) as logged:
                response = self.request_code()
        self.assertEqual((response.status_code, response.json()), (200, {"status": "sent"}))
        self.assertNotIn(EMAIL, "".join(logged.output))

    def test_the_email_carries_the_store_name_and_never_a_link(self):
        self.request_code()
        message = mail.outbox[0]
        self.assertIn("Ana's Store", message.subject)
        self.assertNotIn("http", message.body)

    def test_an_email_nobody_has_verified_is_a_channel_only_while_pending_or_verified(self):
        Member.objects.filter(pk=self.member.pk).update(email_state="verified")
        self.request_code()
        self.assertEqual(len(mail.outbox), 1)


class VerifyTests(MembersTestCase):
    def verify(self, code, email=EMAIL, host=HOST, **extra):
        return self.post("signin/verify/", host=host, email=email, code=code, **extra)

    def test_the_right_code_answers_a_token_once_and_the_member(self):
        self.request_code()
        response = self.verify(self.last_code())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"token", "member"})
        self.assertGreaterEqual(len(body["token"]), 40)
        self.assertEqual(body["member"], {
            "id": self.member.public_id, "full_name": "Maria Santos", "email": EMAIL,
            "mobile": "+639171234567", "email_verified": True, "mobile_verified": False,
        })
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_django_sets_no_cookie_on_any_member_route(self):
        self.request_code()
        verified = self.verify(self.last_code())
        token = verified.json()["token"]
        for response in (
            verified, self.request_code("nobody@buyer.test"),
            self.post("me/", token=token), self.post("signout/", token=token),
        ):
            self.assertEqual(len(response.cookies), 0)

    def test_the_first_code_marks_the_email_verified_and_the_mobile_stays_unverified(self):
        self.request_code()
        self.verify(self.last_code())
        self.member.refresh_from_db()
        self.assertEqual((self.member.email_state, self.member.mobile_state), ("verified", "pending"))
        self.assertIsNotNone(self.member.email_verified_at)
        self.assertIsNone(self.member.mobile_verified_at)

    def test_only_the_hash_of_the_token_is_stored_and_the_session_runs_thirty_days(self):
        self.request_code()
        verified = self.verify(self.last_code(), ip="203.0.113.77")
        session = MemberSession.objects.get()
        self.assertNotIn(verified.json()["token"], session.token_hash)
        self.assertEqual((session.site, session.member), (self.site, self.member))
        self.assertEqual(session.expires_at - session.created_at, timedelta(days=30))
        self.assertEqual(session.ip_advisory, "203.0.113.77")

    def test_a_code_works_once(self):
        self.request_code()
        code = self.last_code()
        self.assertEqual(self.verify(code).status_code, 200)
        self.assertEqual((self.verify(code).status_code, self.verify(code).json()), (400, INVALID_CODE))
        self.assertEqual(MemberSession.objects.count(), 1)

    def test_a_wrong_code_is_refused_and_makes_no_session(self):
        self.request_code()
        wrong = "000000" if self.last_code() != "000000" else "111111"
        response = self.verify(wrong)
        self.assertEqual((response.status_code, response.json()), (400, INVALID_CODE))
        self.assertEqual(MemberSession.objects.count(), 0)
        self.member.refresh_from_db()
        self.assertEqual(self.member.email_state, "pending")

    def test_the_sixth_try_fails_even_with_the_right_code(self):
        self.request_code()
        right = self.last_code()
        wrong = "000000" if right != "000000" else "111111"
        for _ in range(codes.MAX_ATTEMPTS):
            self.assertEqual(self.verify(wrong).status_code, 400)
        self.assertEqual(self.verify(right).json(), INVALID_CODE)
        self.assertEqual(MemberSession.objects.count(), 0)

    def test_an_expired_code_is_refused(self):
        self.request_code()
        code = self.last_code()
        cache.delete(codes._key("code", signin.purpose(self.site), EMAIL))
        self.assertEqual(self.verify(code).json(), INVALID_CODE)

    def test_a_new_code_replaces_the_old_one(self):
        self.request_code()
        old = self.last_code()
        cache.delete(codes._key("cooldown", signin.purpose(self.site), EMAIL))
        self.request_code()
        new = self.last_code()
        if old != new:
            self.assertEqual(self.verify(old).status_code, 400)
        self.assertEqual(self.verify(new).status_code, 200)

    def test_an_unknown_address_answers_exactly_like_a_wrong_code(self):
        self.request_code("nobody@buyer.test")
        for code in ("123456", "000000"):
            response = self.verify(code, email="nobody@buyer.test")
            self.assertEqual((response.status_code, response.json()), (400, INVALID_CODE))

    def test_a_code_for_a_stranger_cannot_sign_in_a_member(self):
        self.request_code("nobody@buyer.test")
        self.assertEqual(self.verify("123456").json(), INVALID_CODE)

    def test_a_code_issued_for_one_site_is_not_accepted_on_another(self):
        # One address cannot be a live member of two sites, so check the purpose itself: the
        # code issued for the first site is unknown to the second.
        self.request_code()
        code = self.last_code()
        self.assertFalse(codes.check_code(EMAIL, code, signin.purpose(self.other_site)))
        self.assertEqual(self.verify(code, host=OTHER_HOST).json(), INVALID_CODE)

    def test_a_contact_change_after_the_code_was_sent_voids_it(self):
        self.request_code()
        code = self.last_code()
        services.change_contacts(self.member, email="changed@buyer.test")
        self.assertEqual(self.verify(code).json(), INVALID_CODE)

    def test_one_visitor_address_is_limited_on_verify(self):
        self.request_code()
        for _ in range(signin.IP_VERIFY_LIMIT[0]):
            self.verify("000000", email="nobody@buyer.test")
        self.assertEqual(self.verify("000000", email="nobody@buyer.test").status_code, 429)

    def test_a_code_is_matched_after_trimming(self):
        self.request_code()
        self.assertEqual(self.verify(f" {self.last_code()} ").status_code, 200)


class SessionTests(MembersTestCase):
    def test_me_answers_the_member_of_the_token(self):
        token = self.sign_in()
        response = self.post("me/", token=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["member"]["id"], self.member.public_id)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_me_without_a_token_or_with_a_made_up_one_is_refused(self):
        for token in (None, "", "x", "A" * 43, "y" * 500):
            with self.subTest(token=token):
                fields = {} if token is None else {"token": token}
                response = self.post("me/", **fields)
                self.assertEqual((response.status_code, response.json()), (401, NOT_SIGNED_IN))

    def test_signing_out_ends_the_session(self):
        token = self.sign_in()
        out = self.post("signout/", token=token)
        self.assertEqual((out.status_code, out.json()), (200, {"status": "signed_out"}))
        self.assertEqual(self.post("me/", token=token).json(), NOT_SIGNED_IN)
        session = MemberSession.objects.get()
        self.assertEqual(session.revoke_reason, "sign_out")
        self.assertIsNotNone(session.revoked_at)

    def test_signing_out_twice_or_with_nothing_is_not_an_error(self):
        token = self.sign_in()
        self.post("signout/", token=token)
        self.assertEqual(self.post("signout/", token=token).status_code, 200)
        self.assertEqual(self.post("signout/", token="unknown").status_code, 200)
        self.assertEqual(self.post("signout/").status_code, 200)

    def test_signing_out_ends_only_that_session(self):
        first = self.sign_in()
        cache.clear()
        second = self.sign_in()
        self.post("signout/", token=first)
        self.assertEqual(self.post("me/", token=second).status_code, 200)

    def test_the_session_ends_thirty_days_after_sign_in_and_use_does_not_extend_it(self):
        token = self.sign_in()
        session = MemberSession.objects.get()
        self.assertEqual(self.post("me/", token=token).status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expires_at - session.created_at, timedelta(days=30))
        MemberSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.post("me/", token=token).json(), NOT_SIGNED_IN)

    def test_a_contact_change_signs_the_member_out_everywhere(self):
        token = self.sign_in()
        services.change_contacts(self.member, mobile="09175550113")
        self.assertEqual(self.post("me/", token=token).json(), NOT_SIGNED_IN)

    def test_me_reads_the_member_row_on_every_call(self):
        token = self.sign_in()
        Member.objects.filter(pk=self.member.pk).update(full_name="Maria S. Reyes")
        self.assertEqual(self.post("me/", token=token).json()["member"]["full_name"], "Maria S. Reyes")

    def test_a_token_of_one_site_is_nothing_on_another(self):
        token = self.sign_in()
        self.assertEqual(self.post("me/", host=OTHER_HOST, token=token).json(), NOT_SIGNED_IN)
        self.post("signout/", host=OTHER_HOST, token=token)
        self.assertEqual(self.post("me/", token=token).status_code, 200)

    def test_a_session_row_pointing_at_another_sites_member_is_refused(self):
        stranger = self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550114")
        from apps.members import sessions

        _, raw = sessions.create_session(self.site, stranger)  # a corrupt row: site and member differ
        self.assertEqual(self.post("me/", token=raw).json(), NOT_SIGNED_IN)

    def test_nothing_of_another_member_is_answered(self):
        self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550115", name="Ben Cruz")
        text = self.post("me/", token=self.sign_in()).content.decode()
        self.assertNotIn("Ben Cruz", text)
        self.assertNotIn("ben@buyer.test", text)


class SwitchAndSiteTests(MembersTestCase):
    PATHS = ("signin/request/", "signin/verify/", "me/", "signout/")

    def every(self, **kwargs):
        return [(path, self.post(path, **kwargs)) for path in self.PATHS]

    def assert_all_not_found(self, results):
        for path, response in results:
            with self.subTest(path=path):
                self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))

    @override_settings(MEMBERS_ENABLED=False)
    def test_off_every_route_answers_the_one_404_and_reads_no_member_table(self):
        with mock.patch.object(Member.objects, "filter") as touched:
            self.assert_all_not_found(self.every(email=EMAIL, code="123456", token="x"))
        touched.assert_not_called()
        self.assertEqual(mail.outbox, [])

    @override_settings(MEMBERS_ENABLED=False)
    def test_off_it_answers_even_for_a_signed_in_member(self):
        token = self.sign_in_when_on()
        self.assert_all_not_found([("me/", self.post("me/", token=token))])

    def sign_in_when_on(self):
        with override_settings(MEMBERS_ENABLED=True):
            return self.sign_in()

    def test_an_unknown_foreign_or_missing_host_answers_the_same_404(self):
        for host in ("nobody.negosyotest.com", "example.com", "", "anas-store.negosyotest.com.evil.test"):
            with self.subTest(host=host):
                self.assert_all_not_found(self.every(host=host, email=EMAIL, code="123456", token="x"))
        self.assertEqual(mail.outbox, [])

    def test_a_site_that_is_not_a_sante_site_answers_the_same_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        self.assert_all_not_found(self.every(email=EMAIL, code="123456", token="x"))
        self.assertEqual(mail.outbox, [])

    def test_a_deleted_site_answers_the_same_404(self):
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.assert_all_not_found(self.every(email=EMAIL, code="123456", token="x"))

    def test_the_server_credential_is_required_on_every_route(self):
        for secret in (None, "wrong-" * 8):
            for path, response in self.every(secret=secret, email=EMAIL):
                with self.subTest(path=path, secret=secret):
                    self.assertEqual((response.status_code, response.json()), (401, UNAUTHENTICATED))
        self.assertEqual(mail.outbox, [])

    def test_a_browser_get_is_not_a_route(self):
        for path in self.PATHS:
            response = self.client.get(
                BASE + path, {"host": HOST}, secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}"
            )
            self.assertEqual(response.status_code, 405, path)
