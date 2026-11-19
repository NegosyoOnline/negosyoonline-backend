"""Task z8uq9mgr0g (ruling 1100430000578068, Coach Ed 2026-11-19): a bank-transfer buyer is a member from
placement, but member benefits switch on only after the owner verifies the transfer."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.pack import constants as C, interfaces, review
from apps.pack.models import PackOrder, SanteRegistration
from apps.pack.tests.base import PackTestCase
from apps.pack.tests.test_member_pack import MemberPackCase
from apps.shop import quote


@override_settings(MEMBERS_ENABLED=True, PACK_ENABLED=True)
class BankBenefitsTests(MemberPackCase):
    def approve(self):
        with self.captureOnCommitCallbacks(execute=True):
            review.approve(self.owner, self.site, self.order.pk, state_version=self.order.state_version)
        self.order.refresh_from_db()

    def test_placement_gives_a_member_t1_pending_review_and_no_benefits(self):
        self.assertEqual(self.order.payment_status, C.PENDING_REVIEW)
        self.assertIsNone(self.order.benefits_active_at)
        self.assertIsNotNone(self.order.member_id)
        self.assertEqual(SanteRegistration.objects.get(member=self.member).status, "awaiting_buyer_details")
        self.assertFalse(interfaces.benefits_active(self.member))

    def test_member_price_is_off_before_verify_and_on_after_approve(self):
        self.assertFalse(quote.resolve_buyer(self.site, self.session).benefits)
        self.approve()
        self.assertIsNotNone(self.order.benefits_active_at)
        self.assertTrue(quote.resolve_buyer(self.site, self.session).benefits)

    def test_the_dashboard_is_not_locked_and_the_registration_card_shows_before_verify(self):
        self.assertFalse(interfaces.pack_dashboard_locked(self.member))
        body = self.member_post("").json()
        self.assertIsNotNone(body["registration"])
        self.assertTrue(body["pack"]["benefits_off"])

    def test_reject_keeps_benefits_off_with_the_lock_on(self):
        self.reject()
        self.assertIsNone(self.order.benefits_active_at)
        self.assertTrue(interfaces.pack_dashboard_locked(self.member))
        self.assertFalse(interfaces.benefits_active(self.member))
        self.assertIsNotNone(self.member_post("").json()["registration"])

    def test_resubmit_then_approve_turns_benefits_on(self):
        self.reject()
        self.assertEqual(self.resubmit().status_code, 200)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.benefits_active_at)
        self.assertTrue(interfaces.pack_dashboard_locked(self.member))
        self.approve()
        self.assertEqual(self.order.payment_status, C.VERIFIED)
        self.assertIsNotNone(self.order.benefits_active_at)
        self.assertFalse(interfaces.pack_dashboard_locked(self.member))
        self.assertFalse(self.member_post("").json()["pack"]["benefits_off"])

    def test_a_legacy_bank_order_with_benefits_keeps_them_through_approve(self):
        legacy = timezone.now() - timedelta(days=3)
        PackOrder.objects.filter(pk=self.order.pk).update(benefits_active_at=legacy)
        self.order.refresh_from_db()
        self.assertTrue(interfaces.benefits_active(self.member))
        self.assertFalse(interfaces.pack_dashboard_locked(self.member))
        self.approve()
        self.assertEqual(self.order.benefits_active_at, legacy)


class OnboardingWithoutAnOrderStaysLocked(PackTestCase):
    def test_an_onboarding_member_with_no_live_order_is_still_locked(self):
        from apps.members import services as members
        member = members.create_member(
            self.site, full_name="No Order", mobile="09170000009", email="no@order.test", email_state="pending",
        )
        self.assertTrue(interfaces.pack_dashboard_locked(member))
