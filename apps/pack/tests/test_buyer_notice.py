"""The day-76 buyer email (ClickUp z8uq9mep8p, plan 2026-11-13-buyer-portal-z8uq9mbk0m, C3 tests)."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import override_settings

from apps.billing.models import Subscription
from apps.notifications.models import SmsMessage
from apps.notifications.services import EmailNotSent
from apps.pack import buyer_notice, constants as C, tasks
from apps.pack.models import PackBuyerNotice
from apps.pack.tests.owner_base import make_order
from apps.pack.tests.test_portal import POINT, SECOND, PortalBase, days

OPENS = POINT - days(14)  # day 76 of a paid site


@override_settings(PACK_PURGE_ENABLED=True, PACK_BUYER_NOTICE_ENABLED=True)
class NoticeBase(PortalBase):
    def setUp(self):
        super().setUp()
        self.suspend()

    def run_at(self, moment):
        self.at(moment)
        return buyer_notice.run(moment)

    def notices(self):
        return PackBuyerNotice.objects.filter(site=self.site)


class NoticeSendTests(NoticeBase):
    def test_nothing_before_14_days_then_one_email_to_the_buyer(self):
        self.assertEqual(self.run_at(OPENS - SECOND), {})
        self.assertEqual(mail.outbox, [])

        self.assertEqual(self.run_at(OPENS), {"sent": 1})

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.member.email])
        row = self.notices().get()
        self.assertEqual((row.member, row.deletion_due_at, row.attempts), (self.member, POINT, 1))
        self.assertIsNotNone(row.sent_at)

    def test_the_text_is_block_c_word_for_word_and_the_time_is_the_deletion_point(self):
        self.run_at(OPENS)

        message = mail.outbox[0]
        store = self.site.display_name
        self.assertEqual(message.subject, f"Your {store} order view closes on April 1, 2027 at 3:00 PM.")
        self.assertEqual(
            message.body,
            f"{store} stopped taking orders on January 1, 2027. You can still sign in to see your "
            "Membership Pack order, payment status and delivery status until 3:00 PM Philippine time "
            "on April 1, 2027. After that this view closes. On or shortly after that time, the "
            "personal registration and delivery details held for your order (such as your date of "
            "birth, usernames and delivery address) are deleted or anonymised. Records that must be "
            "kept for legal, payment, accounting, fraud or audit purposes are kept and are not "
            "available to you, the store or this page. Please save anything you need before 3:00 PM "
            "on April 1, 2027.",
        )

    def test_it_is_sent_once_however_often_the_task_runs(self):
        self.run_at(OPENS)

        for moment in (OPENS + timedelta(hours=1), OPENS + days(3), POINT - SECOND):
            self.assertEqual(self.run_at(moment), {})

        self.assertEqual(len(mail.outbox), 1)

    def test_each_buyer_with_a_verified_order_gets_one_and_nobody_else(self):
        second, _ = make_order(self.site, status=C.VERIFIED)
        make_order(self.site, status=C.PENDING_REVIEW)
        make_order(self.site, status=C.REJECTED)
        other_order, _ = make_order(self.other_site, status=C.VERIFIED)

        self.assertEqual(self.run_at(OPENS), {"sent": 2})

        self.assertEqual(sorted(m.to[0] for m in mail.outbox), sorted([self.member.email, second.member.email]))
        self.assertFalse(PackBuyerNotice.objects.filter(site=self.other_site).exists())
        self.assertNotIn(other_order.member.email, [m.to[0] for m in mail.outbox])

    def test_no_text_message_is_made(self):
        self.run_at(OPENS)

        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_the_table_refuses_a_second_row_for_the_same_date(self):
        self.run_at(OPENS)

        with self.assertRaises(IntegrityError), transaction.atomic():
            PackBuyerNotice.objects.create(site=self.site, member=self.member, deletion_due_at=POINT)

    def test_a_reactivated_site_sends_nothing(self):
        Subscription.objects.filter(site=self.site).update(deletion_due_at=None, download_until=None)

        self.assertEqual(self.run_at(OPENS + days(1)), {})

        self.assertEqual(mail.outbox, [])
        self.assertFalse(self.notices().exists())

    def test_the_subscription_is_read_again_before_each_send(self):
        with mock.patch("apps.billing.services.end_dates", side_effect=[
            (OPENS, POINT), None,  # the list: due; the re-read before sending: reactivated
        ]):
            self.assertEqual(self.run_at(OPENS), {})

        self.assertEqual(mail.outbox, [])

    def test_a_date_that_changed_since_the_record_sends_nothing_for_the_old_one(self):
        later = POINT + days(5)
        with mock.patch("apps.billing.services.end_dates", side_effect=[
            (OPENS, POINT), (OPENS, later),
        ]):
            self.assertEqual(self.run_at(OPENS), {})

        self.assertEqual(mail.outbox, [])

    def test_a_site_that_is_still_served_is_not_listed(self):
        self.assertNotIn(self.other_site.pk, buyer_notice.due_site_ids(OPENS))
        self.assertNotIn(self.site.pk, buyer_notice.due_site_ids(OPENS - SECOND))
        self.assertIn(self.site.pk, buyer_notice.due_site_ids(OPENS))


class NoticeRetryTests(NoticeBase):
    def failing(self):
        return mock.patch.object(buyer_notice, "send_email", side_effect=EmailNotSent())

    def test_a_failed_send_is_tried_again_every_six_hours_only(self):
        with self.failing() as send:
            self.assertEqual(self.run_at(OPENS), {"failed": 1})
            self.assertEqual(self.run_at(OPENS + timedelta(hours=5, minutes=59)), {})
            self.assertEqual(self.run_at(OPENS + timedelta(hours=6)), {"failed": 1})
            self.assertEqual(send.call_count, 2)
        self.assertEqual(self.notices().get().attempts, 2)

        self.assertEqual(self.run_at(OPENS + timedelta(hours=12)), {"sent": 1})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.notices().get().attempts, 3)

    def test_at_most_six_retries_then_it_is_given_up_once_and_logged_once(self):
        with self.failing() as send:
            with self.assertLogs("apps.pack.buyer_notice", level="ERROR") as logged:
                for hours in range(0, 6 * 12, 6):
                    self.run_at(OPENS + timedelta(hours=hours))
        self.assertEqual(send.call_count, 7)  # the first try and six retries
        row = self.notices().get()
        self.assertIsNotNone(row.gave_up_at)
        self.assertIsNone(row.sent_at)
        self.assertEqual(len([line for line in logged.output if "given up" in line]), 1)
        self.assertIn(f"site {self.site.pk}", logged.output[-1])

    def test_no_retry_once_the_point_has_passed_and_the_miss_is_logged_once(self):
        with self.failing():
            self.run_at(POINT - timedelta(hours=3))
        self.assertEqual(self.notices().get().attempts, 1)

        with mock.patch.object(buyer_notice, "send_email") as send:
            with self.assertLogs("apps.pack.buyer_notice", level="ERROR") as logged:
                self.assertEqual(self.run_at(POINT), {"gave_up": 1})
                self.assertEqual(self.run_at(POINT + timedelta(hours=1)), {})
            send.assert_not_called()
        self.assertEqual(len(logged.output), 1)
        self.assertEqual(mail.outbox, [])

    def test_an_outage_past_the_point_sends_nothing_and_logs_once(self):
        with self.assertLogs("apps.pack.buyer_notice", level="ERROR") as logged:
            self.assertEqual(self.run_at(POINT + days(2)), {"gave_up": 1})
            self.assertEqual(self.run_at(POINT + days(2) + timedelta(hours=1)), {})

        self.assertEqual(mail.outbox, [])
        self.assertEqual(len(logged.output), 1)
        self.assertNotIn(self.member.email, logged.output[0])

    def test_a_disposed_address_is_never_emailed(self):
        from apps.members.models import Member
        Member.objects.filter(pk=self.member.pk).update(email="deleted-member-1@deleted.invalid")

        with self.assertLogs("apps.pack.buyer_notice", level="ERROR"):
            self.assertEqual(self.run_at(OPENS), {"gave_up": 1})

        self.assertEqual(mail.outbox, [])


class NoticeSwitchTests(NoticeBase):
    @override_settings(PACK_BUYER_NOTICE_ENABLED=False)
    def test_off_sends_nothing_and_reads_nothing(self):
        self.at(OPENS)
        with mock.patch.object(buyer_notice, "due_site_ids") as listed:
            self.assertEqual(tasks.run_buyer_notice.apply().result, {"skipped": "off"})
            listed.assert_not_called()
        self.assertEqual(mail.outbox, [])

    @override_settings(PACK_PURGE_ENABLED=False)
    def test_it_is_never_sent_unless_the_purge_is_on(self):
        self.assertEqual(buyer_notice.run(OPENS), {"skipped": "off"})
        self.assertEqual(mail.outbox, [])
        self.assertFalse(PackBuyerNotice.objects.exists())

    @override_settings(PACK_ENABLED=False)
    def test_pack_off_sends_nothing(self):
        self.assertEqual(buyer_notice.run(OPENS), {"skipped": "off"})
