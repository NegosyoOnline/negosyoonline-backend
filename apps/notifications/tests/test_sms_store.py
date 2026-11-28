"""SMS-OWNER BE-1: a store's text to one of its own members. The purposes, the refusal order of
the per-store caps, the codes' priority and the room kept for codes, the settings fields."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.common.errors import ApiError
from apps.notifications import gateway, services, sms
from apps.notifications import models as notification_models
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.services import send_sms
from apps.notifications.tests.base import CODE_TEXT, MOBILE, SmsTestCase, fallback, online_phone, set_policy
from apps.operators.models import OperatorAuditEntry
from apps.sites.tests.base import make_account, make_site

TEXT = "Ana's Store: your order is ready."


def store_text(site, purpose="store_reply", **fields):
    fields = {
        "to": MOBILE, "purpose": purpose, "text": TEXT, "site": site,
        "ttl": sms.STAFF_TEXT_LIFETIME, **fields,
    }
    return send_sms(**fields)


def code():
    return send_sms(to=MOBILE, purpose="mobile_verify", text=CODE_TEXT)


class StoreTextPurposeTests(SmsTestCase):
    def setUp(self):
        super().setUp()
        online_phone()
        self.site = make_site(make_account())

    def test_the_two_store_purposes_are_declared_paced_and_at_the_staff_priority(self):
        for name in ("store_reply", "store_compose"):
            with self.subTest(purpose=name):
                declared = sms.PURPOSES[name]
                self.assertEqual(declared.category, "store")
                self.assertEqual(declared.priority, SmsMessage.Priority.REPLY)
                self.assertTrue(declared.paced)
                self.assertIn(name, sms.paced_purposes())
        self.assertEqual(sms.STORE_PURPOSES, ("store_reply", "store_compose", "store_bulk"))

    def test_a_store_text_names_its_site_and_has_no_email_fallback(self):
        with self.assertRaises(ValueError):
            store_text(None)
        with self.assertRaises(ValueError):
            store_text(self.site, fallback=fallback())
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_a_store_text_is_one_plain_sms_without_a_link(self):
        for text in ("x" * 161, "See https://example.test/a", "See example.com/a", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                store_text(self.site, text=text)

    def test_a_queued_store_text_makes_a_record_with_its_site(self):
        result = store_text(self.site)
        self.assertEqual((result.outcome, result.reason), (sms.QUEUED, ""))
        record = SmsMessage.objects.get()
        self.assertEqual((record.category, record.purpose, record.site), ("store", "store_reply", self.site))
        self.assertIsNone(sms.take_fallback(record))
        self.assertEqual(sms.take_text(record), TEXT)

    def test_no_new_marketing_purpose(self):
        marketing = [n for n, d in sms.PURPOSES.items() if d.priority == SmsMessage.Priority.MARKETING]
        self.assertEqual(marketing, ["inbox_bulk", "store_bulk"])


class StoreCapTests(SmsTestCase):
    """STORE-TEXT-NO-CAP (Coach Ed 2026-11-27): a store's one-to-one text has no daily or hourly
    cap. A text from the store's own phone takes no platform slot; one from the platform phone
    still keeps the room for codes."""

    def setUp(self):
        super().setUp()
        self.platform = online_phone()
        self.site = make_site(make_account())
        self.other = make_site(make_account("bo@example.test"), name="bos-store", display_name="Bo's Store")

    def give_own_phone(self, site, name="own"):
        line = mock.patch.object(sms, "site_has_line", return_value=True)
        line.start()
        self.addCleanup(line.stop)
        enabled = override_settings(SMS_SITE_LINES_ENABLED=True)
        enabled.enable()
        self.addCleanup(enabled.disable)
        return online_phone(name, site=site)

    def assert_refused(self, site, reason):
        with self.assertLogs("apps.notifications", level="ERROR"):
            result = store_text(site)
        self.assertEqual((result.outcome, result.reason), (sms.REFUSED, reason))
        self.assertEqual(result.message.status, SmsMessage.Status.REFUSED)

    def test_the_old_caps_are_gone_from_the_code_and_kept_in_the_policy(self):
        self.assertFalse(hasattr(sms, "STORE_HOURLY_LIMIT"))
        row = sms.policy()
        self.assertEqual((row.store_daily_limit, row.all_stores_daily_limit), (50, 1000))

    def test_a_store_with_its_own_phone_sends_past_50_a_day_and_20_an_hour(self):
        self.give_own_phone(self.site)
        for _ in range(55):
            self.assertEqual(store_text(self.site).outcome, sms.QUEUED)
        self.assertEqual(sms.store_used_today(self.site), 55)

    def test_the_stored_caps_enforce_nothing_even_when_low(self):
        set_policy(store_daily_limit=1, all_stores_daily_limit=1)
        for site in (self.site, self.site, self.other):
            self.assertEqual(store_text(site).outcome, sms.QUEUED)

    def test_the_counts_do_not_come_back_as_a_cap_when_the_cache_is_empty(self):
        for _ in range(25):
            store_text(self.site)
        cache.clear()
        self.assertEqual(store_text(self.site).outcome, sms.QUEUED)

    def test_an_own_phone_store_text_does_not_take_the_platform_slot(self):
        self.give_own_phone(self.site)
        set_policy(platform_daily_limit=302)  # room for two staff texts after the 300 kept for codes
        for _ in range(5):
            self.assertEqual(store_text(self.site).outcome, sms.QUEUED)
        self.assertIsNone(cache.get(sms._platform_key()))
        # the platform room is untouched: a store on the platform phone still gets both its slots
        self.assertEqual(store_text(self.other).outcome, sms.QUEUED)
        self.assertEqual(store_text(self.other).outcome, sms.QUEUED)

    def test_own_phone_store_texts_are_not_counted_against_the_platform_when_the_cache_is_empty(self):
        self.give_own_phone(self.site)
        set_policy(platform_daily_limit=302)
        for _ in range(5):
            store_text(self.site)
        cache.clear()
        self.assertEqual(sms.platform_count(), 0)
        self.assertEqual(store_text(self.other).outcome, sms.QUEUED)
        self.assertEqual(store_text(self.other).outcome, sms.QUEUED)
        self.assert_refused(self.other, "staff_limit")

    def test_a_platform_phone_store_text_is_still_refused_once_the_platform_room_is_used(self):
        set_policy(platform_daily_limit=302)
        self.assertEqual(store_text(self.site).outcome, sms.QUEUED)
        self.assertEqual(store_text(self.other).outcome, sms.QUEUED)
        self.assert_refused(self.site, "staff_limit")
        self.assertEqual(sms.platform_count(), 2)
        for _ in range(3):
            self.assertEqual(code().outcome, sms.QUEUED)

    def test_an_own_phone_store_is_not_refused_when_the_platform_room_is_used(self):
        self.give_own_phone(self.site)
        set_policy(platform_daily_limit=300)  # nothing left for staff texts on the platform phone
        self.assert_refused(self.other, "staff_limit")
        self.assertEqual(store_text(self.site).outcome, sms.QUEUED)

    def test_the_refusal_order(self):
        set_policy(sending_stopped=True, platform_daily_limit=300)
        self.assert_refused(self.site, "paused")
        set_policy(sending_stopped=False)
        self.assert_refused(self.site, "staff_limit")
        set_policy(platform_daily_limit=2000)
        SmsDevice.objects.update(last_contact_at=timezone.now() - timedelta(minutes=5))
        self.assert_refused(self.site, "gateway_offline")
        self.assertEqual(sms.store_used_today(self.site), 0)

    def test_an_own_phone_that_is_offline_is_refused_as_that_phone(self):
        phone = self.give_own_phone(self.site)
        SmsDevice.objects.filter(pk=phone.pk).update(last_contact_at=timezone.now() - timedelta(minutes=5))
        self.assert_refused(self.site, "site_phone_offline")

    def test_the_order_status_cap_is_not_the_store_cap(self):
        set_policy(site_daily_limit=0)
        self.assertEqual(store_text(self.site).outcome, sms.QUEUED)

    def test_status_for_the_composer_reports_the_phone_and_no_limits(self):
        store_text(self.site)
        self.assertEqual(
            services.sms_store_status(self.site),
            {"phone_online": True, "daily_limit": None, "left_today": None, "hourly_limit": None, "left_this_hour": None},
        )

    def test_status_with_the_gateway_off_reads_no_table(self):
        with self.settings(SMS_GATEWAY_ENABLED=False):
            with CaptureQueriesContext(connection) as queries:
                status = services.sms_store_status(self.site)
        self.assertFalse(status["phone_online"])
        self.assertEqual(len(queries), 0)


class CodesStayFirstTests(SmsTestCase):
    def test_a_code_is_handed_out_before_a_store_text_even_if_the_store_text_is_older(self):
        phone = online_phone()
        site = make_site(make_account())
        store_text(site)
        code()
        first = gateway._waiting(phone, timezone.now())
        self.assertEqual([m.purpose for m in first], ["mobile_verify", "store_reply"])


class PolicyColumnsTests(SmsTestCase):
    def operator(self):
        return get_user_model().objects.create_user("hq", password="pw", is_staff=True)

    def test_the_store_caps_are_audited_and_validated(self):
        operator = self.operator()
        row = services.change_policy(operator, {
            "sending_stopped": False, "platform_daily_limit": 2000, "site_daily_limit": 2000,
            "store_daily_limit": 40, "all_stores_daily_limit": 800,
        })
        self.assertEqual((row.store_daily_limit, row.all_stores_daily_limit), (40, 800))
        entry = OperatorAuditEntry.objects.get(action="sms_policy_change")
        self.assertEqual(entry.detail["after"], {"store_daily_limit": 40, "all_stores_daily_limit": 800})
        for bad in (-1, "5", 1.5, True, None, 2147483648):
            with self.subTest(bad=bad), self.assertRaises(ApiError):
                services.change_policy(operator, {
                    "sending_stopped": False, "platform_daily_limit": 2000, "site_daily_limit": 2000,
                    "store_daily_limit": bad,
                })

    def test_an_older_body_with_the_store_fields_still_saves_and_enforces_nothing(self):
        row = services.change_policy(self.operator(), {
            "sending_stopped": False, "platform_daily_limit": 2000, "site_daily_limit": 2000,
            "store_daily_limit": 1, "all_stores_daily_limit": 1,
        })
        self.assertEqual((row.store_daily_limit, row.all_stores_daily_limit), (1, 1))
        online_phone()
        site = make_site(make_account())
        for _ in range(3):
            self.assertEqual(store_text(site).outcome, sms.QUEUED)

    def test_a_body_without_the_store_caps_keeps_them(self):
        set_policy(store_daily_limit=33)
        row = services.change_policy(self.operator(), {
            "sending_stopped": True, "platform_daily_limit": 100, "site_daily_limit": 100,
        })
        self.assertEqual((row.store_daily_limit, row.sending_stopped), (33, True))

    def test_before_the_migration_the_columns_are_not_read_or_saved(self):
        online_phone()
        sms.policy()  # the row exists on every environment that has sent a text
        with mock.patch.object(notification_models, "store_limit_columns_exist", return_value=False):
            with CaptureQueriesContext(connection) as queries:
                row = sms.policy()
                self.assertEqual((row.store_daily_limit, row.all_stores_daily_limit), (50, 1000))
            self.assertFalse([q for q in queries.captured_queries if "store_daily_limit" in q["sql"]])
            self.assertEqual(code().outcome, sms.QUEUED)
        with mock.patch.object(services, "store_limit_columns_exist", return_value=False), \
                mock.patch.object(services, "store_bulk_limit_column_exists", return_value=False):
            self.assertEqual(services.policy_fields(), services.POLICY_FIELDS)
            with CaptureQueriesContext(connection) as queries:
                services.change_policy(self.operator(), {
                    "sending_stopped": False, "platform_daily_limit": 10, "site_daily_limit": 10,
                    "store_daily_limit": 7,
                })
            self.assertFalse(
                [q for q in queries.captured_queries if q["sql"].startswith("UPDATE") and "store_daily_limit" in q["sql"]]
            )
