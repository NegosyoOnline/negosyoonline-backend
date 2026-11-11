"""The member's side of a rejected proof: the payment card with the reason (after sign-in only), the
member's own proof file, and the new proof (only from rejected, lock stays until approval)."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.members import sessions
from apps.pack import constants as C, review
from apps.pack.models import PackOrder, PackProofDecision, PackProofSubmission
from apps.pack.tests.base import HOST, NOT_FOUND, OTHER_HOST, PNG, VISITOR, PackTestCase
from apps.storefront.tests.base import SECRET


class MemberPackCase(PackTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.placed()
        self.order = PackOrder.objects.get()
        self.member = self.order.member
        _, self.session = sessions.create_session(self.site, self.member)
        self.owner = self.site.account

    def member_post(self, path, host=HOST, secret=SECRET, token=None, **fields):
        body = {"host": host, "token": self.session if token is None else token, **fields}
        return self.client.post(
            "/storefront/v1/member/pack/" + path, body, content_type="application/json", secure=True,
            HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {secret}",
        )

    def reject(self, code="amount_mismatch", text=""):
        with self.captureOnCommitCallbacks(execute=True):
            review.reject(self.owner, self.site, self.order.pk, state_version=self.order.state_version,
                          reason_code=code, other_text=text)
        self.order.refresh_from_db()

    def resubmit(self, token=None, **changes):
        fields = self.proof_fields(self.session if token is None else token, **changes)
        data = {"host": HOST, "visitor_ip": VISITOR, **fields,
                "proof": SimpleUploadedFile("p.png", PNG, content_type="image/png")}
        return self.client.post(
            "/storefront/v1/member/pack/resubmit/", data, secure=True,
            HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )


class ReadTests(MemberPackCase):
    def test_under_review_has_no_lock_and_one_proof(self):
        pack = self.member_post("").json()["pack"]
        self.assertEqual(pack["payment_status"], "pending_review")
        self.assertIsNone(pack["lock"])
        self.assertEqual(len(pack["proofs"]), 1)
        self.assertEqual(pack["price_centavos"], 148800)

    def test_a_rejected_member_sees_the_reason_and_the_accounts(self):
        self.reject("amount_mismatch")
        pack = self.member_post("").json()["pack"]
        self.assertEqual(pack["lock"]["reason_label"], C.REASON_LABELS["amount_mismatch"])
        self.assertFalse(pack["lock"]["resubmitted"])
        self.assertEqual([a["id"] for a in pack["lock"]["accounts"]], [self.account.public_id])

    def test_other_shows_the_written_text(self):
        self.reject("other", "Wrong month")
        self.assertEqual(self.member_post("").json()["pack"]["lock"]["other_text"], "Wrong month")

    def test_the_buyers_email_names_no_reason(self):
        self.reject("wrong_account")
        sent = mail.outbox[-1]
        self.assertNotIn(C.REASON_LABELS["wrong_account"], sent.subject + sent.body)

    def test_no_session_or_a_bad_token_is_401(self):
        for token in ("", "nope"):
            r = self.member_post("", token=token)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "member_session_invalid"))

    def test_an_unknown_host_is_404_and_another_sites_host_does_not_honour_the_token(self):
        r = self.member_post("", host="nowhere.example.test")
        self.assertEqual((r.status_code, r.json()), (404, NOT_FOUND))
        self.assertEqual(self.member_post("", host=OTHER_HOST).status_code, 401)


class ProofTests(MemberPackCase):
    def test_own_proof_opens_privately(self):
        sub = PackProofSubmission.objects.get()
        r = self.member_post("proof/", proof_id=sub.public_id)
        self.assertEqual((r.status_code, r.content), (200, PNG))
        self.assertIn("no-store", r["Cache-Control"])  # the surface sets it on every answer
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")

    def test_another_members_proof_is_404(self):
        from apps.pack.tests.owner_base import make_order
        _, other = make_order(self.site)
        r = self.member_post("proof/", proof_id=other.public_id)
        self.assertEqual((r.status_code, r.json()), (404, NOT_FOUND))


class ResubmitTests(MemberPackCase):
    def test_only_from_rejected(self):
        r = self.resubmit()
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "not_rejected"))
        self.assertEqual(PackProofSubmission.objects.count(), 1)

    def test_resubmit_adds_seq_2_keeps_the_lock_and_tells_the_owner(self):
        self.reject()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.resubmit(reference_number="NEW999")
        self.assertEqual(r.status_code, 200, r.content)
        self.order.refresh_from_db()
        self.assertEqual((self.order.payment_status, self.order.rejection_lock_active), (C.PENDING_REVIEW, True))
        self.assertEqual(list(PackProofSubmission.objects.values_list("seq", flat=True)), [1, 2])
        self.assertTrue(r.json()["pack"]["lock"]["resubmitted"])
        self.assertEqual(len(mail.outbox), 1)  # the Subscriber, if their email is verified

    def test_a_bad_proof_changes_nothing(self):
        self.reject()
        r = self.resubmit(reference_number="")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(PackProofSubmission.objects.count(), 1)
        self.assertEqual(len(self.proof_files()), 1)

    def test_approval_after_a_resubmission_clears_the_lock(self):
        self.reject()
        self.resubmit()
        self.order.refresh_from_db()
        review.approve(self.owner, self.site, self.order.pk, state_version=self.order.state_version)
        self.order.refresh_from_db()
        self.assertEqual((self.order.payment_status, self.order.rejection_lock_active), (C.VERIFIED, False))
        self.assertEqual(PackProofDecision.objects.filter(decision="approved").count(), 1)

    def test_a_bad_session_is_401(self):
        self.reject()
        self.assertEqual(self.resubmit(token="nope").status_code, 401)
