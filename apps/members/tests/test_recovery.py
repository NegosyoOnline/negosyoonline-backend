"""Security holds and Subscriber-assisted recovery (service logic; the Subscriber routes come later)."""
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.core import mail
from django.utils import timezone

from apps.accounts.models import Account
from apps.common.errors import ApiError
from apps.members import holds, hooks, recovery, sessions
from apps.members.models import (
    ImmutableRecordError, Member, MemberContactAudit, MemberRecoveryCheck, MemberSecurityHold,
)
from apps.members.tests.base import EMAIL, MOBILE, MembersTestCase


class RecoveryTestCase(MembersTestCase):
    def setUp(self):
        super().setUp()
        self.account = self.site.account
        Member.objects.filter(pk=self.member.pk).update(email_state="verified", mobile_state="verified")
        self.member.refresh_from_db()

    def passed_check(self, **answers):
        items = {"full_name": "matched", "delivery_address": "matched", **answers}
        return recovery.record_checklist(self.account, self.site, self.member, items)


class ChecklistTests(RecoveryTestCase):
    def test_a_passing_check_masks_the_subscriber_and_lasts_fifteen_minutes(self):
        check = self.passed_check()
        self.assertTrue(check.passed)
        self.assertEqual(check.expires_at - check.created_at, recovery.CHECK_TTL)
        self.assertEqual(check.actor_account_id, self.account.pk)
        self.assertNotIn(self.account.email, check.actor_email_masked)
        self.assertEqual(check.items["pack_order_number"], "not_applicable_yet")

    def test_one_not_matched_fails_it(self):
        self.assertFalse(self.passed_check(full_name="not_matched").passed)

    def test_every_applicable_item_must_be_answered_with_a_known_answer(self):
        for items in ({"full_name": "matched"}, {"full_name": "matched", "delivery_address": "maybe"},
                      {"full_name": "matched", "delivery_address": "matched", "payout_last4": "matched"}):
            with self.subTest(items=items), self.assertRaises(ApiError) as caught:
                recovery.record_checklist(self.account, self.site, self.member, items)
            self.assertEqual(caught.exception.code, "invalid_items")

    def test_a_later_slice_can_add_an_applicable_item(self):
        recovery.register_checklist_item("pack_order_number", lambda member: True, reference=lambda m: "ORD-1")
        self.addCleanup(recovery._extra_items.clear)
        self.addCleanup(recovery._references.clear)
        items = {i["key"]: i for i in recovery.checklist_items(self.member)}
        self.assertTrue(items["pack_order_number"]["applicable"])
        self.assertEqual(items["pack_order_number"]["reference"], "ORD-1")
        self.assertFalse(items["payout_last4"]["applicable"])
        with self.assertRaises(ValueError):
            recovery.register_checklist_item("full_name", lambda member: True)

    def test_search_finds_by_mobile_email_or_name_within_the_queryset(self):
        qs = Member.objects.filter(site=self.site)
        self.assertEqual(list(recovery.search(qs, "09171234567")), [self.member])
        self.assertEqual(list(recovery.search(qs, " Maria@Buyer.test ")), [self.member])
        self.assertEqual(list(recovery.search(qs, "santos")), [self.member])
        self.assertEqual(list(recovery.search(qs, "x")), [])
        self.assertEqual(list(recovery.search(Member.objects.filter(site=self.other_site), "santos")), [])


class RecoverContactTests(RecoveryTestCase):
    def recover(self, check=None, **kwargs):
        check = check or self.passed_check()
        return recovery.recover_contact(self.account, self.site, self.member.pk, check_id=check.pk, **kwargs)

    def test_a_passed_check_replaces_the_contact_starts_the_hold_revokes_and_notifies(self):
        session, _ = sessions.create_session(self.site, self.member)
        member, hold = self.recover(new_email="New@Buyer.test", ip="203.0.113.8")

        self.assertEqual((member.email, member.email_state, member.email_verified_at), ("new@buyer.test", "pending", None))
        self.assertEqual(member.mobile_state, "verified")
        session.refresh_from_db()
        self.assertEqual(session.revoke_reason, "recovery")
        self.assertEqual(hold.kind, "recovery")
        self.assertEqual(hold.ends_at - hold.starts_at, holds.HOLD_DURATION)
        audit = MemberContactAudit.objects.get()
        self.assertEqual((audit.kind, audit.actor_type, audit.actor_ref), ("subscriber_recovery", "subscriber", self.account.pk))
        self.assertEqual(hold.audit, audit)
        self.assertEqual(audit.recovery_check.used_at is not None, True)
        notice = mail.outbox[-1]
        self.assertEqual(notice.to, [EMAIL])
        self.assertIn("paused until", notice.body)
        self.assertNotIn("new@buyer.test", notice.body)

    def test_both_contacts_at_once_write_two_audit_rows_and_one_hold(self):
        self.recover(new_email="n@buyer.test", new_mobile="09175550170")
        self.assertEqual(MemberContactAudit.objects.count(), 2)
        self.assertEqual(MemberSecurityHold.objects.count(), 1)

    def test_a_check_is_good_once(self):
        check = self.passed_check()
        self.recover(check, new_email="n@buyer.test")
        with self.assertRaises(ApiError) as caught:
            self.recover(check, new_email="m@buyer.test")
        self.assertEqual(caught.exception.code, "recovery_check_invalid")

    def test_a_failed_expired_or_other_members_check_is_refused(self):
        failed = self.passed_check(full_name="not_matched")
        expired = self.passed_check()
        MemberRecoveryCheck.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        other = self.new_member(email="o@buyer.test", mobile="09175550171")
        theirs = recovery.record_checklist(self.account, self.site, other, {"full_name": "matched", "delivery_address": "matched"})
        for check in (failed, expired, theirs):
            with self.subTest(check=check.pk), self.assertRaises(ApiError) as caught:
                self.recover(check, new_email="n@buyer.test")
            self.assertEqual(caught.exception.code, "recovery_check_invalid")
        self.assertEqual(Member.objects.get(pk=self.member.pk).email, EMAIL)

    def test_same_taken_or_missing_values_are_refused_and_change_nothing(self):
        self.new_member(email="taken@buyer.test", mobile="09175550172")
        for kwargs, code in (
            ({"new_email": EMAIL}, "same_contact"), ({"new_mobile": MOBILE}, "same_contact"),
            ({"new_email": "taken@buyer.test"}, "contact_in_use"), ({}, "nothing_to_change"),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ApiError) as caught:
                self.recover(**kwargs)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(MemberSecurityHold.objects.count(), 0)
        self.assertEqual(MemberContactAudit.objects.count(), 0)

    def test_a_member_of_another_site_is_not_found(self):
        self.assertIsNone(
            recovery.recover_contact(self.account, self.other_site, self.member.pk, check_id=1, new_email="n@buyer.test")
        )

    def test_a_later_slice_follow_up_runs_after_the_commit_and_its_failure_never_fails_it(self):
        ran = []

        def boom(member):
            raise RuntimeError("later slice broke")

        recovery.register_follow_up(boom, lambda member: None)
        recovery.register_follow_up(ran.append, lambda member: "Your order link was sent.")
        self.addCleanup(recovery._follow_ups.clear)
        with self.assertLogs(recovery.logger, "ERROR"):
            member, hold = self.recover(new_email="n@buyer.test")
        self.assertEqual(ran, [member])
        self.assertIn("Your order link was sent.", recovery.saved_message(hold, member))
        self.assertIn(recovery.NEXT_SIGN_IN, recovery.saved_message(hold))


class HoldTests(RecoveryTestCase):
    def hold(self, kind="recovery", **extra):
        now = timezone.now()
        audit = MemberContactAudit.objects.create(
            site=self.site, member=self.member, kind="subscriber_recovery", field="email", actor_type="subscriber"
        )
        if kind == "recovery":
            return holds.start_recovery_hold(self.member, audit, now=now)
        return holds.start_payout_fallback_hold(self.member, type("D", (), {"pk": 5})(), now=now)

    def test_no_hold_means_nothing_is_blocked(self):
        for action in holds.ACTIONS:
            holds.assert_no_hold(self.member, action)
        self.assertIsNone(holds.current_hold(self.member))
        self.assertIsNone(holds.hold_out(None))

    def test_a_recovery_hold_blocks_all_three_actions_for_twenty_four_hours(self):
        hold = self.hold()
        for action in holds.ACTIONS:
            with self.subTest(action=action), self.assertRaises(ApiError) as caught:
                holds.assert_no_hold(self.member, action)
            self.assertEqual((caught.exception.code, caught.exception.status), ("security_hold", 409))
            self.assertIn("For your security", caught.exception.message)
        later = hold.ends_at + timedelta(seconds=1)
        holds.assert_no_hold(self.member, "withdrawal_request", now=later)
        self.assertEqual(holds.hold_out(hold)["blocks"], sorted(holds.ACTIONS))

    def test_a_payout_fallback_hold_blocks_only_new_withdrawal_requests(self):
        self.hold("payout_fallback")
        with self.assertRaises(ApiError) as caught:
            holds.assert_no_hold(self.member, "withdrawal_request")
        self.assertIn("New withdrawal requests are paused until", caught.exception.message)
        holds.assert_no_hold(self.member, "payout_destination_change")
        holds.assert_no_hold(self.member, "record_payout")

    def test_an_unknown_action_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            holds.active_hold(self.member, "launch")

    def test_the_end_is_shown_in_philippine_time(self):
        moment = datetime(2026, 11, 11, 16, 5, tzinfo=dt_timezone.utc)
        self.assertEqual(holds.display(moment), "12 Nov 2026, 12:05 AM (Philippine time)")

    def test_a_hold_is_per_member(self):
        self.hold()
        other = self.new_member(email="o@buyer.test", mobile="09175550173")
        holds.assert_no_hold(other, "withdrawal_request")


class ImmutabilityTests(RecoveryTestCase):
    def test_audit_hold_and_check_rows_cannot_be_changed_or_deleted(self):
        audit = MemberContactAudit.objects.create(
            site=self.site, member=self.member, kind="self_service", field="email", actor_type="member"
        )
        hold = holds.start_recovery_hold(self.member, audit, now=timezone.now())
        check = self.passed_check()
        for row in (audit, hold, check):
            with self.subTest(row=type(row).__name__):
                with self.assertRaises(ImmutableRecordError):
                    row.delete()
                with self.assertRaises(ImmutableRecordError):
                    row.save()
        check.used_at = timezone.now()
        check.save(update_fields=["used_at"])  # the one allowed change

    def test_a_hold_needs_its_audit_or_its_payout_reference(self):
        from django.db import IntegrityError, transaction

        now = timezone.now()
        for kind in ("recovery", "payout_fallback"):
            with self.subTest(kind=kind), self.assertRaises(IntegrityError), transaction.atomic():
                MemberSecurityHold.objects.create(
                    site=self.site, member=self.member, kind=kind, starts_at=now, ends_at=now
                )


class HooksRegistryTests(RecoveryTestCase):
    def test_sms_first_is_off_until_a_template_says_so_and_a_failing_hook_is_off(self):
        self.assertFalse(hooks.sign_in_sms_first(self.site))
        hooks.register_sign_in_sms_first(lambda site: 1 / 0)
        self.addCleanup(hooks._sign_in_sms_first.clear)
        with self.assertLogs(hooks.logger, "ERROR"):
            self.assertFalse(hooks.sign_in_sms_first(self.site))
        hooks.register_sign_in_sms_first(lambda site: True)
        with self.assertLogs(hooks.logger, "ERROR"):
            self.assertTrue(hooks.sign_in_sms_first(self.site))
