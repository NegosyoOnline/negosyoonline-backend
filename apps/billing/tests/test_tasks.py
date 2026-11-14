"""The task queue: the Celery app, its one scheduled job, and no Railway config file in the repository.

No test here needs a broker or a running worker: the task is run in-process.
"""
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from apps.billing import tasks
from apps.billing.models import BillingCycle, Subscription, SubscriptionTransition
from apps.billing.tests.base import (
    CYCLE_1_END, TRIAL_END, days, fresh, make_attention, make_subscription,
)
from config import celery_app
from config.celery import broker_url

ROOT = Path(settings.BASE_DIR)
State = Subscription.State


def rows():
    return (
        list(Subscription.objects.order_by("pk").values()),
        SubscriptionTransition.objects.count(),
        BillingCycle.objects.count(),
    )


def run_at(moment):
    """Run the task in-process with the billing clock at `moment`."""
    with mock.patch("apps.billing.services.timezone") as clock:
        clock.now.return_value = moment
        outcome = tasks.sweep.apply()
    assert outcome.successful(), outcome.traceback
    return outcome.result


@override_settings(BILLING_ENABLED=True)
class SweepTaskTests(TestCase):
    def test_it_moves_every_due_subscription_and_a_second_run_changes_nothing(self):
        ended = make_subscription()
        late = make_attention(email="ben@example.test", name="bens-store")
        running = make_subscription(
            email="cora@example.test", name="coras-store", start=CYCLE_1_END
        )

        with self.assertLogs("apps.billing.services", level="INFO"):
            result = run_at(CYCLE_1_END + days(7))

        self.assertEqual(result, {"suspended_after_attention": 1, "trial_ended_no_method": 1})
        self.assertEqual(
            [fresh(row).state for row in (ended, late, running)],
            [State.CANCELLED_NO_PAYMENT_METHOD, State.SUSPENDED, State.TRIAL_ACTIVE],
        )
        after = rows()
        with self.assertLogs("apps.billing.services", level="INFO"):
            self.assertEqual(run_at(CYCLE_1_END + days(7)), {})
        self.assertEqual(rows(), after)

    def test_the_effective_time_is_the_rules_time_not_the_run_time(self):
        subscription = make_subscription()

        with self.assertLogs("apps.billing.services", level="INFO"):
            run_at(TRIAL_END + days(3))

        self.assertEqual(fresh(subscription).service_ended_at, TRIAL_END)

    def test_with_the_switch_off_it_reads_and_writes_nothing(self):
        make_subscription()
        before = rows()

        with override_settings(BILLING_ENABLED=False), self.assertNumQueries(0):
            self.assertEqual(run_at(TRIAL_END + days(3)), {})

        self.assertEqual(rows(), before)

    def test_one_subscription_failing_does_not_stop_the_others_or_the_task(self):
        first = make_subscription()
        second = make_subscription(email="ben@example.test", name="bens-store")
        real = tasks.services.catch_up

        def fail_on_first(subscription, now, **kwargs):
            if subscription.pk == first.pk:
                raise RuntimeError("boom")
            return real(subscription, now, **kwargs)

        with mock.patch.object(tasks.services, "catch_up", side_effect=fail_on_first):
            with self.assertLogs("apps.billing.services", level="ERROR"):
                result = run_at(TRIAL_END)

        self.assertEqual(result, {"failed": 1, "trial_ended_no_method": 1})
        self.assertEqual(
            [fresh(first).state, fresh(second).state],
            [State.TRIAL_ACTIVE, State.CANCELLED_NO_PAYMENT_METHOD],
        )

    def test_the_result_is_counts_only(self):
        make_subscription()

        with self.assertLogs("apps.billing.services", level="INFO") as logs:
            result = run_at(TRIAL_END)

        self.assertEqual(result, {"trial_ended_no_method": 1})
        self.assertNotIn("ana@example.test", " ".join(logs.output))
        self.assertNotIn("anas-store", " ".join(logs.output))


class QueueSetupTests(SimpleTestCase):
    def test_the_task_is_registered_under_its_name(self):
        celery_app.loader.import_default_modules()

        self.assertEqual(celery_app.tasks["billing.sweep"].name, tasks.sweep.name)
        self.assertEqual(
            sorted(name for name in celery_app.tasks if not name.startswith("celery.")),
            [
                "billing.notices", "billing.sweep", "file_backup.run", "health.heartbeat", "invoices.daily_alert",
                "invoices.ensure_items", "lifecycle.run_approved", "meta_ads.send_order_event", "meta_ads.send_page_event", "meta_ads.sweep",
                "pack.buyer_notice", "pack.purge", "pack.registered_notice", "retention.run", "statements.deliver", "statements.ensure",
            ],
        )
        self.assertTrue(tasks.sweep.ignore_result)

    def test_the_schedule_is_the_sweep_every_5_minutes_and_nothing_else(self):
        schedule = celery_app.conf.beat_schedule

        # The B5 safety nets (register items, statements) run beside it, on the same clock.
        self.assertEqual(
            list(schedule),
            [
                "billing-sweep", "invoices-ensure-items", "invoices-daily-alert",
                "statements-ensure", "statements-deliver", "meta-ads-sweep",
                "billing-notices", "retention-daily", "file-backup-daily", "pack-purge", "pack-buyer-notice",
                "pack-registered-notice",
                "lifecycle-run-approved", "health-heartbeat",
            ],
        )
        self.assertEqual(schedule["billing-notices"]["task"], "billing.notices")
        self.assertEqual(schedule["billing-notices"]["schedule"], 300)
        self.assertEqual(schedule["billing-sweep"]["task"], tasks.sweep.name)
        self.assertEqual(schedule["billing-sweep"]["schedule"], 300)
        # A queued sweep nobody picked up is dropped when the next one is due.
        self.assertEqual(schedule["billing-sweep"]["options"], {"expires": 300})

    def test_there_is_no_result_store_and_only_json_is_accepted(self):
        self.assertIsNone(celery_app.conf.result_backend)
        self.assertTrue(celery_app.conf.task_ignore_result)
        self.assertEqual(list(celery_app.conf.accept_content), ["json"])
        self.assertEqual(celery_app.conf.task_serializer, "json")

    def test_with_no_redis_a_task_runs_in_process_and_with_redis_it_is_queued(self):
        if settings.REDIS_URL:
            self.assertFalse(celery_app.conf.task_always_eager)
            self.assertEqual(celery_app.conf.broker_url, broker_url(settings.REDIS_URL))
            self.assertNotEqual(
                celery_app.conf.broker_url, settings.CACHES["default"]["LOCATION"]
            )
        else:
            self.assertTrue(celery_app.conf.task_always_eager)
            self.assertTrue(celery_app.conf.task_eager_propagates)
            self.assertEqual(celery_app.conf.broker_url, "memory://")

    def test_the_broker_is_the_same_redis_server_on_the_next_database_number(self):
        for redis_url, expected in (
            ("redis://127.0.0.1:6379/0", "redis://127.0.0.1:6379/1"),
            ("redis://127.0.0.1:6379", "redis://127.0.0.1:6379/1"),
            ("redis://127.0.0.1:6379/", "redis://127.0.0.1:6379/1"),
            ("redis://127.0.0.1:6379/3", "redis://127.0.0.1:6379/4"),
            ("redis://default:pw@redis.internal:6379", "redis://default:pw@redis.internal:6379/1"),
            ("rediss://default:pw@redis.internal:6380/0?ssl_cert_reqs=required",
             "rediss://default:pw@redis.internal:6380/1?ssl_cert_reqs=required"),
            ("redis://127.0.0.1:6379/cache", "redis://127.0.0.1:6379/1"),
        ):
            with self.subTest(redis_url=redis_url):
                self.assertEqual(broker_url(redis_url), expected)
                self.assertNotEqual(broker_url(redis_url), redis_url)


class RailwayFilesTests(SimpleTestCase):
    """Each service's settings live in its Railway Settings; docs/runbooks/billing.md records them."""

    def test_no_railway_config_file_is_in_the_repository_root(self):
        found = sorted(
            path.name
            for pattern in ("railway.toml", "railway.json", "railway.*.toml", "railway.*.json")
            for path in ROOT.glob(pattern)
        )

        self.assertEqual(found, [])
