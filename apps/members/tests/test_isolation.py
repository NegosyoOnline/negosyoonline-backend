"""Every member table is scoped to its site: nothing of one site's members reaches another's."""
from apps.members import contact_change, otp, sessions
from apps.members.models import Member, MemberOtpChallenge, MemberSession
from apps.members.tests.base import BINDING, OTHER_HOST, MembersTestCase


class IsolationTests(MembersTestCase):
    def setUp(self):
        super().setUp()
        self.ben = self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550120", name="Ben Cruz")

    def test_each_member_row_names_one_site_and_the_session_the_same(self):
        for member in Member.objects.all():
            self.assertIn(member.site, (self.site, self.other_site))
        _, raw = sessions.create_session(self.other_site, self.ben)
        self.assertEqual(MemberSession.objects.get().site, self.other_site)
        self.assertIsNone(sessions.find_session(self.site, raw))
        self.assertIsNotNone(sessions.find_session(self.other_site, raw))

    def test_a_code_requested_on_one_site_signs_in_nobody_on_the_other(self):
        self.request_code("ben@buyer.test", host=OTHER_HOST)
        code = self.last_code()
        wrong_site = self.post("signin/verify/", code=code)
        self.assertEqual(wrong_site.status_code, 400)
        right_site = self.post("signin/verify/", host=OTHER_HOST, code=code)
        self.assertEqual(right_site.json()["member"]["full_name"], "Ben Cruz")

    def test_the_sign_in_of_one_site_leaves_the_other_sites_members_alone(self):
        self.sign_in()
        self.ben.refresh_from_db()
        self.assertEqual(self.ben.email_state, "pending")
        self.assertEqual(MemberSession.objects.filter(site=self.other_site).count(), 0)

    def test_every_code_row_names_its_site_and_the_limits_count_per_site(self):
        self.request_code("ben@buyer.test", host=OTHER_HOST)
        self.assertEqual(MemberOtpChallenge.objects.get().site, self.other_site)
        self.assertEqual(MemberOtpChallenge.objects.filter(site=self.site).count(), 0)
        # the same typed contact on the other site is not limited by the first
        self.assertEqual(self.request_code("ben@buyer.test").status_code, 200)

    def test_a_contact_change_revokes_only_that_members_sessions(self):
        Member.objects.filter(pk=self.member.pk).update(email_state="verified")
        self.member.refresh_from_db()
        mine_row, mine = sessions.create_session(self.site, self.member)
        _, theirs = sessions.create_session(self.other_site, self.ben)
        contact_change.current_proof_start(self.site, self.member, session_raw=mine, field="email")
        import re
        from django.core import mail

        proof = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        contact_change.current_proof_confirm(self.site, self.member, session_raw=mine, field="email", code=proof)
        contact_change.replacement_start(self.site, self.member, session_raw=mine, field="email", raw_value="m2@buyer.test")
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        contact_change.replacement_confirm(
            self.site, self.member, session=mine_row, session_raw=mine, field="email", code=code
        )
        self.assertIsNone(sessions.find_session(self.site, mine))
        self.assertIsNotNone(sessions.find_session(self.other_site, theirs))
