"""Hotfix z8uq9mgpt5 (Coach Ed, 2026-11-19): a Xendit checkout creates the Member only when the payment is
confirmed. A failed, expired or abandoned Xendit checkout holds no account and never blocks the next
checkout with the same mobile and email. Bank transfer follows the same rule since task z8uq9mgpzd (test_bank_member_on_verify)."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.utils import timezone

from apps.members import services as members
from apps.members.models import Member, MemberOtpChallenge
from apps.meta_ads import events as meta_events
from apps.pack import api_owner, constants as C, export, journey, notices, prospects, purge, retention
from apps.pack import checkout
from apps.pack.models import PackOrder, SanteRegistration
from apps.pack.tests.base import EMAIL, MOBILE, NAME
from apps.pack.tests.xendit_base import XenditPackCase


def mobile_form(mobile):
    return {"mobile": mobile}


class PlacementCreatesNoMember(XenditPackCase):
    def test_placing_a_xendit_order_creates_no_member_and_keeps_the_contact_on_the_order(self):
        _, _, order = self.placed_xendit()
        self.assertEqual(Member.objects.count(), 0)
        self.assertIsNone(order.member_id)
        self.assertEqual((order.buyer_mobile, order.buyer_email), ("+639171234567", EMAIL))
        self.assertEqual(order.buyer_name, NAME)
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)

    def test_the_status_read_and_try_again_work_with_no_member(self):
        token, _, order = self.placed_xendit()
        response = self.status(token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["payment_status"], "waiting")
        PackOrder.objects.filter(pk=order.pk).update(xendit_invoice_id="", xendit_invoice_url="")
        again = self.status(token, retry=True)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertTrue(again.json()["pay_url"])
        self.assertEqual(Member.objects.count(), 0)

    def test_onboarding_stays_closed_with_no_member(self):
        token, _, _ = self.placed_xendit()
        response = self.onboarding("onboarding/state/", token)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "payment_pending")

    def test_a_verified_mobile_is_kept_by_its_challenge_and_not_consumed_before_payment(self):
        token = self.start()
        challenge = self.verify_mobile(token)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place_xendit(token)
        self.assertEqual(response.status_code, 200, response.content)
        order = PackOrder.objects.get()
        self.assertEqual(order.onboarding_challenge_id, challenge.pk)
        challenge.refresh_from_db()
        self.assertIsNone(challenge.member_id)
        with self.captureOnCommitCallbacks(execute=True):
            self.webhook(order)
        challenge.refresh_from_db()
        member = Member.objects.get()
        self.assertEqual(challenge.member_id, member.pk)
        self.assertEqual(member.mobile_state, "verified")

    def verify_mobile(self, token):
        """A used onboarding challenge for the token's mobile, as the checkout's mobile check leaves it."""
        from apps.members.otp import binding_hash
        row = self.token_row(token)
        challenge = MemberOtpChallenge.objects.create(
            site=self.site, purpose="onboarding", channel="sms", target=row.requested_mobile,
            binding_hash=binding_hash(token), code_hash="x", expires_at=timezone.now() + timedelta(hours=1),
            used_at=timezone.now(),
        )
        type(row).objects.filter(pk=row.pk).update(onboarding_challenge_id=challenge.pk, verified_mobile=challenge.target)
        return challenge


class RetryAfterAFailedPayment(XenditPackCase):
    def test_after_a_failed_checkout_the_same_contacts_can_check_out_again(self):
        _, _, first = self.placed_xendit()
        self.webhook(first, status="EXPIRED")
        first.refresh_from_db()
        self.assertEqual(first.payment_status, C.XENDIT_EXPIRED)
        self.assertEqual(self.contact().status_code, 200)  # not "already registered"
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place_xendit(token, submit_key="key-2")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(PackOrder.objects.count(), 2)
        self.assertEqual(Member.objects.count(), 0)

    def test_an_unpaid_order_still_waiting_never_blocks_a_new_checkout(self):
        self.placed_xendit()
        self.assertEqual(self.contact().status_code, 200)
        self.assertEqual(self.contact(mobile="09171234567", email="other@buyer.test").status_code, 200)

    def test_a_mismatch_order_never_blocks_a_new_checkout(self):
        _, _, first = self.placed_xendit()
        self.webhook(first, paid_amount=1000, amount=1000)
        first.refresh_from_db()
        self.assertEqual(first.payment_status, C.XENDIT_MISMATCH)
        self.assertEqual(self.contact().status_code, 200)

    def test_the_contact_check_does_not_mark_the_contacts_of_an_unpaid_order(self):
        self.placed_xendit()
        self.assertEqual(checkout.check_contact(self.site, "+639171234567", EMAIL), {})


class PayCreatesTheMember(XenditPackCase):
    def test_paid_creates_the_member_attaches_it_and_runs_the_existing_steps(self):
        _, _, order = self.placed_xendit()
        self.assertEqual(Member.objects.count(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.webhook(order)
        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        member = Member.objects.get()
        self.assertEqual(order.member_id, member.pk)
        self.assertEqual((member.full_name, member.mobile, member.email), (NAME, "+639171234567", EMAIL))
        self.assertEqual(member.mobile_state, "pending")  # no mobile code was typed in this checkout
        self.assertEqual(member.source, "onboarding")
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertIsNotNone(order.benefits_active_at)
        self.assertEqual((order.buyer_mobile, order.buyer_email), ("", ""))  # the copy goes once the member holds it
        self.assertIsNone(order.onboarding_challenge_id)
        self.assertEqual(SanteRegistration.objects.get().member_id, member.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [EMAIL])

    def test_the_member_carries_the_checkout_address(self):
        _, _, order = self.placed_xendit()
        PackOrder.objects.filter(pk=order.pk).update(delivery_address={"line": "1 Rizal St", "city": "Manila", "province": "NCR"})
        self.webhook(order)
        self.assertEqual(Member.objects.get().delivery_address["city"], "Manila")

    def test_a_duplicate_paid_event_makes_no_second_member(self):
        _, _, order = self.placed_xendit()
        self.assertEqual(self.webhook(order, webhook_id="evt-1").status_code, 200)
        self.assertEqual(self.webhook(order, webhook_id="evt-1").status_code, 200)  # the same event
        self.assertEqual(self.webhook(order, webhook_id="evt-2").status_code, 200)  # another delivery of it
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(SanteRegistration.objects.count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertEqual(order.events.filter(kind="xendit_paid").count(), 1)

    def test_the_server_read_of_the_invoice_creates_the_member_too(self):
        token, _, order = self.placed_xendit()
        self.pay_in_fake(order)
        with mock.patch("apps.pack.xendit.cache.add", return_value=True):
            response = self.status(token)
        self.assertEqual(response.json()["payment_status"], "paid")
        self.assertEqual(Member.objects.count(), 1)

    def test_a_late_paid_event_on_an_expired_order_still_creates_the_member(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, status="EXPIRED")
        self.assertEqual(Member.objects.count(), 0)
        self.webhook(order)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertEqual(Member.objects.count(), 1)

    def test_a_wrong_amount_creates_no_member(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, paid_amount=1000, amount=1000)
        self.assertEqual(Member.objects.count(), 0)

    def test_after_payment_the_buyer_reaches_onboarding(self):
        token, _, order = self.placed_xendit()
        self.webhook(order)
        response = self.onboarding("onboarding/state/", token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["buyer_first_name"], "Maria")


class ContactTakenBeforePayment(XenditPackCase):
    def other_buyer(self, **kw):
        """A real member (a verified mobile): an unverified one with no order would count as abandoned."""
        member = members.create_member(
            self.site, full_name="Someone Else", mobile=kw.get("mobile", "09179990000"),
            email=kw.get("email", "other@buyer.test"),
        )
        Member.objects.filter(pk=member.pk).update(mobile_state="verified")
        return member

    def test_a_contact_taken_in_the_meantime_sends_the_paid_order_to_under_review(self):
        _, _, order = self.placed_xendit()
        self.other_buyer(email=EMAIL)  # someone else now holds this email
        with self.captureOnCommitCallbacks(execute=True):
            self.webhook(order)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_MISMATCH)
        self.assertIsNone(order.member_id)
        self.assertIsNone(order.benefits_active_at)
        self.assertEqual(Member.objects.count(), 1)  # nothing merged, nothing created
        self.assertFalse(SanteRegistration.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        event = order.events.get(kind="xendit_mismatch")
        self.assertEqual(event.new["reason"], "contact_in_use")
        self.assertEqual(event.new["field"], "email")
        self.assertEqual(dict(C.PAYMENT_STATUSES)[order.payment_status], "Under review")

    def test_a_mobile_taken_in_the_meantime_is_named_too(self):
        _, _, order = self.placed_xendit()
        self.other_buyer(mobile="+639171234567")
        self.webhook(order)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_MISMATCH)
        self.assertEqual(order.events.get(kind="xendit_mismatch").new["field"], "mobile")

    def test_two_unpaid_orders_with_the_same_contact_the_second_to_pay_goes_to_review(self):
        _, _, first = self.placed_xendit()
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            self.place_xendit(token, submit_key="key-2")
        second = PackOrder.objects.exclude(pk=first.pk).get()
        self.webhook(first)
        self.webhook(second)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.payment_status, C.VERIFIED)
        self.assertEqual(second.payment_status, C.XENDIT_MISMATCH)
        self.assertEqual(Member.objects.count(), 1)


class LegacyUnpaidHolders(XenditPackCase):
    def legacy_holder(self, status=C.AWAITING_XENDIT, **member):
        """What the old code left behind: a member created at placement, whose only order is unpaid Xendit."""
        n = PackOrder.objects.count() + 90001
        holder = members.create_member(self.site, full_name=NAME, mobile="09171234567", email=EMAIL)
        order = PackOrder.objects.create(
            site=self.site, member=holder, order_number=f"SMP{n}", price_centavos=C.PRICE_CENTAVOS,
            buyer_name=NAME, payment_method="xendit", payment_status=status, idempotency_key=f"legacy-{n}",
            xendit_external_id=f"np-{self.site.pk}-{n:024d}",
        )
        return holder, order

    def test_a_legacy_unpaid_holder_counts_as_abandoned(self):
        holder, _ = self.legacy_holder()
        self.assertTrue(checkout.abandoned(holder))

    def test_the_next_checkout_releases_the_legacy_holder_and_deletes_nothing(self):
        holder, legacy = self.legacy_holder()
        self.assertEqual(self.contact().status_code, 200)  # not "already registered"
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place_xendit(token, submit_key="key-2")
        self.assertEqual(response.status_code, 200, response.content)
        holder.refresh_from_db()
        self.assertTrue(members.contacts_released(holder))
        self.assertTrue(PackOrder.objects.filter(pk=legacy.pk).exists())
        self.assertEqual(Member.objects.count(), 1)  # the released row stays; the new buyer has no member yet
        order = PackOrder.objects.exclude(pk=legacy.pk).get()
        self.webhook(order)
        self.assertEqual(Member.objects.count(), 2)
        self.assertEqual(Member.objects.exclude(pk=holder.pk).get().email, EMAIL)

    def test_an_expired_and_a_mismatch_legacy_holder_are_abandoned_too(self):
        for status in (C.XENDIT_EXPIRED, C.XENDIT_MISMATCH):
            holder, legacy = self.legacy_holder(status)
            self.assertTrue(checkout.abandoned(holder), status)
            self.assertEqual(self.contact().status_code, 200, status)
            checkout.release_abandoned(self.site, "+639171234567", EMAIL)
            holder.refresh_from_db()
            self.assertTrue(members.contacts_released(holder), status)

    def test_a_late_payment_on_a_released_legacy_order_goes_to_review_not_to_a_member_nobody_can_reach(self):
        holder, legacy = self.legacy_holder()
        checkout.release_abandoned(self.site, "+639171234567", EMAIL)
        PackOrder.objects.filter(pk=legacy.pk).update(xendit_invoice_id="inv-legacy")
        legacy.refresh_from_db()
        self.webhook({"id": "inv-legacy", "external_id": legacy.xendit_external_id, "amount": 1488.0, "currency": "PHP"})
        legacy.refresh_from_db()
        self.assertEqual(legacy.payment_status, C.XENDIT_MISMATCH)
        self.assertIsNone(legacy.benefits_active_at)
        self.assertEqual(legacy.events.get(kind="xendit_mismatch").new["reason"], "contacts_released")

    def test_a_paid_member_still_blocks_the_contact(self):
        _, _, order = self.placed_xendit()
        self.webhook(order)
        response = self.contact()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "contact_in_use")

    def test_a_legacy_member_with_a_verified_order_is_not_abandoned(self):
        holder, legacy = self.legacy_holder(C.VERIFIED)
        PackOrder.objects.filter(pk=legacy.pk).update(benefits_active_at=timezone.now())
        self.assertFalse(checkout.abandoned(holder))
        self.assertEqual(self.contact().status_code, 409)

    def test_a_legacy_member_with_benefits_on_an_unpaid_looking_order_is_not_abandoned(self):
        holder, legacy = self.legacy_holder()
        PackOrder.objects.filter(pk=legacy.pk).update(benefits_active_at=timezone.now())
        self.assertFalse(checkout.abandoned(holder))

    def test_a_member_with_a_bank_order_is_not_abandoned(self):
        holder, legacy = self.legacy_holder()
        PackOrder.objects.filter(pk=legacy.pk).update(payment_method="direct", payment_status=C.PENDING_REVIEW)
        self.assertFalse(checkout.abandoned(holder))


class BankTransferHoldsContact(XenditPackCase):
    """Bank transfer follows the same rule since task z8uq9mgpzd: the member is made when the owner approves."""

    def test_a_bank_order_creates_no_member_at_placement(self):
        token, body = self.placed_memberless()
        order = PackOrder.objects.get()
        self.assertIsNone(order.member_id)
        self.assertEqual((order.buyer_mobile, order.buyer_email), ("+639171234567", EMAIL))
        self.assertEqual(order.payment_status, C.PENDING_REVIEW)
        self.assertIsNone(order.benefits_active_at)
        self.assertFalse(SanteRegistration.objects.exists())
        self.assertFalse(Member.objects.exists())

    def test_a_bank_buyer_with_an_order_under_review_still_blocks_the_contact(self):
        self.placed_memberless()
        response = self.contact()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "order_on_file")


class ReadersHandleNoMember(XenditPackCase):
    def setUp(self):
        super().setUp()
        _, _, self.order = self.placed_xendit()

    def test_the_owner_order_row_shows_the_kept_mobile_masked(self):
        row = api_owner.row(self.order)
        self.assertEqual(row["payment_status"], C.AWAITING_XENDIT)
        self.assertIn("4567", row["mobile_masked"])
        self.assertNotIn("0917123", row["mobile_masked"])

    def test_the_owner_order_list_and_detail_work(self):
        self.assertEqual(len(api_owner.orders_list(SimpleNamespace(pack_site=self.site), self.site.public_id)["orders"]), 1)
        detail = api_owner.detail(self.order)
        self.assertIn("mobile_masked", detail)

    def test_the_journey_lists_the_order_with_no_dashboard_access(self):
        rows = journey.recent_orders(self.site, None)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["dashboard_access"])

    def test_prospects_show_the_kept_contact(self):
        rows = [r for r in prospects.order_rows(self.site) if r["kind"] == "order"]
        self.assertEqual(rows[0]["mobile"], "+639171234567")
        self.assertEqual(rows[0]["email"], EMAIL)

    def test_the_buyer_notice_goes_to_the_kept_email_without_a_member(self):
        self.assertIsNotNone(notices.payment_verified_order(self.order.pk))  # to the kept buyer email
        self.assertEqual([m.to for m in mail.outbox], [[EMAIL]])

    def test_the_meta_event_uses_the_kept_contact(self):
        context = SimpleNamespace(client_ip="203.0.113.5", user_agent="UA", fbp="", fbc="", source_url="https://x.test/")
        delivery = SimpleNamespace(event_name="AddPaymentInfo", event_id="api_x")
        event = meta_events.order_event(delivery, self.order, context)
        self.assertIsNotNone(event)
        self.assertIn("em", event["user_data"])

    def test_the_export_carries_the_order_and_the_kept_contact(self):
        data = retention.export_packs(type(self.site).objects.filter(pk=self.site.pk))
        row = data[retention.ORDERS][0]
        self.assertIsNone(row["member"])
        self.assertEqual((row["buyer_mobile"], row["buyer_email"]), ("+639171234567", EMAIL))

    def test_the_site_disposal_blanks_the_kept_contact_and_the_challenge(self):
        PackOrder.objects.filter(pk=self.order.pk).update(onboarding_challenge_id=5)
        retention.blank_packs(type(self.site).objects.filter(pk=self.site.pk))
        self.order.refresh_from_db()
        self.assertEqual((self.order.buyer_mobile, self.order.buyer_email), ("", ""))
        self.assertIsNone(self.order.onboarding_challenge_id)
        self.assertIn("buyer_mobile", retention.blanked()[retention.ORDERS])

    def test_the_details_purge_blanks_the_kept_contact(self):
        purge._clear_details(self.site.pk, timezone.now())
        self.order.refresh_from_db()
        self.assertEqual((self.order.buyer_mobile, self.order.buyer_email), ("", ""))

    def test_members_by_order_leaves_out_an_order_with_no_member(self):
        from apps.members import api_owner as members_owner
        request = SimpleNamespace(member_site=self.site)
        response = members_owner.members_by_order(request, self.site.public_id, ids=self.order.public_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["members"], {})
