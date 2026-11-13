"""Tenant isolation of member sign-in, sessions and codes (ClickUp z8uq9m7ycc, D and F).

The same email and the same mobile belong to a member of Ana's store and to a member of Ben's.
One browser (one binding) visits both. A code, a session or a text of one store must never be
usable on, or readable from, the other.
"""
from unittest import mock

from django.core import mail
from django.core.cache import cache

from apps.common.errors import ApiError
from apps.members import hooks, sessions, sms as member_sms
from apps.members.models import Member, MemberOtpChallenge, MemberSession
from apps.members.tests.base import (
    EMAIL, HOST, MOBILE, NOT_SIGNED_IN, OTHER_HOST, MembersTestCase,
)
from apps.notifications.models import SmsMessage
from apps.notifications.tests.base import gateway_on, online_phone

NUMBER = "+639171234567"


class TwoStoresOneBuyerCase(MembersTestCase):
    """self.member is Maria on Ana's store; self.ben is Maria's twin (same email and mobile) on
    Ben's. Both contacts are confirmed. The service refuses a contact held on another live store
    (test_the_service_refuses_...), so the twin is written directly: the state a race, a legacy row
    or a released contact could leave, and the one isolation must hold in."""

    def setUp(self):
        super().setUp()
        self.ben = Member.objects.create(
            site=self.other_site, full_name="Maria Ben-Store", mobile=self.member.mobile,
            email=self.member.email,
        )
        Member.objects.filter(site__in=[self.site, self.other_site]).update(
            email_state="verified", mobile_state="verified")

    def me_calls(self, token, host):
        return {path: self.post(path, host=host, token=token)
                for path in ("me/", "account/", "account/verify-email/")}


class SignInTests(TwoStoresOneBuyerCase):
    def test_the_service_refuses_a_contact_held_on_another_live_store(self):
        with self.assertRaises(ApiError) as caught:
            self.new_member(self.other_site, email="x@buyer.test", name="Twin")  # mobile is held
        self.assertEqual(caught.exception.code, "contact_in_use")
        self.assertNotIn(self.site.public_id, str(caught.exception.fields))

    def test_a_code_emailed_for_one_store_does_not_sign_in_the_other(self):
        self.assertEqual(self.request_code(EMAIL, HOST).status_code, 200)
        code = self.last_code()

        wrong = self.post("signin/verify/", host=OTHER_HOST, code=code)
        self.assertEqual(wrong.status_code, 400, wrong.content)
        self.assertEqual(MemberSession.objects.count(), 0)
        right = self.post("signin/verify/", host=HOST, code=code)
        self.assertEqual(right.status_code, 200, right.content)
        self.assertEqual(right.json()["member"]["full_name"], "Maria Santos")
        self.assertEqual(MemberSession.objects.get().site, self.site)

    def test_each_store_signs_the_same_email_in_as_its_own_member_with_its_own_code(self):
        token_a = self.sign_in(EMAIL, HOST)
        cache.clear()
        token_b = self.sign_in(EMAIL, OTHER_HOST)

        self.assertNotEqual(token_a, token_b)
        self.assertEqual(self.post("me/", host=HOST, token=token_a).json()["member"]["full_name"],
                         "Maria Santos")
        self.assertEqual(self.post("me/", host=OTHER_HOST, token=token_b).json()["member"]["full_name"],
                         "Maria Ben-Store")
        self.assertEqual(
            sorted(MemberSession.objects.values_list("site_id", "member_id")),
            sorted([(self.site.pk, self.member.pk), (self.other_site.pk, self.ben.pk)]),
        )

    def test_a_session_of_one_store_is_refused_on_every_member_route_of_the_other(self):
        token_a = self.sign_in(EMAIL, HOST)
        cache.clear()
        token_b = self.sign_in(EMAIL, OTHER_HOST)
        before = list(MemberSession.objects.order_by("pk").values())

        for token, host in ((token_a, OTHER_HOST), (token_b, HOST)):
            for path, response in self.me_calls(token, host).items():
                with self.subTest(path=path, host=host):
                    self.assertEqual((response.status_code, response.json()), (401, NOT_SIGNED_IN))
        # Signing out on the wrong store ends nobody's session.
        self.post("signout/", host=OTHER_HOST, token=token_a)
        self.assertEqual(list(MemberSession.objects.order_by("pk").values()), before)
        self.assertEqual(self.post("me/", host=HOST, token=token_a).status_code, 200)

    def test_one_stores_resend_never_resends_the_others_code(self):
        self.request_code(EMAIL, HOST)
        self.age_challenges()
        mail.outbox.clear()
        before = MemberOtpChallenge.objects.count()

        response = self.post("signin/resend/", host=OTHER_HOST)  # same browser binding, other store

        self.assertEqual(response.json()["error"]["code"], "code_expired")
        self.assertEqual((len(mail.outbox), MemberOtpChallenge.objects.count()), (0, before))

    def test_the_other_stores_member_is_not_told_apart_from_a_stranger(self):
        self.new_member(self.other_site, email="only-ben@buyer.test", mobile="09175550777", name="Only Ben")
        known_elsewhere = self.request_code("only-ben@buyer.test", HOST)
        cache.clear()
        stranger = self.request_code("nobody@buyer.test", HOST, binding="x-another-binding-0123456789")

        self.assertEqual((known_elsewhere.status_code, known_elsewhere.json()),
                         (stranger.status_code, stranger.json()))
        self.assertEqual(mail.outbox, [])


@gateway_on
class SmsIsolationTests(TwoStoresOneBuyerCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        hooks._sms_transport.clear()
        hooks._sign_in_sms_first.clear()
        member_sms.register()
        self.addCleanup(hooks._sms_transport.clear)
        self.addCleanup(hooks._sign_in_sms_first.clear)
        later = mock.patch("apps.operators.background.run_later")
        later.start()
        self.addCleanup(later.stop)
        online_phone()
        # A verified email is the default channel for a typed mobile; a pending one is not.
        Member.objects.filter(site__in=[self.site, self.other_site]).update(email_state="pending")

    def texted_code(self, host):
        """Ask for a code by mobile on this store. Returns the code the gateway was handed."""
        sent = []
        real = member_sms.send_sms

        def spy(**kwargs):
            sent.append(kwargs)
            return real(**kwargs)

        with mock.patch.object(member_sms, "send_sms", spy):
            response = self.request_code(MOBILE, host)
        self.assertEqual(response.status_code, 200, response.content)
        (call,) = sent
        return call["text"].split("code is ")[1][:6], call["text"]

    def test_a_texted_code_belongs_to_the_store_that_asked_for_it(self):
        code, text = self.texted_code(HOST)

        (row,) = SmsMessage.objects.all()
        self.assertEqual((row.site_id, row.recipient, row.purpose), (self.site.pk, NUMBER, "member_signin_code"))
        self.assertIn(self.site.display_name, text)
        self.assertEqual(MemberOtpChallenge.objects.get().site, self.site)

        wrong = self.post("signin/verify/", host=OTHER_HOST, code=code)
        self.assertEqual(wrong.status_code, 400, wrong.content)
        self.assertEqual(MemberSession.objects.count(), 0)
        right = self.post("signin/verify/", host=HOST, code=code)
        self.assertEqual(right.json()["member"]["full_name"], "Maria Santos")

    def test_each_store_gets_its_own_text_row_and_code_for_the_same_number(self):
        code_a, _ = self.texted_code(HOST)
        self.age_challenges()
        cache.clear()
        code_b, text_b = self.texted_code(OTHER_HOST)

        by_site = {row.site_id: row for row in SmsMessage.objects.all()}
        self.assertEqual(sorted(by_site), sorted([self.site.pk, self.other_site.pk]))
        self.assertIn(self.other_site.display_name, text_b)
        self.assertEqual({c.site_id for c in MemberOtpChallenge.objects.all()},
                         {self.site.pk, self.other_site.pk})
        for challenge in MemberOtpChallenge.objects.all():
            self.assertEqual(challenge.sms_message_id, by_site[challenge.site_id].pk)
        # Each code opens its own store and only that store (when two are equal, only their own).
        cross = self.post("signin/verify/", host=HOST, code=code_b)
        if code_a != code_b:
            self.assertEqual(cross.status_code, 400, cross.content)
            self.assertEqual(MemberSession.objects.count(), 0)
        self.assertEqual(self.post("signin/verify/", host=OTHER_HOST, code=code_b).json()["member"]
                         ["full_name"], "Maria Ben-Store")

    def test_a_decoy_on_one_store_writes_no_text_row_on_any_store(self):
        self.texted_code(HOST)
        rows = list(SmsMessage.objects.order_by("pk").values_list("pk", "site_id"))

        # A number nobody holds, asked for on the other store, makes no text row anywhere.
        response = self.request_code("09175550999", OTHER_HOST, binding="x-another-binding-0123456789")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(SmsMessage.objects.order_by("pk").values_list("pk", "site_id")), rows)

    def test_a_code_texted_for_one_store_is_not_in_the_others_rows_challenges_or_sessions(self):
        code, _ = self.texted_code(HOST)

        self.assertEqual(SmsMessage.objects.filter(site=self.other_site).count(), 0)
        self.assertEqual(MemberOtpChallenge.objects.filter(site=self.other_site).count(), 0)
        for row in SmsMessage.objects.values():
            self.assertNotIn(code, "".join(str(v) for v in row.values()))
        self.assertNotRegex(MemberOtpChallenge.objects.get().code_hash, r"^\d{6}$")
        self.assertIsNone(sessions.find_session(self.other_site, code))

