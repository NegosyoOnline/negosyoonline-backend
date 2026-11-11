"""The member Account routes: the page's data, email verification, the contact change, and the
no-session, wrong-host and sign-out-after-change rules."""
from django.core import mail
from django.core.cache import cache

from apps.members import sessions
from apps.members.models import Member
from apps.members.tests.base import NOT_FOUND, NOT_SIGNED_IN, OTHER_HOST, MembersTestCase


class AccountApiTests(MembersTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        Member.objects.filter(pk=self.member.pk).update(mobile_state="verified")
        _, self.raw = sessions.create_session(self.site, self.member)

    def call(self, path, **fields):
        return self.post(path, token=fields.pop("token", self.raw), **fields)

    def test_account_shows_masked_contacts_and_states(self):
        body = self.call("account/").json()
        self.assertEqual((body["email_state"], body["mobile_state"]), ("pending", "verified"))
        self.assertNotIn("maria@buyer.test", str(body))
        self.assertNotIn("09171234567", str(body))
        self.assertEqual(body["member_name"], "Maria Santos")

    def test_no_session_a_bad_token_and_another_sites_host(self):
        for path in ("account/", "account/verify-email/", "account/change/email/"):
            self.assertEqual(self.call(path, token="").json(), NOT_SIGNED_IN, path)
        self.assertEqual(self.call("account/", host=OTHER_HOST).json(), NOT_SIGNED_IN)
        self.assertEqual(self.call("account/", host="nowhere.example.test").json(), NOT_FOUND)

    def test_verify_email_send_then_verify(self):
        self.assertEqual(self.call("account/verify-email/").json()["step"], "send")
        mail.outbox.clear()
        self.assertEqual(self.call("account/verify-email/send/").json()["step"], "code")
        bad = self.call("account/verify-email/verify/", code="000000")
        self.assertEqual(bad.status_code, 400)
        done = self.call("account/verify-email/verify/", code=self.last_code()).json()
        self.assertEqual((done["step"], done["message"]), ("done", "Your email is verified."))
        self.assertEqual(Member.objects.get().email_state, "verified")
        again = self.call("account/verify-email/send/")
        self.assertEqual(again.json()["error"]["code"], "already_verified")

    def test_change_email_through_all_four_steps_then_every_session_is_gone(self):
        Member.objects.filter(pk=self.member.pk).update(email_state="verified")
        state = self.call("account/change/email/").json()
        self.assertEqual((state["step"], state["proof_channel"]), ("current", "email"))
        refused = self.call("account/change/email/act/", action="send_replacement", new_value="new@buyer.test")
        self.assertEqual(refused.json()["error"]["code"], "current_proof_required")
        mail.outbox.clear()
        self.assertEqual(self.call("account/change/email/act/", action="send_current").json()["state"]["step"], "current_code")
        self.call("account/change/email/act/", action="verify_current", code=self.last_code())
        self.age_challenges()
        mail.outbox.clear()
        self.call("account/change/email/act/", action="send_replacement", new_value="new@buyer.test")
        self.assertEqual(mail.outbox[-1].to, ["new@buyer.test"])
        done = self.call("account/change/email/act/", action="verify_replacement", code=self.last_code()).json()
        self.assertEqual(done, {"state": None, "changed": True})
        self.assertEqual(Member.objects.get().email, "new@buyer.test")
        self.assertEqual(self.call("account/").json(), NOT_SIGNED_IN)

    def test_an_unknown_field_or_action_is_404(self):
        self.assertEqual(self.call("account/change/name/").status_code, 404)
        self.assertEqual(self.call("account/change/email/act/", action="nope").status_code, 404)
