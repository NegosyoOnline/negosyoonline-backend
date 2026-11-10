from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common import retention
from apps.operators.models import OperatorAuditEntry

BUILD_7_RULES = {
    "expired_subscriber_sessions",
    "expired_admin_sign_in_codes",
    "expired_admin_sessions",
    "expired_django_sessions",
    "email_messages",
}


class RegistryTestCase(TestCase):
    """Runs against a registry that holds only the rules a test adds."""

    def setUp(self):
        patcher = mock.patch.dict(retention._rules, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def rule(self, name, count=3, switched=False, fails=False):
        def fn(now, dry_run):
            self.calls.append((name, dry_run))
            if fails:
                raise RuntimeError("boom")
            return count

        retention.register(name, retention.behind_switch(fn) if switched else fn)

    def log(self):
        return OperatorAuditEntry.objects.filter(action="retention_run")


class RegistryTests(RegistryTestCase):
    def test_a_name_is_registered_once(self):
        self.rule("one")
        with self.assertRaises(ValueError):
            self.rule("one")

    def test_a_rule_that_is_not_registered_cannot_run(self):
        self.rule("one")
        with self.assertRaisesMessage(ValueError, "No such retention rule: two."):
            retention.run_all(only=["two"])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.log().exists())

    def test_only_runs_the_named_rule_alone(self):
        self.rule("one")
        self.rule("two")
        report = retention.run_all(only=["two"])
        self.assertEqual(report.counts, {"two": 3})
        self.assertEqual(self.calls, [("two", False)])

    def test_a_run_writes_one_log_row_with_the_counts_and_no_operator(self):
        self.rule("one", count=2)
        self.rule("two", count=0)
        retention.run_all()
        entry = self.log().get()
        self.assertIsNone(entry.operator)
        self.assertEqual(
            entry.detail, {"counts": {"one": 2, "two": 0}, "switched_off": [], "errors": {}}
        )
        self.assertEqual(entry.personal, {})

    def test_a_dry_run_passes_dry_run_on_and_writes_no_log(self):
        self.rule("one")
        report = retention.run_all(dry_run=True)
        self.assertEqual(report.counts, {"one": 3})
        self.assertEqual(self.calls, [("one", True)])
        self.assertFalse(self.log().exists())

    def test_a_failing_rule_is_logged_by_name_and_the_others_still_run(self):
        self.rule("bad", fails=True)
        self.rule("good")
        with self.assertLogs("apps.common.retention", "ERROR"):
            report = retention.run_all()
        self.assertEqual(report.counts, {"good": 3})
        self.assertEqual(report.errors, {"bad": "RuntimeError"})
        self.assertEqual(self.log().get().detail["errors"], {"bad": "RuntimeError"})

    def test_a_failing_rule_keeps_none_of_its_own_changes(self):
        def half_done(now, dry_run):
            OperatorAuditEntry.objects.create(action="half")
            raise RuntimeError("boom")

        retention.register("half", half_done)
        with self.assertLogs("apps.common.retention", "ERROR"):
            retention.run_all()
        self.assertFalse(OperatorAuditEntry.objects.filter(action="half").exists())


class SwitchTests(RegistryTestCase):
    def test_the_switch_is_off_by_default_and_only_1_turns_it_on(self):
        import os
        import subprocess
        import sys

        def read(value):
            env = {k: v for k, v in os.environ.items() if k != "RETENTION_ENABLED"}
            if value is not None:
                env["RETENTION_ENABLED"] = value
            code = (
                "import django; django.setup(); from django.conf import settings; "
                "print(settings.RETENTION_ENABLED)"
            )
            env["DJANGO_SETTINGS_MODULE"] = "config.settings"
            return subprocess.run(
                [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
            ).stdout.strip()

        self.assertEqual(read(None), "False")
        self.assertEqual(read("true"), "False")
        self.assertEqual(read("0"), "False")
        self.assertEqual(read("1"), "True")

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_a_rule_behind_the_switch_is_not_run(self):
        self.rule("ours", switched=True)
        self.rule("theirs")
        report = retention.run_all()  # as an automatic start would call it
        self.assertEqual(report.counts, {"theirs": 3})
        self.assertEqual(report.switched_off, ["ours"])
        self.assertEqual(self.calls, [("theirs", False)])
        self.assertEqual(self.log().get().detail["switched_off"], ["ours"])

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_the_rule_itself_refuses_even_when_called_directly(self):
        self.rule("ours", switched=True)
        self.assertEqual(retention._rules["ours"](timezone.now(), False), 0)
        self.assertEqual(self.calls, [])

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_the_dry_run_still_counts(self):
        self.rule("ours", switched=True)
        self.assertEqual(retention.run_all(dry_run=True).counts, {"ours": 3})

    @override_settings(RETENTION_ENABLED=True)
    def test_switched_on_the_rule_runs(self):
        self.rule("ours", switched=True)
        self.assertEqual(retention.run_all().counts, {"ours": 3})


class RegisteredRulesTests(TestCase):
    def test_every_rule_of_this_build_is_registered_behind_the_switch(self):
        self.assertLessEqual(BUILD_7_RULES, set(retention.names()))
        for name in BUILD_7_RULES:
            self.assertTrue(getattr(retention._rules[name], "behind_switch", False), name)

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_a_real_run_reports_every_rule_of_this_build_as_off(self):
        report = retention.run_all(only=sorted(BUILD_7_RULES))
        self.assertEqual(report.counts, {})
        self.assertEqual(set(report.switched_off), BUILD_7_RULES)


class CommandTests(RegistryTestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command("run_retention", *args, stdout=out)
        return out.getvalue()

    def test_the_dry_run_prints_the_counts_and_writes_nothing(self):
        self.rule("one", count=4)
        out = self.run_command("--dry-run")
        self.assertIn("Dry run: nothing was changed.", out)
        self.assertIn("one: 4", out)
        self.assertEqual(self.calls, [("one", True)])
        self.assertFalse(self.log().exists())

    def test_a_run_prints_the_counts_and_writes_the_log(self):
        self.rule("one", count=4)
        self.assertIn("one: 4", self.run_command())
        self.assertEqual(self.log().count(), 1)

    @override_settings(RETENTION_ENABLED=False)
    def test_a_rule_held_back_by_the_switch_is_printed_as_switched_off(self):
        self.rule("ours", switched=True)
        self.assertIn("ours: switched off", self.run_command())

    def test_only_takes_one_rule_and_refuses_an_unknown_name(self):
        self.rule("one")
        self.rule("two")
        self.assertNotIn("one:", self.run_command("--only", "two"))
        with self.assertRaisesMessage(CommandError, "No such retention rule: three."):
            self.run_command("--only", "three")

    def test_a_failed_rule_ends_the_command_with_an_error(self):
        self.rule("bad", fails=True)
        with self.assertLogs("apps.common.retention", "ERROR"):
            with self.assertRaisesMessage(CommandError, "One or more rules failed."):
                self.run_command()
