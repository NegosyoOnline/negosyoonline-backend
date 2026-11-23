"""The member routes: request a code, resend, verify it, me, sign out."""
import logging
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.members import hooks, otp, services, sessions, signin
from apps.members.models import Member, MemberContactAudit, MemberOtpChallenge, MemberSession
from apps.members.tests.base import (
    BASE, BINDING, EMAIL, HOST, MOBILE, NOT_FOUND, NOT_SIGNED_IN, OTHER_BINDING, OTHER_HOST,
    SECRET, VISITOR, MembersTestCase,
)
from apps.sites.models import Site
from apps.storefront.tests.base import UNAUTHENTICATED

GENERIC = {"step": "code", "message": signin.GENERIC, "offer_email": False}


def wrong_for(code):
    return "000000" if code != "000000" else "111111"


class RequestTests(MembersTestCase):
    def test_a_member_is_emailed_a_six_digit_code_and_the_answer_is_generic(self):
        response = self.request_code()

        self.assertEqual((response.status_code, response.json()), (200, GENERIC))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertRegex(mail.outbox[0].body, r"code is \d{6}\. It expires in 10 minutes")
        self.assertNotIn(self.last_code(), response.content.decode())

    def test_branded_switch_on_sends_html_from_the_store_sender_with_the_code_once(self):
        with override_settings(STORE_EMAILS_BRANDED=True, STORE_SENDERS="anas-store=orders@liveformore.test"):
            self.request_code()
        sent = mail.outbox[0]
        code = self.last_code()
        self.assertEqual(sent.from_email, '"Ana\'s Store" <orders@liveformore.test>')
        self.assertEqual(sent.reply_to, [self.site.account.email] if self.site.account.email_verified_at else [])
        self.assertEqual(sent.alternatives[0][0].count(code), 1)
        self.assertNotIn(code, sent.subject)

    def test_the_email_goes_out_after_the_rows_are_written_and_before_the_floor(self):
        order = []
        real_pad = signin.pad

        def pad(started):
            order.append(("pad", len(mail.outbox), MemberOtpChallenge.objects.count()))
            real_pad(started)

        with mock.patch.object(signin, "pad", pad):
            self.request_code()
        self.assertEqual(order, [("pad", 1, 1)])

    def test_the_address_is_matched_whatever_its_case_or_spacing(self):
        self.assertEqual(self.request_code("  Maria@Buyer.TEST ").status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_an_unknown_address_gets_the_same_answer_a_decoy_row_and_no_email(self):
        known, unknown = self.request_code(), self.request_code("nobody@buyer.test", binding=OTHER_BINDING)
        self.assertEqual((known.status_code, known.json()), (unknown.status_code, unknown.json()))
        self.assertEqual(len(mail.outbox), 1)
        decoy = MemberOtpChallenge.objects.get(requested_contact="nobody@buyer.test")
        self.assertIsNone(decoy.member)
        self.assertTrue(decoy.code_hash.startswith(otp.DECOY_PREFIX))

    def test_a_member_of_another_site_gets_the_same_answer_and_no_email(self):
        self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550111")
        response = self.request_code("ben@buyer.test")
        self.assertEqual((response.status_code, response.json()), (200, GENERIC))
        self.assertEqual(mail.outbox, [])

    def test_a_malformed_contact_is_a_validation_error(self):
        for contact in ("not-an-email@", "12345", ""):
            with self.subTest(contact=contact):
                response = self.request_code(contact)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.assertEqual(mail.outbox, [])

    def test_a_binding_is_required_and_must_be_a_real_value(self):
        for binding in ("", "short", "x" * 201):
            with self.subTest(binding=binding):
                response = self.request_code(binding=binding)
                self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid_request"))
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
                self.request_code(email, binding=email.ljust(20, "x"))
            self.assertEqual(len(naps), 1, email)

    def test_a_second_request_inside_the_minute_is_refused_for_everyone(self):
        for email in (EMAIL, "nobody@buyer.test"):
            with self.subTest(email=email):
                self.assertEqual(self.request_code(email).status_code, 200)
                response = self.request_code(email)
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.json()["error"]["code"], "rate_limited")

    def test_the_fourth_request_in_ten_minutes_is_refused_for_everyone(self):
        for email in (EMAIL, "nobody@buyer.test"):
            with self.subTest(email=email):
                cache.clear()
                for _ in range(otp.SEND_LIMIT):
                    self.assertEqual(self.request_code(email).status_code, 200)
                    self.age_challenges()
                self.assertEqual(self.request_code(email).status_code, 429)

    def test_one_visitor_address_is_limited_across_addresses(self):
        for number in range(otp.IP_SEND_LIMIT[1]):
            self.assertEqual(self.request_code(f"p{number}@buyer.test").status_code, 200)
        self.assertEqual(self.request_code("last@buyer.test").status_code, 429)

    def test_the_site_flood_monitor_warns_once_and_never_refuses(self):
        key = f"members:signin-volume:{self.site.pk}:{timezone.now().strftime('%Y%m%d%H')}"
        cache.set(key, otp.SITE_MONITOR - 1, 3600)
        with self.assertLogs(otp.logger, logging.WARNING) as logged:
            for number in range(3):
                self.assertEqual(self.request_code(f"f{number}@buyer.test").status_code, 200)
        warnings = [line for line in logged.output if "More than" in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn(self.site.public_id, warnings[0])
        self.assertNotIn("buyer.test", warnings[0])

    def test_a_cache_failure_in_the_monitor_never_blocks_a_request(self):
        with mock.patch.object(otp, "cache") as broken:
            broken.add.side_effect = RuntimeError("down")
            with self.assertLogs(otp.logger, logging.ERROR):
                self.assertEqual(self.request_code().status_code, 200)

    def test_an_email_that_cannot_be_sent_is_answered_like_one_that_was(self):
        from apps.notifications.services import EmailNotSent
        from apps.members import messaging

        with mock.patch.object(messaging, "send_store_email", side_effect=EmailNotSent()):
            with self.assertLogs(messaging.logger, logging.WARNING) as logged:
                response = self.request_code()
        self.assertEqual((response.status_code, response.json()), (200, GENERIC))
        self.assertNotIn(EMAIL, "".join(logged.output))
        self.assertEqual(MemberOtpChallenge.objects.get().delivery, "failed")

    def test_the_email_carries_the_store_name_and_never_a_link(self):
        self.request_code()
        message = mail.outbox[0]
        self.assertIn("Ana's Store", message.subject)
        self.assertNotIn("http", message.body)
        self.assertIn("If you did not ask for this code", message.body)

    def test_an_email_is_a_channel_only_while_pending_or_verified(self):
        for state, expected in (("verified", 1), ("pending", 1), ("unverified", 0)):
            with self.subTest(state=state):
                mail.outbox.clear()
                MemberOtpChallenge.objects.all().delete()
                cache.clear()
                Member.objects.filter(pk=self.member.pk).update(email_state=state)
                self.request_code()
                self.assertEqual(len(mail.outbox), expected)

    def test_a_resend_keeps_the_code_inside_its_ten_minutes(self):
        self.request_code()
        first = self.last_code()
        self.age_challenges()
        response = self.post("signin/resend/")
        self.assertEqual((response.status_code, response.json()), (200, GENERIC))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(self.last_code(), first)

    def test_a_resend_with_no_code_asked_for_is_expired(self):
        response = self.post("signin/resend/")
        self.assertEqual(response.json()["error"]["code"], "code_expired")


class MobileRequestTests(MembersTestCase):
    """A mobile identifier needs the SMS channel, which the SMS slice registers."""

    def tearDown(self):
        hooks._sms_transport.clear()
        hooks._sign_in_sms_first.clear()
        super().tearDown()

    def test_without_the_sms_slice_a_mobile_is_unavailable_for_every_input(self):
        for contact in (MOBILE, "09175550999"):
            with self.subTest(contact=contact):
                response = self.request_code(contact)
                self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "sms_unavailable"))
        self.assertEqual(MemberOtpChallenge.objects.count(), 0)

    def register(self, outcome="queued", precheck=None):
        self.sent = []

        def deliver(challenge, code, category):
            self.sent.append((challenge.target, code, category))
            return outcome

        hooks.register_sms_transport(available=lambda: True, deliver_code=deliver, precheck=precheck)

    def test_an_unconfirmed_onboarding_number_gets_no_code(self):
        self.register()
        response = self.request_code(MOBILE)
        self.assertEqual((response.status_code, response.json()), (200, GENERIC))
        self.assertEqual(self.sent, [])
        self.assertIsNone(MemberOtpChallenge.objects.get().member)

    def test_a_confirmed_mobile_goes_by_sms_through_the_hook(self):
        self.register()
        Member.objects.filter(pk=self.member.pk).update(mobile_state="verified")
        response = self.request_code(MOBILE)
        self.assertEqual((response.status_code, response.json()), (200, GENERIC))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "+639171234567")
        self.assertEqual(mail.outbox, [])
        self.assertEqual(MemberOtpChallenge.objects.get().channel, "sms")

    def test_a_verified_email_is_the_default_channel_for_a_typed_mobile(self):
        self.register()
        Member.objects.filter(pk=self.member.pk).update(email_state="verified", mobile_state="verified")
        self.request_code(MOBILE)
        self.assertEqual((self.sent, len(mail.outbox)), ([], 1))

    def test_a_verified_email_is_the_channel_even_when_the_typed_mobile_is_not_confirmed(self):
        self.register()
        Member.objects.filter(pk=self.member.pk).update(email_state="verified", mobile_state="pending")
        response = self.request_code(MOBILE)
        self.assertEqual((response.status_code, response.json()), (200, GENERIC))
        self.assertEqual((self.sent, len(mail.outbox)), ([], 1))
        self.assertEqual(MemberOtpChallenge.objects.get().channel, "email")

    def test_a_template_that_texts_first_overrides_the_email(self):
        self.register()
        hooks.register_sign_in_sms_first(lambda site: True)
        Member.objects.filter(pk=self.member.pk).update(email_state="verified", mobile_state="verified")
        self.request_code(MOBILE)
        self.assertEqual((len(self.sent), len(mail.outbox)), (1, 0))

    def test_a_precheck_refusal_answers_before_any_member_lookup(self):
        from apps.common.errors import ApiError

        def refuse(site, mobile):
            raise ApiError("not_test_number", "This number cannot receive messages yet.")

        self.register(precheck=refuse)
        for contact in (MOBILE, "09175550999"):
            response = self.request_code(contact)
            self.assertEqual(response.json()["error"]["code"], "not_test_number")

    def test_a_held_sms_supersedes_its_code(self):
        self.register(outcome="paused")
        Member.objects.filter(pk=self.member.pk).update(mobile_state="verified")
        response = self.request_code(MOBILE)
        self.assertEqual(response.json()["error"]["code"], "sms_paused")
        row = MemberOtpChallenge.objects.get()
        self.assertEqual(row.delivery, "held")
        self.assertIsNotNone(row.superseded_at)


class VerifyTests(MembersTestCase):
    def verify(self, code, host=HOST, binding=BINDING, **extra):
        return self.post("signin/verify/", host=host, binding=binding, code=code, **extra)

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
            verified, self.request_code("nobody@buyer.test", binding=OTHER_BINDING),
            self.post("me/", token=token), self.post("signout/", token=token),
        ):
            self.assertEqual(len(response.cookies), 0)

    def test_the_first_code_marks_the_email_verified_and_audits_it(self):
        self.request_code()
        self.verify(self.last_code(), ip="203.0.113.60")
        self.member.refresh_from_db()
        self.assertEqual((self.member.email_state, self.member.mobile_state), ("verified", "pending"))
        self.assertIsNotNone(self.member.email_verified_at)
        self.assertIsNone(self.member.mobile_verified_at)
        audit = MemberContactAudit.objects.get()
        self.assertEqual((audit.kind, audit.field, audit.actor_type), ("first_verification", "email", "member"))
        self.assertEqual(audit.new_masked, "m***@buyer.test")
        self.assertEqual((audit.old_masked, audit.ip_advisory), ("", "203.0.113.60"))

    def test_a_second_sign_in_writes_no_second_audit_row(self):
        self.sign_in()
        self.age_challenges()
        self.sign_in()
        self.assertEqual(MemberContactAudit.objects.count(), 1)

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
        again = self.verify(code)
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (400, "code_expired"))
        self.assertEqual(MemberSession.objects.count(), 1)

    def test_a_wrong_code_is_refused_and_makes_no_session(self):
        self.request_code()
        response = self.verify(wrong_for(self.last_code()))
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "wrong_code"))
        self.assertEqual(MemberSession.objects.count(), 0)
        self.member.refresh_from_db()
        self.assertEqual(self.member.email_state, "pending")

    def test_the_sixth_try_fails_even_with_the_right_code(self):
        self.request_code()
        right = self.last_code()
        for _ in range(otp.MAX_GUESSES):
            self.assertEqual(self.verify(wrong_for(right)).status_code, 400)
        burned = self.verify(right)
        self.assertEqual(burned.json()["error"]["code"], "code_burned")
        self.assertEqual(MemberSession.objects.count(), 0)

    def test_an_expired_code_is_refused(self):
        self.request_code()
        code = self.last_code()
        MemberOtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verify(code).json()["error"]["code"], "code_expired")

    def test_a_code_is_good_only_for_the_browser_it_was_sent_to(self):
        self.request_code()
        code = self.last_code()
        self.assertEqual(self.verify(code, binding=OTHER_BINDING).json()["error"]["code"], "code_expired")
        self.assertEqual(self.verify(code).status_code, 200)

    def test_a_newer_code_replaces_an_older_one_of_the_same_browser(self):
        self.request_code()
        self.age_challenges()
        self.request_code("maria@buyer.test")
        newest = MemberOtpChallenge.objects.order_by("-pk").first()
        self.assertEqual(MemberOtpChallenge.objects.filter(superseded_at__isnull=False).count(), 1)
        self.assertIsNone(newest.superseded_at)

    def test_a_decoy_never_signs_anyone_in(self):
        self.request_code("nobody@buyer.test")
        for code in ("123456", "000000"):
            response = self.verify(code)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(MemberSession.objects.count(), 0)

    def test_a_code_issued_for_one_site_is_not_accepted_on_another(self):
        self.request_code()
        code = self.last_code()
        self.assertEqual(self.verify(code, host=OTHER_HOST).json()["error"]["code"], "code_expired")

    def test_a_contact_change_after_the_code_was_sent_voids_it(self):
        self.request_code()
        code = self.last_code()
        Member.objects.filter(pk=self.member.pk).update(email="changed@buyer.test")
        self.assertEqual(self.verify(code).json()["error"]["code"], "code_expired")
        self.assertEqual(MemberSession.objects.count(), 0)

    def test_one_visitor_address_is_limited_on_verify(self):
        for _ in range(otp.IP_VERIFY_LIMIT[1]):
            self.verify("000000")
        self.assertEqual(self.verify("000000").status_code, 429)

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
        self.age_challenges()
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
        _, raw = sessions.create_session(self.site, stranger)  # a corrupt row: site and member differ
        self.assertEqual(self.post("me/", token=raw).json(), NOT_SIGNED_IN)

    def test_nothing_of_another_member_is_answered(self):
        self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550115", name="Ben Cruz")
        text = self.post("me/", token=self.sign_in()).content.decode()
        self.assertNotIn("Ben Cruz", text)
        self.assertNotIn("ben@buyer.test", text)


class SwitchAndSiteTests(MembersTestCase):
    PATHS = ("signin/request/", "signin/resend/", "signin/accept-email/", "signin/verify/", "me/", "signout/")

    def every(self, **kwargs):
        return [(path, self.post(path, **kwargs)) for path in self.PATHS]

    def assert_all_not_found(self, results):
        for path, response in results:
            with self.subTest(path=path):
                self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))

    @override_settings(MEMBERS_ENABLED=False)
    def test_off_every_route_answers_the_one_404_and_reads_no_member_table(self):
        with mock.patch.object(Member.objects, "filter") as touched, mock.patch.object(
            MemberOtpChallenge.objects, "filter"
        ) as challenges:
            self.assert_all_not_found(self.every(contact=EMAIL, code="123456", token="x"))
        touched.assert_not_called()
        challenges.assert_not_called()
        self.assertEqual(mail.outbox, [])

    @override_settings(MEMBERS_ENABLED=False)
    def test_off_it_answers_even_for_a_signed_in_member(self):
        with override_settings(MEMBERS_ENABLED=True):
            token = self.sign_in()
        self.assert_all_not_found([("me/", self.post("me/", token=token))])

    def test_an_unknown_foreign_or_missing_host_answers_the_same_404(self):
        for host in ("nobody.negosyotest.com", "example.com", "", "anas-store.negosyotest.com.evil.test"):
            with self.subTest(host=host):
                self.assert_all_not_found(self.every(host=host, contact=EMAIL, code="123456", token="x"))
        self.assertEqual(mail.outbox, [])

    def test_a_site_that_is_not_a_sante_site_answers_the_same_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        self.assert_all_not_found(self.every(contact=EMAIL, code="123456", token="x"))
        self.assertEqual(mail.outbox, [])

    def test_a_deleted_site_answers_the_same_404(self):
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.assert_all_not_found(self.every(contact=EMAIL, code="123456", token="x"))

    def test_the_server_credential_is_required_on_every_route(self):
        for secret in (None, "wrong-" * 8):
            for path in self.PATHS:
                cache.clear()  # the surface limits failed credentials per address
                response = self.post(path, secret=secret, contact=EMAIL)
                with self.subTest(path=path, secret=secret):
                    self.assertEqual((response.status_code, response.json()), (401, UNAUTHENTICATED))
        self.assertEqual(mail.outbox, [])

    def test_a_browser_get_is_not_a_route(self):
        for path in self.PATHS:
            response = self.client.get(
                BASE + path, {"host": HOST}, secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}"
            )
            self.assertEqual(response.status_code, 405, path)


class HeldStoreTests(MembersTestCase):
    """H3, decision C: a store HQ suspended by hand (operator_hold) still answers its members."""

    def test_members_still_get_sign_in_codes_and_read_their_session(self):
        token = self.sign_in()
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)
        cache.clear()
        self.age_challenges()
        before = len(mail.outbox)
        self.assertEqual(self.request_code().status_code, 200)
        self.assertEqual(len(mail.outbox), before + 1)
        self.assertEqual(self.post("me/", token=token).status_code, 200)
        self.assertEqual(self.post("order-log/", token=token).status_code, 200)

    def test_the_public_lookup_stays_404_and_a_removed_store_is_404_for_members(self):
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)
        response = self.client.get("/v1/public/sites/by-host/", {"host": HOST}, secure=True)
        self.assertEqual(response.status_code, 404)
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.request_code().status_code, 404)
