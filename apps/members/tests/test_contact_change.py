"""Email verification and the self-service contact change (service logic; the routes come later)."""
import re

from django.core import mail

from apps.common.errors import ApiError
from apps.members import contact_change, hooks, otp, sessions
from apps.members.models import Member, MemberContactAudit, MemberOtpChallenge
from apps.members.tests.base import EMAIL, MembersTestCase


def code_in(message):
    return re.search(r"code is (\d{6})", message.body).group(1)


class ContactChangeTestCase(MembersTestCase):
    def setUp(self):
        super().setUp()
        Member.objects.filter(pk=self.member.pk).update(email_state="verified", mobile_state="verified")
        self.member.refresh_from_db()
        self.session, self.raw = sessions.create_session(self.site, self.member)

    def tearDown(self):
        hooks._sms_transport.clear()
        super().tearDown()

    def age(self):
        for row in MemberOtpChallenge.objects.all():
            MemberOtpChallenge.objects.filter(pk=row.pk).update(created_at=row.created_at.replace(year=2020))


class EmailVerifyTests(ContactChangeTestCase):
    def test_a_pending_email_is_verified_with_a_code_bound_to_the_session(self):
        Member.objects.filter(pk=self.member.pk).update(email_state="pending", email_verified_at=None)
        self.member.refresh_from_db()
        contact_change.email_verification_start(self.site, self.member, session_raw=self.raw)
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        member = contact_change.email_verification_confirm(
            self.site, self.member, session=self.session, session_raw=self.raw,
            code=code_in(mail.outbox[-1]), ip="203.0.113.3",
        )
        self.assertEqual(member.email_state, "verified")
        self.assertEqual(MemberContactAudit.objects.get().kind, "first_verification")

    def test_an_already_verified_email_needs_nothing(self):
        with self.assertRaises(ApiError) as caught:
            contact_change.email_verification_start(self.site, self.member, session_raw=self.raw)
        self.assertEqual(caught.exception.code, "already_verified")

    def test_another_session_cannot_use_the_code(self):
        Member.objects.filter(pk=self.member.pk).update(email_state="pending")
        self.member.refresh_from_db()
        contact_change.email_verification_start(self.site, self.member, session_raw=self.raw)
        with self.assertRaises(ApiError):
            contact_change.email_verification_confirm(
                self.site, self.member, session=self.session, session_raw="other-session-token-0123456789",
                code=code_in(mail.outbox[-1]),
            )

    def test_a_revoked_session_cannot_complete_it(self):
        Member.objects.filter(pk=self.member.pk).update(email_state="pending")
        self.member.refresh_from_db()
        contact_change.email_verification_start(self.site, self.member, session_raw=self.raw)
        sessions.revoke_session(self.session)
        with self.assertRaises(ApiError) as caught:
            contact_change.email_verification_confirm(
                self.site, self.member, session=self.session, session_raw=self.raw, code=code_in(mail.outbox[-1])
            )
        self.assertEqual((caught.exception.code, caught.exception.status), ("signed_out", 401))


class ChangeEmailTests(ContactChangeTestCase):
    def prove_current(self):
        contact_change.current_proof_start(self.site, self.member, session_raw=self.raw, field="email")
        return contact_change.current_proof_confirm(
            self.site, self.member, session_raw=self.raw, field="email", code=code_in(mail.outbox[-1])
        )

    def test_the_whole_change_revokes_every_session_audits_and_notifies_the_old_email(self):
        other, _ = sessions.create_session(self.site, self.member)
        self.assertEqual(contact_change.change_state(self.site, self.member, session_raw=self.raw, field="email"), "current")
        self.prove_current()
        self.assertEqual(contact_change.change_state(self.site, self.member, session_raw=self.raw, field="email"), "replacement")

        contact_change.replacement_start(
            self.site, self.member, session_raw=self.raw, field="email", raw_value=" New@Buyer.test "
        )
        self.assertEqual(mail.outbox[-1].to, ["new@buyer.test"])
        member = contact_change.replacement_confirm(
            self.site, self.member, session=self.session, session_raw=self.raw, field="email",
            code=code_in(mail.outbox[-1]), ip="203.0.113.4",
        )

        self.assertEqual((member.email, member.email_state), ("new@buyer.test", "verified"))
        for row in (self.session, other):
            row.refresh_from_db()
            self.assertEqual(row.revoke_reason, "contact_change")
        audit = MemberContactAudit.objects.get()
        self.assertEqual((audit.kind, audit.field, audit.actor_type), ("self_service", "email", "member"))
        self.assertEqual((audit.old_masked, audit.new_masked), ("m***@buyer.test", "n***@buyer.test"))
        notice = mail.outbox[-1]
        self.assertEqual(notice.to, [EMAIL])
        self.assertIn("was changed", notice.body)
        self.assertNotIn("new@buyer.test", notice.body)
        self.assertNotRegex(notice.body, r"\d{6}")

    def test_the_replacement_needs_the_current_contact_first(self):
        with self.assertRaises(ApiError) as caught:
            contact_change.replacement_start(
                self.site, self.member, session_raw=self.raw, field="email", raw_value="new@buyer.test"
            )
        self.assertEqual(caught.exception.code, "current_proof_required")

    def test_the_same_or_a_taken_email_is_refused(self):
        self.prove_current()
        taken = self.new_member(email="taken@buyer.test", mobile="09175550160")
        for value, code in ((EMAIL, "same_contact"), ("taken@buyer.test", "contact_in_use")):
            with self.subTest(value=value), self.assertRaises(ApiError) as caught:
                contact_change.replacement_start(
                    self.site, self.member, session_raw=self.raw, field="email", raw_value=value
                )
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(taken.email, "taken@buyer.test")

    def test_a_replacement_code_does_not_prove_the_current_contact(self):
        self.prove_current()
        contact_change.replacement_start(
            self.site, self.member, session_raw=self.raw, field="email", raw_value="new@buyer.test"
        )
        with self.assertRaises(ApiError):
            contact_change.current_proof_confirm(
                self.site, self.member, session_raw=self.raw, field="email", code=code_in(mail.outbox[-1])
            )

    def test_a_pending_contact_is_never_sent_the_current_proof(self):
        Member.objects.filter(pk=self.member.pk).update(email_state="pending", mobile_state="pending")
        self.member.refresh_from_db()
        with self.assertRaises(ApiError) as caught:
            contact_change.current_proof_start(self.site, self.member, session_raw=self.raw, field="email")
        self.assertEqual(caught.exception.code, "no_verified_contact")

    def test_a_mobile_change_with_no_verified_mobile_is_proven_by_the_verified_email(self):
        Member.objects.filter(pk=self.member.pk).update(mobile_state="pending")
        self.member.refresh_from_db()
        contact_change.current_proof_start(self.site, self.member, session_raw=self.raw, field="mobile")
        self.assertEqual(mail.outbox[-1].to, [EMAIL])

    def test_a_mobile_change_goes_by_the_sms_hook_and_without_it_is_unavailable(self):
        with self.assertRaises(ApiError) as caught:
            contact_change.current_proof_start(self.site, self.member, session_raw=self.raw, field="mobile")
        self.assertEqual((caught.exception.code, caught.exception.status), ("sms_unavailable", 503))
        sent = []
        hooks.register_sms_transport(
            available=lambda: True, deliver_code=lambda ch, code, category: sent.append((ch.target, ch.purpose)) or "queued"
        )
        contact_change.current_proof_start(self.site, self.member, session_raw=self.raw, field="mobile")
        self.assertEqual(sent, [("+639171234567", "current_proof")])

    def test_a_contact_notice_also_texts_the_old_mobile_when_the_slice_is_registered(self):
        texts = []
        hooks.register_sms_transport(
            available=lambda: True, deliver_code=lambda *a: "queued",
            send_notice=lambda site, mobile, body, key: texts.append((mobile, body, key)),
        )
        self.prove_current()
        contact_change.replacement_start(
            self.site, self.member, session_raw=self.raw, field="email", raw_value="new@buyer.test"
        )
        contact_change.replacement_confirm(
            self.site, self.member, session=self.session, session_raw=self.raw, field="email",
            code=code_in(mail.outbox[-1]),
        )
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0][0], "+639171234567")
        self.assertIn("Ana's Store", texts[0][1])

    def test_the_member_sends_are_limited_per_hour(self):
        for _ in range(otp.MEMBER_LIMIT[1]):
            otp.throttle_signed_in(self.member, "")
        with self.assertRaises(ApiError) as caught:
            contact_change.current_proof_start(self.site, self.member, session_raw=self.raw, field="email")
        self.assertEqual(caught.exception.status, 429)
