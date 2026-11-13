"""The daily invoice alert (Billing Build B5, PR 10): one email a day to the active staff while
an invoice is waiting to be written. It sends nothing, and reads nothing, while the register is off."""
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from celery.schedules import crontab
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.invoices import alert, tasks
from apps.notifications.models import EmailMessage
from apps.operators.models import User
from apps.statements.tests.base import make_item

ON = dict(BILLING_ENABLED=True, BILLING_INVOICE_REGISTER_ENABLED=True,
          HQ_STAFF_ORIGIN="https://hq.example.test")
MANILA = ZoneInfo("Asia/Manila")
MORNING = datetime(2026, 11, 28, 8, 0, tzinfo=MANILA)


def staff(name, *, email=None, active=True, is_staff=True):
    return User.objects.create(
        username=name, email=email if email is not None else f"{name}@example.test",
        is_active=active, is_staff=is_staff,
    )


@override_settings(**ON)
class AlertTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = mock.patch("apps.invoices.alert.timezone.now", return_value=MORNING)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = staff("admin")

    def run_alert(self):
        return tasks.daily_alert()

    def test_the_switch_off_reads_nothing_and_sends_nothing(self):
        make_item(sale_date=date(2026, 11, 25))
        for off in (dict(BILLING_INVOICE_REGISTER_ENABLED=False), dict(BILLING_ENABLED=False)):
            with override_settings(**off), self.assertNumQueries(0):
                self.assertEqual(self.run_alert(), {"skipped": "off"})
            self.assertEqual(len(mail.outbox), 0)

    def test_no_pending_item_sends_nothing(self):
        make_item(sale_date=date(2026, 11, 25), status="recorded")
        self.assertEqual(self.run_alert(), {"skipped": "nothing pending"})
        self.assertEqual(len(mail.outbox), 0)

    def test_one_email_to_each_active_staff_user_and_to_no_one_else(self):
        make_item(sale_date=date(2026, 11, 25))
        second = staff("second")
        staff("gone", active=False)
        staff("customer", is_staff=False)
        staff("blank", email="")
        self.assertEqual(self.run_alert(), {"sent": 2})
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), sorted([self.admin.email, second.email]))
        self.assertTrue(all(len(m.to) == 1 for m in mail.outbox))
        self.assertEqual(EmailMessage.objects.filter(category="invoice_queue_alert").count(), 2)

    def test_the_subject_and_body(self):
        make_item(sale_date=date(2026, 11, 25))
        make_item(sale_date=date(2026, 11, 26))
        make_item(sale_date=date(2026, 11, 20), status="recorded")
        make_item(sale_date=date(2026, 11, 21), status="uploaded")
        self.run_alert()
        message = mail.outbox[0]
        self.assertEqual(message.subject, "NegosyoOnline HQ: 2 invoices to write, oldest 3 days")
        for line in (
            "Ready to write: 2", "Issued, awaiting upload: 1", "Awaiting verification: 1",
            "Oldest waiting sale date: 25 Nov 2026",
            "DO NOT issue invoices in a single delayed Monday batch.",
            "https://hq.example.test/finance/invoices/",
        ):
            self.assertIn(line, message.body)

    def test_singular_one_invoice_one_day(self):
        make_item(sale_date=date(2026, 11, 27))
        self.run_alert()
        self.assertEqual(mail.outbox[0].subject, "NegosyoOnline HQ: 1 invoice to write, oldest 1 day")

    def test_zero_days_is_plural(self):
        make_item(sale_date=date(2026, 11, 28))
        self.run_alert()
        self.assertEqual(mail.outbox[0].subject, "NegosyoOnline HQ: 1 invoice to write, oldest 0 days")

    def test_several_invoices_one_day_old(self):
        make_item(sale_date=date(2026, 11, 27))
        make_item(sale_date=date(2026, 11, 28))
        self.run_alert()
        self.assertEqual(mail.outbox[0].subject, "NegosyoOnline HQ: 2 invoices to write, oldest 1 day")

    def test_no_customer_data_is_in_the_email(self):
        make_item(sale_date=date(2026, 11, 25), total=39900, site_name="Zebra Secret Shop",
                  site_public_id="site_zebra", source_ref="btp_secretref")
        self.run_alert()
        message = mail.outbox[0]
        text = message.subject + message.body
        for leaked in ("Zebra", "site_zebra", "399", "btp_secretref"):
            self.assertNotIn(leaked, text)

    def test_a_second_run_the_same_manila_day_sends_nothing(self):
        make_item(sale_date=date(2026, 11, 25))
        self.assertEqual(self.run_alert(), {"sent": 1})
        self.assertEqual(self.run_alert(), {"skipped": "already sent today"})
        self.assertEqual(len(mail.outbox), 1)

    def test_the_next_manila_day_sends_again(self):
        make_item(sale_date=date(2026, 11, 25))
        self.run_alert()
        with mock.patch("apps.invoices.alert.timezone.now",
                        return_value=datetime(2026, 11, 29, 8, 0, tzinfo=MANILA)):
            self.assertEqual(self.run_alert(), {"sent": 1})
        self.assertEqual(len(mail.outbox), 2)

    def test_the_day_is_the_manila_day_not_the_utc_day(self):
        make_item(sale_date=date(2026, 11, 25))
        early = datetime(2026, 11, 29, 0, 30, tzinfo=MANILA)  # still the 28th in UTC
        late = datetime(2026, 11, 29, 20, 0, tzinfo=MANILA)  # the 29th in UTC
        with mock.patch("apps.invoices.alert.timezone.now", return_value=early):
            self.assertEqual(self.run_alert(), {"sent": 1})
        with mock.patch("apps.invoices.alert.timezone.now", return_value=late):
            self.assertEqual(self.run_alert(), {"skipped": "already sent today"})

    def test_a_failed_send_to_everyone_can_be_retried_the_same_day(self):
        make_item(sale_date=date(2026, 11, 25))
        with mock.patch("apps.invoices.alert.send_email", side_effect=alert.EmailNotSent()):
            self.assertEqual(self.run_alert(), {"sent": 0})
        self.assertEqual(self.run_alert(), {"sent": 1})

    def test_one_failed_recipient_does_not_stop_the_others(self):
        make_item(sale_date=date(2026, 11, 25))
        staff("second")
        real = alert.send_email

        def flaky(**kwargs):
            if kwargs["to"] == self.admin.email:
                raise alert.EmailNotSent()
            return real(**kwargs)

        with mock.patch("apps.invoices.alert.send_email", side_effect=flaky):
            self.assertEqual(self.run_alert(), {"sent": 1})

    def test_without_an_hq_origin_the_path_is_named_instead(self):
        make_item(sale_date=date(2026, 11, 25))
        with override_settings(HQ_STAFF_ORIGIN=""):
            self.run_alert()
        self.assertIn("/finance/invoices/", mail.outbox[0].body)
        self.assertNotIn("http", mail.outbox[0].body)


class ScheduleTests(TestCase):
    def test_the_beat_entry_runs_the_task_at_eight_manila_time(self):
        entry = settings.CELERY_BEAT_SCHEDULE["invoices-daily-alert"]
        self.assertEqual(entry["task"], "invoices.daily_alert")
        self.assertEqual(entry["schedule"], crontab(hour=8, minute=0))
        self.assertEqual(settings.TIME_ZONE, "Asia/Manila")

    def test_the_worker_knows_the_task(self):
        from config.celery import app

        app.loader.import_default_modules()
        self.assertIn("invoices.daily_alert", app.tasks)
