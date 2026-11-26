"""Placing a pack order: the transfer rules, the proof file, idempotency, records and mail."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.billing.models import Subscription
from apps.common import private_files
from apps.pack import placement
from apps.members.models import Member
from apps.pack import constants as C
from apps.pack.models import (
    CheckoutToken, PackAgreementAcceptance, PackEnrollmentAuthorization, PackOrder,
    PackOrderEvent, PackProofSubmission,
)
from apps.pack.tests.base import (
    EMAIL, MOBILE, NOT_FOUND, OTHER_HOST, PDF, PNG, PackTestCase,
)


class PlaceTests(PackTestCase):
    def test_a_good_order_is_placed_pending_review_with_benefits_off(self):
        token, body = self.placed()
        self.assertEqual(body, {"order_number": "SMP00001", "replay": False, "pay_url": None})
        order = PackOrder.objects.get()
        self.assertEqual(order.payment_status, C.PENDING_REVIEW)
        self.assertEqual(order.price_centavos, C.PRICE_CENTAVOS)
        self.assertIsNone(order.benefits_active_at)
        self.assertEqual(order.buyer_name, "Maria Santos")
        self.assertTrue(order.public_id.startswith("pko_"))
        member = Member.objects.get()
        self.assertEqual((member.mobile_state, member.email_state), ("pending", "unverified"))
        self.assertEqual(order.member, member)

    def test_the_proof_snapshot_and_file_are_kept(self):
        self.placed()
        sub = PackProofSubmission.objects.get()
        self.assertEqual((sub.seq, sub.amount_sent_centavos, sub.sender_name), (1, 148800, "Maria Santos"))
        self.assertEqual(sub.account_number_last4, self.account.account_number[-4:])
        self.assertTrue(sub.public_id.startswith("pps_"))
        self.assertEqual(self.proof_files(), [sub.file_key])
        self.assertTrue(sub.file_key.startswith(f"sante/proofs/site-{self.site.pk}/"))

    def test_agreement_and_enrollment_records_are_written(self):
        self.placed()
        acc = PackAgreementAcceptance.objects.get()
        self.assertEqual((acc.version_id, acc.sha256), (self.version.pk, self.version.sha256))
        self.assertEqual(PackEnrollmentAuthorization.objects.get().text, C.ENROLLMENT_AUTHORIZATION)
        event = PackOrderEvent.objects.get()
        self.assertEqual(event.kind, C.EVENT_PLACED)

    def test_placing_without_the_agreement_writes_no_acceptance_but_the_enrollment(self):
        response = self.post("contact/", **self.contact_fields_without_agreement())
        with self.captureOnCommitCallbacks(execute=True):
            placed = self.place(response.json()["token"])
        self.assertEqual(placed.status_code, 200, placed.content)
        self.assertEqual(PackAgreementAcceptance.objects.count(), 0)
        self.assertEqual(PackEnrollmentAuthorization.objects.count(), 1)

    def test_the_token_is_spent_and_its_contact_cleared(self):
        token, _ = self.placed()
        row = self.token_row(token)
        self.assertEqual(row.order_ref, PackOrder.objects.get().pk)
        self.assertEqual(row.pending_contact, {})
        again = self.place(token, submit_key="key-2")
        self.assertEqual(again.status_code, 409)

    def test_order_numbers_count_up_per_site(self):
        self.placed()
        token = self.start(email="b@buyer.test", mobile="09170000002")
        self.assertEqual(self.place(token, submit_key="k2").json()["order_number"], "SMP00002")

    def _place_more(self, count):
        for i in range(count):
            token = self.start(email=f"gap{i}@buyer.test", mobile=f"0917000{i:04d}")
            self.assertEqual(self.place(token, submit_key=f"gap-{i}").status_code, 200)

    def _next_after_numbers(self, numbers):
        """Place len(numbers) orders, renumber them, then place one more; return its number."""
        self._place_more(len(numbers))
        for row, number in zip(PackOrder.objects.filter(site=self.site).order_by("pk"), numbers):
            PackOrder.objects.filter(pk=row.pk).update(order_number=number)
        token = self.start(email="next@buyer.test", mobile="09170009999")
        return self.place(token, submit_key="gap-next").json()["order_number"]

    def test_a_gap_in_the_numbering_never_collides(self):
        self.assertEqual(self._next_after_numbers(["SMP00001", "SMP00002", "SMP00004"]), "SMP00005")

    def test_an_empty_site_places_number_one(self):
        self.assertEqual(self._next_after_numbers([]), "SMP00001")

    def test_another_sites_numbers_do_not_affect_this_site(self):
        self._place_more(1)
        PackOrder.objects.filter(site=self.site).update(site=self.other_site, order_number="SMP00099")
        token = self.start(email="next@buyer.test", mobile="09170009999")
        self.assertEqual(self.place(token, submit_key="gap-next").json()["order_number"], "SMP00001")

    def test_numbers_outside_the_pattern_are_ignored(self):
        self.assertEqual(self._next_after_numbers(["SMP00001", "SMP00007", "LEGACY-1"]), "SMP00008")

    def test_a_replay_returns_the_first_order_and_writes_nothing(self):
        token, first = self.placed()
        files = self.proof_files()
        replay = self.place(token)
        self.assertEqual(replay.json(), {"order_number": first["order_number"], "replay": True, "pay_url": None})
        self.assertEqual(PackOrder.objects.count(), 1)
        self.assertEqual(self.proof_files(), files)
        self.assertEqual(len(mail.outbox), 2)

    def test_no_contact_no_expired_and_unknown_tokens_are_refused(self):
        self.assertEqual(self.place("unknown-token").json()["error"]["message"], C.MSG_CHECKOUT_EXPIRED)
        raw = self.start()
        CheckoutToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.place(raw).status_code, 409)

    def test_a_missing_submit_key_is_refused(self):
        raw = self.start()
        self.assertEqual(self.place(raw, submit_key="").status_code, 409)

    def test_isolation_and_credential(self):
        raw = self.start()
        for host in ("nobody.negosyotest.com", OTHER_HOST):
            response = self.place(raw, host=host)
            self.assertIn(response.status_code, (404, 409), host)
        self.assertEqual(self.place(raw, secret=None).status_code, 401)
        self.assertEqual(PackOrder.objects.count(), 0)
        with override_settings(PACK_ENABLED=False):
            response = self.place(raw)
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))


class LapseDuringPlacementTests(PackTestCase):
    """A request that passed the storefront check and then lost service before the lock commits."""

    def lapse_after_the_first_check(self, how):
        real = placement._store

        def store_then_lapse(site, proof):
            key = real(site, proof)  # the stored file is what a refused order must discard
            how()
            return key

        return mock.patch.object(placement, "_store", side_effect=store_then_lapse)

    def assert_nothing_created(self, response):
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertEqual(PackOrder.objects.count(), 0)
        self.assertEqual(PackProofSubmission.objects.count(), 0)
        self.assertEqual(Member.objects.count(), 0)
        self.assertEqual(self.proof_files(), [])
        self.assertEqual(CheckoutToken.objects.get().order_ref, None)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_suspension_after_the_first_check_creates_no_order(self):
        raw = self.start()
        with self.lapse_after_the_first_check(
            lambda: Subscription.objects.filter(site=self.site).update(state=Subscription.State.SUSPENDED)
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.place(raw)
        self.assert_nothing_created(response)

    def test_a_cancellation_that_has_ended_after_the_first_check_creates_no_order(self):
        raw = self.start()
        ended = self.clock.now() - timedelta(seconds=1)  # the billing clock the storefront reads
        with self.lapse_after_the_first_check(
            lambda: Subscription.objects.filter(site=self.site).update(
                state=Subscription.State.CANCELLED, service_ended_at=ended
            )
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.place(raw)
        self.assert_nothing_created(response)

    def test_an_operator_hold_after_the_first_check_creates_no_order(self):
        raw = self.start()
        with self.lapse_after_the_first_check(
            lambda: type(self.site).objects.filter(pk=self.site.pk).update(operator_hold=True)
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.place(raw)
        self.assert_nothing_created(response)

    def test_the_same_request_without_a_lapse_is_placed(self):
        raw = self.start()
        with self.lapse_after_the_first_check(lambda: None), self.captureOnCommitCallbacks(execute=True):
            response = self.place(raw)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(PackOrder.objects.count(), 1)


class TransferRuleTests(PackTestCase):
    def refused(self, **fields):
        raw = self.start()
        response = self.place(raw, **fields)
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(PackOrder.objects.count(), 0)
        self.assertEqual(self.proof_files(), [])
        return response.json()["error"]["fields"]

    def test_each_required_field(self):
        for name in ("account_id", "reference_number", "amount_sent", "sender_bank",
                     "sender_account_number", "transferred_at"):
            self.assertIn(name, self.refused(**{name: ""}), name)

    def test_amount_rules(self):
        for bad in ("0", "-5", "100000", "12.345", "abc"):
            self.assertIn("amount_sent", self.refused(amount_sent=bad), bad)

    def test_an_amount_other_than_1488_is_accepted(self):
        self.assertEqual(self.place(self.start(), amount_sent="1,000.50").status_code, 200)
        self.assertEqual(PackProofSubmission.objects.get().amount_sent_centavos, 100050)

    def test_transfer_time_cannot_be_in_the_future_or_a_date_alone(self):
        future = (timezone.localtime() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        self.assertEqual(self.refused(transferred_at=future)["transferred_at"], ["The time of transfer cannot be in the future."])
        self.assertIn("transferred_at", self.refused(transferred_at="2026-11-01"))

    def test_sender_name_split_rules(self):
        self.assertIn("sender_last_name", self.refused(sender_last_name=""))
        self.assertIn("sender_first_name", self.refused(sender_first_name="x" * 60))

    def test_the_single_sender_name_stays_accepted(self):
        raw = self.start()
        response = self.place(raw, sender_first_name=None, sender_last_name=None, sender_name="Maria S")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(PackProofSubmission.objects.get().sender_name, "Maria S")

    def test_an_account_of_another_site_or_removed_is_refused(self):
        from apps.payments.tests.base import make_payment_account
        theirs = make_payment_account(self.other_site)
        self.assertIn("account_id", self.refused(account_id=theirs.public_id))

    def test_payment_switched_off_is_method_unavailable(self):
        from apps.payments.models import SitePaymentSettings
        SitePaymentSettings.objects.create(site=self.site, direct_enabled=False)
        raw = self.start()
        response = self.place(raw)
        self.assertEqual((response.status_code, response.json()["error"]["message"]),
                         (409, C.MSG_PAYMENT_UNAVAILABLE))


class FileTests(PackTestCase):
    def test_pdf_is_accepted(self):
        self.assertEqual(self.place(self.start(), content=PDF, declared="application/pdf").status_code, 200)

    def test_a_missing_file_is_refused(self):
        response = self.place(self.start(), content=None)
        self.assertEqual(response.json()["error"]["fields"]["proof"], ["Attach the file."])

    def test_the_type_is_judged_by_the_bytes(self):
        response = self.place(self.start(), content=b"MZ" + b"0" * 50, name="x.png")
        self.assertEqual(response.status_code, 422)
        response = self.place(self.start(email="c@buyer.test", mobile="09170000003"), content=PNG,
                              declared="text/plain")
        self.assertEqual(response.status_code, 422)

    def test_the_cap_is_4_mb(self):
        big = PNG + b"0" * C.PROOF_MAX_BYTES
        response = self.place(self.start(), content=big)
        self.assertEqual(response.json()["error"]["fields"]["proof"], ["The file is too large. The limit is 4 MB."])
        exact = PNG + b"0" * (C.PROOF_MAX_BYTES - len(PNG))
        self.assertEqual(self.place(self.start(email="d@buyer.test", mobile="09170000004"), content=exact).status_code, 200)

    def test_a_rollback_discards_the_stored_file(self):
        raw = self.start()
        with mock.patch("apps.pack.placement.proofs.write", side_effect=RuntimeError("boom")):
            self.assertEqual(self.place(raw).status_code, 500)
        self.assertEqual(self.proof_files(), [])
        self.assertEqual(PackOrder.objects.count(), 0)

    def test_a_store_that_cannot_take_the_file_refuses_with_503(self):
        raw = self.start()
        with mock.patch.object(private_files, "save", side_effect=private_files.StoreError):
            self.assertEqual(self.place(raw).status_code, 503)
        self.assertEqual(PackOrder.objects.count(), 0)


class AgreementCheckoutTests(PackTestCase):
    def test_a_new_active_version_after_the_tick_is_changed_and_stores_nothing(self):
        from apps.sante import agreement
        from apps.sante.tests.agreement_helpers import make_operator, published
        raw = self.start()
        published(make_operator("op2"), label="v2")
        response = self.place(raw)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "agreement_changed"))
        self.assertEqual(self.proof_files(), [])
        self.assertEqual(PackOrder.objects.count(), 0)
        again = self.post("contact/", token=raw, **self.contact_fields(agreement_version_id=str(agreement.active_version().pk)))
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(self.place(raw).status_code, 200)
        self.assertEqual(PackAgreementAcceptance.objects.get().version.version_label, "v2")


class ReturningBuyerTests(PackTestCase):
    def test_a_returning_buyer_must_prove_the_number_first(self):
        from apps.members import services
        member = services.create_member(self.site, full_name="Maria Santos", mobile=MOBILE,
                                        email=EMAIL, email_state="pending")
        Member.objects.filter(pk=member.pk).update(mobile_state="verified")  # no longer abandoned
        raw = self.start()
        response = self.place(raw)
        self.assertEqual((response.status_code, response.json()["error"]["message"]), (409, C.MSG_VERIFY_FIRST))
        self.assertEqual(self.proof_files(), [])
        self.assertEqual(PackOrder.objects.count(), 0)


class MailTests(PackTestCase):
    def test_buyer_and_verified_owner_are_mailed_after_commit(self):
        from django.utils import timezone as tz
        self.site.account.__class__.objects.filter(pk=self.site.account_id).update(email_verified_at=tz.now())
        with self.captureOnCommitCallbacks(execute=True):
            self.placed()
        subjects = {m.to[0]: m.subject for m in mail.outbox}
        self.assertEqual(subjects[EMAIL], "We received your payment proof for order SMP00001")
        self.assertIn("New payment proof to review: SMP00001", subjects[self.site.account.email])
        self.assertIn("Maria S.", [m for m in mail.outbox if m.to[0] == self.site.account.email][0].body)

    def test_branded_switch_on_mails_the_buyer_html_and_leaves_the_owner_notice_plain(self):
        with override_settings(STORE_EMAILS_BRANDED=True):
            with self.captureOnCommitCallbacks(execute=True):
                self.placed()
        buyer = [m for m in mail.outbox if m.to[0] == EMAIL][0]
        owner = [m for m in mail.outbox if m.to[0] == self.site.account.email][0]
        self.assertEqual(buyer.alternatives[0][1], "text/html")
        self.assertEqual(getattr(owner, "alternatives", []), [])
        self.assertEqual(owner.from_email, "NegosyoOnline <no-reply@negosyoonline.com>")

    def test_an_unverified_owner_is_not_mailed(self):
        type(self.site.account).objects.filter(pk=self.site.account_id).update(email_verified_at=None)
        self.placed()
        self.assertEqual([m.to[0] for m in mail.outbox], [EMAIL])

    def test_a_failed_email_does_not_undo_the_order(self):
        with mock.patch("apps.pack.notices.send_store_email", side_effect=RuntimeError("smtp")):
            self.placed()
        self.assertEqual(PackOrder.objects.count(), 1)
