import time
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common import sentry
from apps.health import tasks, views
from apps.meta_ads.models import MetaDelivery
from apps.notifications.models import EmailMessage, SmsMessage


class ReadyzTests(TestCase):
    def setUp(self):
        cache.clear()
        # A fresh beat heartbeat, as a running worker leaves; without Redis the check is skipped (eager mode).
        cache.set(tasks.HEARTBEAT_KEY, timezone.now().timestamp(), 600)

    def get(self, **extra):
        return self.client.get("/readyz/", secure=True, **extra)

    def test_all_pass(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {"ok": True, "db": True, "cache": True, "beat": True, "sms_queue": True, "email": True,
             "files_backup": True, "meta_queue": True, "xendit_queue": True},
        )

    def test_db_failure_is_503(self):
        with mock.patch.object(views.connection, "cursor", side_effect=Exception("secret host")):
            response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["db"])
        self.assertFalse(response.json()["ok"])
        self.assertNotIn("secret", response.content.decode())

    def test_cache_failure_is_503(self):
        with mock.patch.object(views, "_cache_round_trip", side_effect=Exception("down")):
            response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["cache"])

    def test_cache_timeout_is_a_failure(self):
        def slow():
            time.sleep(0.5)
            return True

        with mock.patch.object(views, "CHECK_TIMEOUT", 0.05), mock.patch.object(
            views, "_cache_round_trip", slow
        ):
            self.assertFalse(views.check_cache())

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_beat_needs_a_fresh_heartbeat(self):
        cache.delete(tasks.HEARTBEAT_KEY)
        self.assertFalse(self.get().json()["beat"])
        tasks.heartbeat()
        self.assertTrue(self.get().json()["beat"])
        stale = (timezone.now() - timedelta(minutes=4)).timestamp()
        cache.set(tasks.HEARTBEAT_KEY, stale, 600)
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["beat"])

    def test_heartbeat_writes_the_cache(self):
        cache.delete(tasks.HEARTBEAT_KEY)
        self.assertIsNone(cache.get(tasks.HEARTBEAT_KEY))
        tasks.heartbeat()
        self.assertAlmostEqual(
            cache.get(tasks.HEARTBEAT_KEY), timezone.now().timestamp(), delta=5
        )

    def test_heartbeat_is_scheduled_every_minute(self):
        entry = settings.CELERY_BEAT_SCHEDULE["health-heartbeat"]
        self.assertEqual(entry["task"], "health.heartbeat")
        self.assertEqual(entry["schedule"], 60)

    def _queued(self, age):
        message = SmsMessage.objects.create(
            recipient="+639171234567",
            category="code",
            purpose="mobile_verify",
            priority=10,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        SmsMessage.objects.filter(pk=message.pk).update(created_at=timezone.now() - age)

    @override_settings(SMS_GATEWAY_ENABLED=True)
    def test_sms_queue_old_queued_row_fails_when_gateway_on(self):
        self._queued(timedelta(minutes=5))
        self.assertTrue(self.get().json()["sms_queue"])
        self._queued(timedelta(minutes=16))
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["sms_queue"])

    @override_settings(SMS_GATEWAY_ENABLED=True)
    def test_sms_queue_ignores_sent_rows(self):
        self._queued(timedelta(minutes=30))
        SmsMessage.objects.update(status="sent")
        self.assertTrue(self.get().json()["sms_queue"])

    @override_settings(SMS_GATEWAY_ENABLED=False)
    def test_sms_queue_not_counted_when_gateway_off(self):
        self._queued(timedelta(minutes=30))
        self.assertTrue(self.get().json()["sms_queue"])

    def _meta(self, outcome, age, n=1):
        from apps.billing.tests.base import make_account, make_site

        existing = MetaDelivery.objects.first()
        site = existing.site if existing else make_site(make_account("ana@example.test"), name="anas-store")
        for i in range(n):
            row = MetaDelivery.objects.create(
                site=site, event_name="Purchase", event_id=f"{outcome}-{age}-{i}", outcome=outcome,
            )
            MetaDelivery.objects.filter(pk=row.pk).update(created_at=timezone.now() - age)

    @override_settings(META_EVENTS_ENABLED=True)
    def test_meta_queue_no_rows_is_true(self):
        body = self.get().json()
        self.assertIn("meta_queue", body)
        self.assertTrue(body["meta_queue"])

    @override_settings(META_EVENTS_ENABLED=True)
    def test_meta_queue_fresh_queued_row_is_true(self):
        self._meta(MetaDelivery.QUEUED, timedelta(minutes=views.READYZ_META_QUEUE_MINUTES) - timedelta(minutes=1))
        self.assertTrue(self.get().json()["meta_queue"])

    @override_settings(META_EVENTS_ENABLED=True)
    def test_meta_queue_old_queued_row_fails_and_is_503(self):
        self._meta(MetaDelivery.QUEUED, timedelta(minutes=views.READYZ_META_QUEUE_MINUTES) + timedelta(minutes=1))
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["meta_queue"])

    @override_settings(META_EVENTS_ENABLED=True)
    def test_meta_queue_old_retrying_row_fails(self):
        self._meta(MetaDelivery.RETRYING, timedelta(minutes=views.READYZ_META_QUEUE_MINUTES) + timedelta(minutes=1))
        self.assertFalse(self.get().json()["meta_queue"])

    @override_settings(META_EVENTS_ENABLED=True)
    def test_meta_queue_old_finished_rows_are_true(self):
        old = timedelta(minutes=views.READYZ_META_QUEUE_MINUTES) + timedelta(hours=1)
        for outcome in (MetaDelivery.SENT, MetaDelivery.FAILED, MetaDelivery.EXPIRED):
            self._meta(outcome, old)
        self.assertTrue(self.get().json()["meta_queue"])

    @override_settings(META_EVENTS_ENABLED=False)
    def test_meta_queue_true_when_meta_disabled(self):
        self._meta(MetaDelivery.QUEUED, timedelta(minutes=views.READYZ_META_QUEUE_MINUTES) + timedelta(hours=1))
        self.assertTrue(self.get().json()["meta_queue"])

    def _emails(self, status, count, age=timedelta(minutes=1)):
        for _ in range(count):
            row = EmailMessage.objects.create(recipient="owner@example.com", category="x", status=status)
            EmailMessage.objects.filter(pk=row.pk).update(created_at=timezone.now() - age)

    def test_email_no_rows_is_ok(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["email"])

    def test_email_three_failed_none_sent_is_503_without_addresses(self):
        self._emails("failed", 3)
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["email"])
        self.assertNotIn(b"owner@example.com", response.content)

    def test_email_one_sent_keeps_it_ok(self):
        self._emails("failed", 3)
        self._emails("sent", 1)
        self.assertEqual(self.get().status_code, 200)

    def test_email_below_threshold_is_ok(self):
        self._emails("failed", 2)
        self.assertEqual(self.get().status_code, 200)

    def test_email_failures_older_than_the_window_are_ignored(self):
        self._emails("failed", 3, age=timedelta(minutes=31))
        self.assertEqual(self.get().status_code, 200)

    def test_files_backup_off_is_true_and_calls_nothing(self):
        with mock.patch.object(views, "_files_backup_fresh") as fresh:
            self.assertTrue(self.get().json()["files_backup"])
        fresh.assert_not_called()

    @override_settings(FILE_BACKUP_ENABLED=True, FILE_BACKUP_STALE_HOURS=36)
    def test_files_backup_on_flips_the_status_when_stale_or_never_run(self):
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["files_backup"])
        cache.set("file_backup:last_ok", timezone.now().timestamp(), None)
        self.assertEqual(self.get().status_code, 200)
        cache.set("file_backup:last_ok", (timezone.now() - timedelta(hours=37)).timestamp(), None)
        self.assertEqual(self.get().status_code, 503)

    def test_email_pending_counts_neither_way(self):
        self._emails("failed", 3)
        self._emails("pending", 5)
        self.assertEqual(self.get().status_code, 503)
        EmailMessage.objects.filter(status="failed").delete()
        self._emails("failed", 2)
        self.assertEqual(self.get().status_code, 200)

    @override_settings(READYZ_EMAIL_FAILURES=5)
    def test_email_threshold_is_a_setting(self):
        self._emails("failed", 3)
        self.assertEqual(self.get().status_code, 200)

    def test_email_check_is_one_query(self):
        self._emails("failed", 3)
        with self.assertNumQueries(1):
            self.assertFalse(views.check_email())

    def test_email_head_answers_like_get(self):
        self._emails("failed", 3)
        self.assertEqual(self.client.head("/readyz/", secure=True).status_code, 503)

    def test_email_query_error_is_a_failure(self):
        with mock.patch.object(EmailMessage.objects, "order_by", side_effect=Exception("boom")):
            response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["email"])

    def test_throttled_per_ip(self):
        with mock.patch.object(views, "READYZ_LIMIT", (2, 60)):
            self.assertEqual(self.get(HTTP_X_FORWARDED_FOR="9.9.9.9").status_code, 200)
            self.assertEqual(self.get(HTTP_X_FORWARDED_FOR="9.9.9.9").status_code, 200)
            self.assertEqual(self.get(HTTP_X_FORWARDED_FOR="9.9.9.9").status_code, 429)
            self.assertEqual(self.get(HTTP_X_FORWARDED_FOR="8.8.8.8").status_code, 200)

    def test_head_matches_get_status(self):
        self.assertEqual(self.client.head("/readyz/", secure=True).status_code, 200)
        with mock.patch.object(views.connection, "cursor", side_effect=Exception("secret host")):
            self.assertEqual(self.client.head("/readyz/", secure=True).status_code, 503)

    def test_head_counts_against_the_same_throttle(self):
        with mock.patch.object(views, "READYZ_LIMIT", (2, 60)):
            self.assertEqual(self.get(HTTP_X_FORWARDED_FOR="9.9.9.9").status_code, 200)
            self.assertEqual(self.client.head("/readyz/", secure=True, HTTP_X_FORWARDED_FOR="9.9.9.9").status_code, 200)
            self.assertEqual(self.client.head("/readyz/", secure=True, HTTP_X_FORWARDED_FOR="9.9.9.9").status_code, 429)

    def test_post_not_allowed_and_healthz_unchanged(self):
        self.assertEqual(self.client.post("/readyz/", secure=True).status_code, 405)
        response = self.client.get("/healthz/", secure=True)
        self.assertEqual(set(response.json()), {"status", "commit"})


class SentryTests(TestCase):
    def test_no_dsn_does_nothing(self):
        with mock.patch.dict("os.environ", {"SENTRY_DSN": ""}):
            with mock.patch("sentry_sdk.init") as init:
                self.assertFalse(sentry.init())
        init.assert_not_called()

    def test_dsn_starts_the_sdk_errors_only_without_pii(self):
        with mock.patch("sentry_sdk.init") as init:
            self.assertTrue(sentry.init("https://key@example.invalid/1"))
        kwargs = init.call_args.kwargs
        self.assertFalse(kwargs["send_default_pii"])
        self.assertEqual(kwargs["traces_sample_rate"], 0)
        self.assertFalse(kwargs["include_local_variables"])
        self.assertIs(kwargs["before_send"], sentry.before_send)

    def test_before_send_strips_and_masks(self):
        event = {
            "user": {"email": "a@b.com", "ip_address": "1.2.3.4"},
            "request": {
                "method": "POST",
                "url": "https://api.example.com/v1/x?token=abc#f",
                "headers": {"Authorization": "Bearer x", "Cookie": "s=1"},
                "cookies": {"s": "1"},
                "data": {"mobile": "09171234567"},
                "query_string": "token=abc",
            },
            "message": "failed for ana@example.com at +639171234567 and 09171234567",
            "exception": {"values": [{"value": "bad 639171234567 juan.d@mail.ph"}]},
        }
        out = sentry.before_send(event, {})
        self.assertNotIn("user", out)
        self.assertEqual(
            out["request"], {"method": "POST", "url": "https://api.example.com/v1/x"}
        )
        text = str(out)
        for leaked in ("ana@", "juan.d", "9171234567", "Bearer", "abc"):
            self.assertNotIn(leaked, text)
        self.assertIn("[masked]", out["message"])

    def test_before_send_drops_frame_variables(self):
        event = {"exception": {"values": [{"value": "x", "stacktrace": {"frames": [
            {"function": "f", "vars": {"code": "123456", "token": "abc"}},
            {"function": "g", "vars": {"name": "Ana"}},
        ]}}]}}
        out = sentry.before_send(event, {})
        frames = out["exception"]["values"][0]["stacktrace"]["frames"]
        self.assertTrue(all("vars" not in frame for frame in frames))
        self.assertNotIn("123456", str(out))

    def test_before_send_drops_celery_task_arguments_keeps_name(self):
        event = {"contexts": {"celery-job": {
            "task_name": "billing.notices", "args": ["+639171234567"], "kwargs": {"code": "654321"},
        }}}
        job = sentry.before_send(event, {})["contexts"]["celery-job"]
        self.assertEqual(job, {"task_name": "billing.notices"})
