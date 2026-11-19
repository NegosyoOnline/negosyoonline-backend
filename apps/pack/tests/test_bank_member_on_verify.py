"""Task z8uq9mgpzd (Coach Ed, 2026-11-19, "the creation of account must be after successful payment"): a bank
transfer order is placed with no Member. It keeps the buyer's contact; the member, benefits and registration
come when the Subscriber approves the proof. Until then the buyer works from the checkout token or the
emailed order link (the status page, the new proof)."""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.common.errors import ApiError
from apps.members import services as members
from apps.members.models import Member, MemberOtpChallenge
from apps.pack import constants as C, review
from apps.pack.models import PackOrder, PackProofSubmission, SanteRegistration
from apps.pack.tests.base import EMAIL, HOST, MOBILE, OTHER_HOST, PNG, VISITOR, PackTestCase
from apps.storefront.tests.base import SECRET

LINK = re.compile(r"https://\S+/funnel/membership-pack/access/([^/\s]+)/")


class BankCase(PackTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.token, self.body = self.placed_memberless()
        self.order = PackOrder.objects.get()
        self.owner = self.site.account
        mail.outbox.clear()

    def call(self, path, **fields):
        return self.client.post(
            "/storefront/v1/" + path, {"host": HOST, "visitor_ip": VISITOR, **fields},
            content_type="application/json", secure=True,
            HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def status(self, token=None):
        return self.call("checkout/payment-status/", token=token or self.token)

    def resubmit(self, token=None, **changes):
        fields = self.proof_fields(token or self.token, **changes)
        for name in ("submit_key",):
            fields.pop(name)
        data = {"host": HOST, "visitor_ip": VISITOR, **fields, "proof": SimpleUploadedFile("p.png", PNG, "image/png")}
        return self.client.post(
            "/storefront/v1/checkout/resubmit/", data, secure=True,
            HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def fresh(self):
        self.order.refresh_from_db()
        return self.order

    def approve(self):
        order = self.fresh()
        with self.captureOnCommitCallbacks(execute=True):
            review.approve(self.owner, self.site, order.pk, state_version=order.state_version)
        return self.fresh()

    def reject(self, code="amount_mismatch", text=""):
        order = self.fresh()
        with self.captureOnCommitCallbacks(execute=True):
            review.reject(self.owner, self.site, order.pk, state_version=order.state_version,
                          reason_code=code, other_text=text)
        return self.fresh()

    def link_in(self, message):
        return LINK.search(message.body).group(1)


class PlacementTests(BankCase):
    def test_a_bank_order_keeps_the_contact_and_creates_nothing_else(self):
        order = self.fresh()
        self.assertIsNone(order.member_id)
        self.assertEqual((order.buyer_mobile, order.buyer_email), ("+639171234567", EMAIL))
        self.assertEqual(order.payment_status, C.PENDING_REVIEW)
        self.assertIsNone(order.benefits_active_at)
        self.assertEqual(Member.objects.count(), 0)
        self.assertEqual(SanteRegistration.objects.count(), 0)

    def test_the_answer_names_the_method_and_the_status(self):
        self.assertEqual((self.body["payment_method"], self.body["payment_status"]), ("direct", "under_review"))
        self.assertIsNone(self.body["pay_url"])

    def test_the_contact_stays_taken_while_the_proof_is_under_review(self):
        response = self.contact()
        error = response.json()["error"]
        self.assertEqual((response.status_code, error["code"]), (409, "order_on_file"))
        self.assertEqual(error["message"], C.ORDER_ON_FILE["mobile"])
        self.assertEqual(error["fields"], {"mobile": [C.ORDER_ON_FILE["mobile"]]})
        check = self.post("check/", mobile=MOBILE, email=EMAIL).json()
        self.assertIn("mobile", check["errors"])

    def test_a_rejected_order_also_gives_order_on_file(self):
        self.reject()
        error = self.contact().json()["error"]
        self.assertEqual(error["code"], "order_on_file")
        self.assertEqual(error["message"], C.ORDER_ON_FILE["mobile"])

    def test_the_email_alone_gives_the_email_message(self):
        error = self.contact(mobile="09179990000").json()["error"]
        self.assertEqual((error["code"], error["message"]), ("order_on_file", C.ORDER_ON_FILE["email"]))
        self.assertEqual(error["fields"], {"email": [C.ORDER_ON_FILE["email"]]})

    def test_check_names_the_new_message_per_field(self):
        self.assertEqual(self.post("check/", mobile=MOBILE).json()["errors"], {"mobile": [C.ORDER_ON_FILE["mobile"]]})
        self.assertEqual(self.post("check/", email=EMAIL).json()["errors"], {"email": [C.ORDER_ON_FILE["email"]]})
        both = self.post("check/", mobile=MOBILE, email=EMAIL).json()["errors"]
        self.assertEqual(both, {"mobile": [C.ORDER_ON_FILE["mobile"]], "email": [C.ORDER_ON_FILE["email"]]})
        self.assertEqual(self.post("check/", mobile="09179990000", email="new@buyer.test").json()["errors"], {})

    def test_a_real_member_keeps_contact_in_use_and_its_old_text(self):
        self.approve()
        error = self.contact().json()["error"]
        self.assertEqual((error["code"], error["message"]), ("contact_in_use", "This mobile number is already registered."))
        errors = self.post("check/", email=EMAIL).json()["errors"]
        self.assertEqual(errors, {"email": ["This email is already registered."]})

    def test_a_member_of_another_store_keeps_contact_in_use(self):
        Member.objects.create(site=self.other_site, full_name="Ben", mobile="+639170000002", email="ben@buyer.test")
        error = self.contact(mobile="09170000002", email="fresh@buyer.test").json()["error"]
        self.assertEqual((error["code"], error["message"]), ("contact_in_use", "This mobile number is already registered."))

    def test_an_unpaid_xendit_order_holds_nothing(self):
        PackOrder.objects.filter(pk=self.order.pk).update(
            payment_method=C.XENDIT, payment_status=C.AWAITING_XENDIT, xendit_external_id="np-1-" + "a" * 24,
        )
        self.assertEqual(self.contact().status_code, 200)
        self.assertEqual(self.post("check/", mobile=MOBILE, email=EMAIL).json()["errors"], {})

    def test_a_bank_order_of_one_store_holds_nothing_in_another(self):
        response = self.post("contact/", host=OTHER_HOST, **self.contact_fields())
        error = (response.json() or {}).get("error", {})
        self.assertNotIn(error.get("code"), ("order_on_file", "contact_in_use"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.post("check/", host=OTHER_HOST, mobile=MOBILE, email=EMAIL).json()["errors"], {})


class ApproveTests(BankCase):
    def test_approve_makes_the_member_turns_benefits_on_and_activates(self):
        order = self.approve()
        member = order.member
        self.assertIsNotNone(member)
        self.assertEqual((member.mobile, member.email), ("+639171234567", EMAIL))
        self.assertEqual((order.buyer_mobile, order.buyer_email), ("", ""))
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertIsNotNone(order.benefits_active_at)
        self.assertEqual(SanteRegistration.objects.get().member_id, member.pk)

    def test_the_verified_email_carries_the_continue_line_and_the_access_link(self):
        self.approve()
        message = [m for m in mail.outbox if m.subject.startswith("Your payment for order")][0]
        self.assertEqual(message.to, [EMAIL])
        self.assertIn("Continue your registration: https://", message.body)
        token = self.link_in(message)
        read = self.call("access/read/", link=token).json()
        self.assertEqual((read["kind"], read["link_open"]), ("member", True))
        redeemed = self.call("access/redeem/", link=token).json()
        self.assertTrue(redeemed["redeemed"])
        self.assertEqual(self.call("onboarding/state/", token=redeemed["token"]).status_code, 200)

    def test_a_contact_taken_meanwhile_refuses_the_approval(self):
        before = self.fresh().state_version
        with mock.patch("apps.pack.buyer.checkout.match_existing", side_effect=members.contact_in_use("email")):
            with self.assertRaises(ApiError) as caught:
                self.approve()
        self.assertEqual((caught.exception.code, caught.exception.status), ("contact_conflict", 409))
        self.assertIn("email", caught.exception.message)
        order = self.fresh()
        self.assertEqual((order.payment_status, order.state_version, order.member_id), (C.PENDING_REVIEW, before, None))
        self.assertEqual((Member.objects.count(), SanteRegistration.objects.count()), (0, 0))
        self.assertEqual(order.decisions.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_real_member_with_the_email_refuses_the_approval(self):
        # Someone else took the email after the checkout: the held-by-pending rule no longer protects it
        other = Member.objects.create(site=self.site, full_name="Other", mobile="+639170000001", email=EMAIL)
        PackOrder.objects.create(
            site=self.site, member=other, order_number="SMP90001", price_centavos=C.PRICE_CENTAVOS,
            buyer_name="Other", payment_status=C.VERIFIED, idempotency_key="other-1",
        )
        with self.assertRaises(ApiError) as caught:
            self.approve()
        self.assertEqual(caught.exception.code, "contact_conflict")
        self.assertIsNone(self.fresh().member_id)

    def test_a_legacy_bank_order_with_a_member_is_unchanged(self):
        self.give_member(self.order.order_number)
        legacy = self.fresh()
        member = legacy.member
        approved = self.approve()
        self.assertEqual((approved.member_id, approved.payment_status), (member.pk, C.VERIFIED))
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(SanteRegistration.objects.count(), 1)


class StatusPageTests(BankCase):
    def test_under_review_has_no_lock_and_no_resubmit(self):
        answer = self.status().json()
        self.assertEqual(
            (answer["payment_status"], answer["payment_method"], answer["payment_label"], answer["lock"], answer["can_resubmit"]),
            ("under_review", "direct", "Under review", None, False),
        )

    def test_a_rejected_order_shows_the_reason_and_the_accounts_to_the_token_holder(self):
        self.reject("amount_mismatch")
        answer = self.status().json()
        self.assertEqual((answer["payment_status"], answer["can_resubmit"]), ("needs_attention", True))
        self.assertEqual(answer["lock"]["reason_label"], C.REASON_LABELS["amount_mismatch"])
        self.assertFalse(answer["lock"]["resubmitted"])
        self.assertTrue(answer["lock"]["accounts"])

    def test_after_approval_the_status_is_paid_and_onboarding_opens_with_the_same_token(self):
        self.assertEqual(self.call("onboarding/state/", token=self.token).json()["error"]["code"], "payment_pending")
        self.approve()
        self.assertEqual(self.status().json()["payment_status"], "paid")
        self.assertEqual(self.call("onboarding/state/", token=self.token).status_code, 200)

    def test_onboarding_answers_payment_pending_while_under_review_and_rejected(self):
        for path in ("onboarding/state/", "onboarding/mobile/send/", "onboarding/completed/"):
            response = self.call(path, token=self.token)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "payment_pending"), path)
        self.reject()
        self.assertEqual(self.call("onboarding/state/", token=self.token).json()["error"]["code"], "payment_pending")

    def test_a_bad_token_is_checkout_expired(self):
        response = self.status("nope")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "checkout_expired"))


class ResubmitTests(BankCase):
    def test_reject_then_token_resubmit_then_approve(self):
        self.reject()
        response = self.resubmit(reference_number="REF999")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["payment_status"], body["lock"]["resubmitted"], body["can_resubmit"]), ("under_review", True, False))
        self.assertEqual(self.order.submissions.count(), 2)
        self.assertEqual(PackProofSubmission.objects.get(seq=2).reference_number, "REF999")
        order = self.approve()
        self.assertEqual((order.payment_status, order.rejection_lock_active), (C.VERIFIED, False))
        self.assertIsNotNone(order.member_id)

    def test_only_from_rejected(self):
        response = self.resubmit()
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_rejected"))
        self.assertEqual(self.order.submissions.count(), 1)

    def test_a_bad_proof_is_a_field_error_and_changes_nothing(self):
        self.reject()
        response = self.resubmit(reference_number="")
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(self.fresh().payment_status, C.REJECTED)

    def test_a_bad_token_is_checkout_expired(self):
        response = self.resubmit(token="nope")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "checkout_expired"))

    def test_ten_an_hour_per_order(self):
        codes = [self.resubmit().status_code for _ in range(11)]
        self.assertEqual(codes[:10], [409] * 10)
        self.assertEqual(codes[10], 429)

    def test_the_legacy_member_route_still_works_for_a_member_order(self):
        self.give_member(self.order.order_number)
        self.reject()
        member = self.fresh().member
        review.resubmit(self.site, member, data=self.proof_fields("x"), upload=SimpleUploadedFile("p.png", PNG, "image/png"))
        self.assertEqual(self.fresh().payment_status, C.PENDING_REVIEW)


class OrderLinkTests(PackTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.place(self.token).status_code, 200)
        self.order = PackOrder.objects.get()
        self.owner = self.site.account
        self.received = [m for m in mail.outbox if m.subject.startswith("We received your payment proof")][0]

    call = BankCase.call
    status = BankCase.status
    resubmit = BankCase.resubmit
    fresh = BankCase.fresh
    approve = BankCase.approve
    reject = BankCase.reject
    link_in = BankCase.link_in

    def test_the_received_email_goes_to_the_buyer_email_with_the_order_link(self):
        self.assertEqual(self.received.to, [EMAIL])
        self.assertIn("follow your order here: https://", self.received.body)
        self.assertNotIn("dashboard", self.received.body)
        self.assertIn("(This secure link works once and expires in 7 days.)", self.received.body)

    def test_the_link_opens_the_status_page_once_and_answers_a_fresh_token(self):
        link = self.link_in(self.received)
        read = self.call("access/read/", link=link).json()
        self.assertEqual(
            (read["link_open"], read["renewable"], read["kind"], read["payment_status"], read["payment_method"]),
            (True, True, "order", "under_review", "direct"),
        )
        redeemed = self.call("access/redeem/", link=link).json()
        self.assertEqual((redeemed["redeemed"], redeemed["kind"], redeemed["payment_status"]), (True, "order", "under_review"))
        status = self.call("checkout/payment-status/", token=redeemed["token"]).json()
        self.assertEqual((status["payment_status"], status["order_number"]), ("under_review", self.order.order_number))
        again = self.call("access/redeem/", link=link).json()
        self.assertEqual((again["redeemed"], again["token"], again["kind"]), (False, "", "order"))
        self.assertIsNone(self.fresh().member_id)  # opening the link makes no member

    def test_the_rejection_email_link_leads_to_the_reason_and_the_resubmit(self):
        mail.outbox.clear()
        self.reject("wrong_account")
        message = mail.outbox[0]
        self.assertEqual(message.to, [EMAIL])
        self.assertIn("Open this link to see the reason and send your payment details and proof again: https://", message.body)
        redeemed = self.call("access/redeem/", link=self.link_in(message)).json()
        self.assertEqual((redeemed["redeemed"], redeemed["kind"], redeemed["payment_status"]), (True, "order", "needs_attention"))
        status = self.call("checkout/payment-status/", token=redeemed["token"]).json()
        self.assertEqual(status["lock"]["reason_label"], C.REASON_LABELS["wrong_account"])
        again = self.resubmit(token=redeemed["token"])
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(self.fresh().payment_status, C.PENDING_REVIEW)

    def test_renew_sends_a_new_link_to_the_buyer_email_and_replaces_the_old_one(self):
        old = self.link_in(self.received)
        mail.outbox.clear()
        renewed = self.call("access/renew/", link=old).json()
        self.assertTrue(renewed["sent"])
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertFalse(self.call("access/read/", link=old).json()["link_open"])
        self.assertTrue(self.call("access/read/", link=self.link_in(mail.outbox[0])).json()["link_open"])

    def test_once_approved_the_order_link_is_closed_and_says_paid(self):
        link = self.link_in(self.received)
        self.approve()
        read = self.call("access/read/", link=link).json()
        self.assertEqual((read["link_open"], read["renewable"], read["payment_status"]), (False, False, "paid"))
        self.assertFalse(self.call("access/redeem/", link=link).json()["redeemed"])

    def test_the_order_email_link_works_for_the_under_review_state(self):
        # "Use the link in your order email": the received email carries it and it opens the status page
        status = self.call("checkout/payment-status/", token=self.call("access/redeem/", link=self.link_in(self.received)).json()["token"]).json()
        self.assertEqual((status["payment_status"], status["can_resubmit"]), ("under_review", False))

    def test_the_order_email_link_works_for_the_rejected_state(self):
        mail.outbox.clear()
        self.reject("wrong_account")
        message = mail.outbox[0]
        self.assertEqual(message.to, [EMAIL])
        read = self.call("access/read/", link=self.link_in(message)).json()
        self.assertEqual((read["link_open"], read["kind"], read["payment_status"]), (True, "order", "needs_attention"))
        redeemed = self.call("access/redeem/", link=self.link_in(message)).json()
        status = self.call("checkout/payment-status/", token=redeemed["token"]).json()
        self.assertEqual((status["payment_status"], status["can_resubmit"]), ("needs_attention", True))

    def test_a_used_order_link_can_get_a_new_one_by_email(self):
        link = self.link_in(self.received)
        self.assertTrue(self.call("access/redeem/", link=link).json()["redeemed"])
        mail.outbox.clear()
        renewed = self.call("access/renew/", link=link).json()
        self.assertEqual((renewed["sent"], renewed["renewable"]), (True, True))
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertTrue(self.call("access/read/", link=self.link_in(mail.outbox[0])).json()["link_open"])

    def test_an_expired_order_link_can_get_a_new_one_by_email(self):
        link = self.link_in(self.received)
        MemberOtpChallenge.objects.filter(member__isnull=True, channel="email").update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        read = self.call("access/read/", link=link).json()
        self.assertEqual((read["link_open"], read["renewable"]), (False, True))
        mail.outbox.clear()
        renewed = self.call("access/renew/", link=link).json()
        self.assertTrue(renewed["sent"])
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertTrue(self.call("access/read/", link=self.link_in(mail.outbox[0])).json()["link_open"])

    def test_a_link_of_another_site_or_a_forged_one_is_404(self):
        link = self.link_in(self.received)
        self.assertEqual(self.call("access/read/", link=link + "x").status_code, 404)
        other = self.client.post(
            "/storefront/v1/access/read/", {"host": "bens-store.negosyotest.com", "link": link},
            content_type="application/json", secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )
        self.assertEqual(other.status_code, 404)


class ReadersTests(BankCase):
    def test_the_owner_row_the_journey_and_the_prospects_handle_no_member(self):
        from apps.pack import api_owner, journey, prospects
        row = api_owner.row(self.order)
        self.assertIn("4567", row["mobile_masked"])
        self.assertFalse(journey.recent_orders(self.site, None)[0]["dashboard_access"])
        rows = [r for r in prospects.order_rows(self.site) if r["kind"] == "order"]
        self.assertEqual((rows[0]["mobile"], rows[0]["email"]), ("+639171234567", EMAIL))
