"""The service-end export notice (G4b, z8uq9mbbkj): sent once, behind OWNER_NOTICES_ENABLED."""
import os
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.billing import notices, plan, services, tasks
from apps.billing.models import BillingNotice, Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, T0, TRIAL_END, days, fresh, make_active, make_subscription, make_suspended,
    submit,
)
from apps.billing.tests.test_notices import HOUR, NoticeCase, confirm
from apps.notifications.services import EmailNotSent
from apps.sites.models import Site

SUBJECT = "Your NegosyoOnline store data: how to ask for your export"
OWNER = "ana@example.test"


@override_settings(OWNER_NOTICES_ENABLED=True)
class ExportNoticeTests(NoticeCase):
    def notice_mails(self):
        return [m for m in mail.outbox if m.subject == SUBJECT]

    def cancel_active(self, **kwargs):
        """A paid site cancelled inside its paid period: service ends at CYCLE_1_END."""
        subscription = make_active(**kwargs)
        services.cancel(subscription, now=CYCLE_1_END - days(3))
        return fresh(subscription)

    def unpaid_cancelled(self):
        subscription = make_subscription()
        services.cancel(subscription, now=T0 + days(2))
        return fresh(subscription)

    def test_a_cancelled_site_after_its_service_end_gets_one_export_notice(self):
        self.cancel_active()
        self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {"export_notice": 1})
        self.assertEqual(len(self.notice_mails()), 1)
        self.assertEqual(self.notice_mails()[0].to, [OWNER])

    def test_a_cancel_inside_a_paid_period_sends_nothing_until_the_service_end(self):
        self.cancel_active()
        self.assertEqual(self.run_at(CYCLE_1_END - days(2)), {})
        self.assertEqual(self.notice_mails(), [])
        self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {"export_notice": 1})

    def test_a_trial_that_ended_unpaid_gets_one_export_notice(self):
        make_subscription()
        services.sweep(now=TRIAL_END + HOUR)
        result = self.run_at(TRIAL_END + 2 * HOUR)
        self.assertEqual(result, {"export_notice": 1, "trial_ended": 1})
        body = self.notice_mails()[0].body
        self.assertIn("The free trial for Ana's Store ended on 25 November 2026 at 10:00 AM", body)
        self.assertIn("no payment was approved.", body)

    def test_the_notice_names_the_export_date_the_deletion_date_and_support(self):
        self.cancel_active()
        self.run_at(CYCLE_1_END + HOUR)
        body = self.notice_mails()[0].body
        self.assertIn("Hello,", body)
        self.assertIn("Your subscription for Ana's Store ended on 25 December 2026 at 3:00 PM.", body)
        self.assertIn("email support@negosyoonline.com from the email address on your account", body)
        self.assertIn("Please ask by 24 January 2027 at 3:00 PM.", body)
        self.assertIn("After 25 March 2027 at 3:00 PM, as our Terms say", body)
        self.assertIn("Until 25 March 2027 at 3:00 PM you can still bring your site back", body)
        self.assertIn("/settings/billing/", body)
        self.assertTrue(body.rstrip().endswith("All dates are Manila time.\n\nNegosyoOnline"))

    def test_a_never_paid_site_gets_the_thirty_day_dates_in_the_same_text(self):
        sub = self.unpaid_cancelled()
        self.run_at(T0 + days(2) + HOUR)
        body = self.notice_mails()[0].body
        self.assertEqual(sub.download_until, sub.deletion_due_at)
        self.assertIn("Please ask by 13 December 2026 at 10:00 AM.", body)
        self.assertIn("After 13 December 2026 at 10:00 AM, as our Terms say", body)

    def test_a_paid_site_gets_the_ninety_day_deletion_date(self):
        sub = self.cancel_active()
        self.assertEqual(sub.deletion_due_at - sub.service_ended_at, plan.DELETION_AFTER_PAID)
        self.run_at(CYCLE_1_END + HOUR)
        self.assertIn("25 March 2027", self.notice_mails()[0].body)

    def test_the_dates_are_manila_time(self):
        self.cancel_active()
        self.run_at(CYCLE_1_END + HOUR)
        self.assertIn("3:00 PM", self.notice_mails()[0].body)  # CYCLE_1_END is 15:00 Manila
        self.assertNotIn("UTC", self.notice_mails()[0].body)

    def test_no_notice_while_the_dates_are_not_set_and_one_when_the_clocks_start(self):
        subscription = make_subscription()
        with mock.patch("apps.billing.plan.collection_live", return_value=False):
            services.cancel(subscription, now=T0 + days(2))
        self.assertIsNone(fresh(subscription).deletion_due_at)
        self.assertEqual(self.run_at(T0 + days(10)), {})
        services.sweep(now=T0 + days(10))  # collection is live: the clocks start
        self.assertIsNotNone(fresh(subscription).deletion_due_at)
        self.assertEqual(self.run_at(T0 + days(10) + HOUR), {"export_notice": 1})

    def test_the_notice_is_sent_once_and_a_replay_sends_nothing(self):
        self.cancel_active()
        self.run_at(CYCLE_1_END + HOUR)
        self.assertEqual(self.run_at(CYCLE_1_END + 2 * HOUR), {})
        self.assertEqual(BillingNotice.objects.filter(code="export_notice").count(), 1)
        self.assertEqual(len(self.notice_mails()), 1)

    def test_a_new_service_end_after_a_reactivation_gets_a_new_notice(self):
        subscription = self.cancel_active()
        self.run_at(CYCLE_1_END + HOUR)
        payment = submit(subscription, CYCLE_1_END + 2 * HOUR)
        confirm(payment, self.operator, CYCLE_1_END + 3 * HOUR)
        self.assertEqual(fresh(subscription).state, Subscription.State.ACTIVE)
        services.cancel(fresh(subscription), now=CYCLE_1_END + days(2))
        end = fresh(subscription).service_ended_at
        self.assertEqual(self.run_at(end + HOUR).get("export_notice"), 1)
        self.assertEqual(BillingNotice.objects.filter(code="export_notice").count(), 2)

    def test_a_cancel_while_suspended_counts_from_the_cancellation(self):
        subscription = make_suspended()
        suspended_at = CYCLE_1_END + days(7)
        self.run_at(suspended_at + HOUR)  # the paused email only
        self.assertEqual(self.notice_mails(), [])
        later = suspended_at + days(5)
        services.cancel(fresh(subscription), now=later)
        self.assertEqual(self.run_at(later + HOUR), {"export_notice": 1})

    def test_a_cancel_while_suspended_is_not_announced_later_than_48_hours(self):
        subscription = make_suspended()
        later = CYCLE_1_END + days(12)
        services.cancel(fresh(subscription), now=later)
        self.assertEqual(self.run_at(later + days(3)), {})

    def test_a_failed_send_writes_no_row_is_retried_and_given_up_after_48_hours(self):
        self.cancel_active()
        with mock.patch("apps.billing.notices.send_email", side_effect=EmailNotSent()):
            with self.assertLogs("apps.billing.notices", level="WARNING") as logged:
                self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        self.assertNotIn(OWNER, "".join(logged.output))
        self.assertEqual(BillingNotice.objects.filter(code="export_notice").count(), 0)
        self.assertEqual(self.run_at(CYCLE_1_END + 2 * HOUR), {"export_notice": 1})

    def test_a_failed_send_is_given_up_after_48_hours(self):
        self.cancel_active()
        with mock.patch("apps.billing.notices.send_email", side_effect=EmailNotSent()):
            self.run_at(CYCLE_1_END + HOUR)
        self.assertEqual(self.run_at(CYCLE_1_END + days(3)), {})
        self.assertEqual(self.notice_mails(), [])

    def test_a_send_failure_leaves_the_cancellation_and_the_state_unchanged(self):
        sub = self.cancel_active()
        with mock.patch("apps.billing.notices.send_email", side_effect=EmailNotSent()):
            self.run_at(CYCLE_1_END + HOUR)
        after = fresh(sub)
        self.assertEqual(after.state, Subscription.State.CANCELLED)
        self.assertEqual(after.service_ended_at, sub.service_ended_at)
        self.assertEqual(after.deletion_due_at, sub.deletion_due_at)

    def test_a_scan_error_in_the_export_scan_does_not_stop_the_other_emails(self):
        submit(make_subscription(), T0 + days(1))
        def broken(now):
            raise RuntimeError("boom")

        with mock.patch("apps.billing.notices._export_notice_emails", broken):
            with self.assertLogs("apps.billing.notices", level="ERROR"):
                result = self.run_at(T0 + days(1) + HOUR)
        self.assertEqual(result.get("proof_received"), 1)

    def test_nothing_is_sent_or_read_while_the_switch_is_off(self):
        self.cancel_active()
        with override_settings(OWNER_NOTICES_ENABLED=False):
            with self.assertNumQueries(0):
                self.assertEqual(dict(notices._export_notice_emails(CYCLE_1_END + HOUR)), {})
            self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        self.assertEqual(self.notice_mails(), [])

    def test_the_switch_defaults_off(self):
        from config import settings as project_settings
        with mock.patch.dict(os.environ):
            os.environ.pop("OWNER_NOTICES_ENABLED", None)
            self.assertFalse(os.environ.get("OWNER_NOTICES_ENABLED", "").strip() == "1")
        self.assertIn("OWNER_NOTICES_ENABLED", vars(project_settings))

    def test_nothing_is_sent_while_collection_is_off_or_billing_is_off(self):
        self.cancel_active()
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        with override_settings(BILLING_ENABLED=False):
            self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        self.assertEqual(self.notice_mails(), [])

    def test_an_exempt_site_a_deleted_site_a_closed_account_and_a_disposed_address_get_nothing(self):
        sub = self.cancel_active()
        account = type(sub.site.account)
        Subscription.objects.filter(pk=sub.pk).update(billing_exempt=True)
        self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        Subscription.objects.filter(pk=sub.pk).update(billing_exempt=False)
        Site.objects.filter(pk=sub.site_id).update(deleted_at=timezone.now())
        self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        Site.objects.filter(pk=sub.site_id).update(deleted_at=None)
        account.objects.filter(pk=sub.site.account_id).update(closed_at=timezone.now())
        self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        account.objects.filter(pk=sub.site.account_id).update(closed_at=None)
        with mock.patch("apps.billing.notices.is_disposed_address", return_value=True):
            self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        self.assertEqual(BillingNotice.objects.filter(code="export_notice").count(), 0)

    def test_one_sites_notice_goes_only_to_its_own_owner(self):
        self.cancel_active()
        other = make_active(email="b@example.test", name="bs-store")
        services.cancel(other, now=CYCLE_1_END - days(3))
        self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {"export_notice": 2})
        sent = {tuple(m.to): m.body for m in self.notice_mails()}
        self.assertEqual(set(sent), {(OWNER,), ("b@example.test",)})
        self.assertEqual(
            BillingNotice.objects.filter(code="export_notice")
            .values("subscription").distinct().count(), 2,
        )

    def test_the_text_never_promises_an_unconditional_deletion_and_names_the_hold(self):
        self.cancel_active()
        self.run_at(CYCLE_1_END + HOUR)
        body = self.notice_mails()[0].body
        self.assertIn("We do not delete what the law requires us to keep", body)
        self.assertIn("dispute or a fraud check", body)
        self.assertNotIn("will be deleted", body)
        self.assertNotIn("will delete", body)

    def test_the_text_has_no_proof_image_no_xendit_no_tax_claim_and_no_account_number(self):
        self.cancel_active()
        self.run_at(CYCLE_1_END + HOUR)
        text = (self.notice_mails()[0].subject + self.notice_mails()[0].body).lower()
        for banned in ("proof image", "xendit", "tax", "bir", "0000 0000 00", "test receiver"):
            self.assertNotIn(banned, text)

    def test_no_backlog_when_the_switch_is_turned_on_after_the_window(self):
        self.cancel_active()
        with override_settings(OWNER_NOTICES_ENABLED=False):
            self.assertEqual(self.run_at(CYCLE_1_END + HOUR), {})
        self.assertEqual(self.run_at(CYCLE_1_END + days(3)), {})
        self.assertEqual(self.notice_mails(), [])

    def test_the_task_runs_the_export_scan_in_process(self):
        self.cancel_active()
        with mock.patch("apps.billing.notices.timezone") as clock:
            clock.now.return_value = CYCLE_1_END + HOUR
            outcome = tasks.send_notices.apply()
        self.assertTrue(outcome.successful(), outcome.traceback)
        self.assertEqual(outcome.result, {"export_notice": 1})
