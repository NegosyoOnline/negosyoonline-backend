from unittest import mock

from celery.schedules import crontab
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.common import retention, tasks
from apps.common.tests.test_retention import RegistryTestCase


class RetentionScheduleTests(TestCase):
    def test_the_beat_entry_runs_the_task_at_three_manila_time_with_an_hour_expiry(self):
        entry = settings.CELERY_BEAT_SCHEDULE["retention-daily"]
        self.assertEqual(entry["task"], "retention.run")
        self.assertEqual(entry["schedule"], crontab(hour=3, minute=0))
        self.assertEqual(entry["options"], {"expires": 3600})
        self.assertEqual(settings.TIME_ZONE, "Asia/Manila")

    def test_the_worker_knows_the_task(self):
        from config.celery import app

        app.loader.import_default_modules()
        self.assertIn("retention.run", app.tasks)


class RetentionTaskTests(RegistryTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_it_runs_every_rule_and_writes_one_log_entry(self):
        self.rule("one", count=2)
        self.rule("two", count=5)
        result = tasks.run()
        self.assertEqual(result["counts"], {"one": 2, "two": 5})
        self.assertEqual([c[0] for c in self.calls], ["one", "two"])
        self.assertEqual(self.log().count(), 1)

    def test_a_second_call_the_same_day_does_nothing(self):
        self.rule("one")
        tasks.run()
        result = tasks.run()
        self.assertEqual(result, {"skipped": "already ran today"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.log().count(), 1)

    def test_the_next_manila_day_runs_again(self):
        self.rule("one")
        tasks.run()
        with mock.patch("apps.common.tasks.timezone.localdate") as localdate:
            import datetime

            localdate.return_value = datetime.date(2099, 1, 1)
            tasks.run()
        self.assertEqual(len(self.calls), 2)

    @override_settings(RETENTION_ENABLED=False)
    def test_with_the_switch_off_the_guarded_rules_touch_nothing_and_are_named(self):
        self.rule("guarded", switched=True)
        self.rule("free")
        result = tasks.run()
        self.assertEqual(result["switched_off"], ["guarded"])
        self.assertEqual(result["counts"], {"free": 3})
        self.assertEqual(self.calls, [("free", False)])
        entry = self.log().get()
        self.assertEqual(entry.detail["switched_off"], ["guarded"])

    def test_a_failing_rule_still_writes_the_entry_with_its_name(self):
        self.rule("bad", fails=True)
        self.rule("good")
        result = tasks.run()
        self.assertEqual(result["errors"], {"bad": "RuntimeError"})
        self.assertEqual(result["counts"], {"good": 3})
        self.assertEqual(self.log().get().detail["errors"], {"bad": "RuntimeError"})


class RealRegistryTests(TestCase):
    @override_settings(RETENTION_ENABLED=False)
    def test_the_real_rules_run_with_the_switch_off_and_write_one_entry(self):
        cache.clear()
        self.addCleanup(cache.clear)
        result = tasks.run()
        self.assertEqual(len(result["switched_off"]) + len(result["counts"]), len(retention.names()))
        self.assertEqual(result["errors"], {})
