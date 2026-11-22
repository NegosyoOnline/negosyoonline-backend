"""APP_ENVIRONMENT and its one-release fallback to the old RAILWAY_ENVIRONMENT_NAME variable.

Every guard that reads the environment name is driven through the same matrix: the variable that
names it (APP_ENVIRONMENT set, only the old variable set, neither set) crossed with the values
staging, production and empty. The settings module is reloaded under each environment, so the test
proves what the guards read from the real variables, not only from an override.
"""
import importlib
import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings

from apps.common.environment import app_environment
from apps.payments import fake_views, services
from apps.payments.checks import check_xendit_fake_is_local
from config import settings as cfg

# (how the name is supplied, the value it carries, the name the app must resolve)
CASES = [
    (source, value, value)
    for source in ("APP_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME")
    for value in ("staging", "production", "")
] + [("neither", "", "")]


def supplied(source, value):
    """The two variables, with the one not under test blank so the ambient shell never leaks in."""
    env = {"APP_ENVIRONMENT": "", "RAILWAY_ENVIRONMENT_NAME": ""}
    if source != "neither":
        env[source] = value
    return env


def settings_under(env, **extra):
    """Reload config.settings with the variables patched; return (APP_ENVIRONMENT, XENDIT_FAKE)."""
    with mock.patch.dict(os.environ, {**env, **extra}):
        reloaded = importlib.reload(cfg)
        return reloaded.APP_ENVIRONMENT, reloaded.XENDIT_FAKE


class ResolverTests(TestCase):
    def test_resolution_matrix(self):
        for source, value, expected in CASES:
            with self.subTest(source=source, value=value):
                self.assertEqual(app_environment(supplied(source, value)), expected)

    def test_value_is_stripped_and_lowercased(self):
        self.assertEqual(app_environment({"APP_ENVIRONMENT": "  Staging "}), "staging")
        self.assertEqual(app_environment({"RAILWAY_ENVIRONMENT_NAME": " PRODUCTION"}), "production")

    def test_app_environment_wins_over_the_old_variable(self):
        env = {"APP_ENVIRONMENT": "staging", "RAILWAY_ENVIRONMENT_NAME": "production"}
        self.assertEqual(app_environment(env), "staging")

    def test_blank_app_environment_falls_back_to_the_old_variable(self):
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                env = {"APP_ENVIRONMENT": blank, "RAILWAY_ENVIRONMENT_NAME": "production"}
                self.assertEqual(app_environment(env), "production")

    def test_nothing_set_is_empty(self):
        self.assertEqual(app_environment({}), "")


class GuardMatrixTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        importlib.reload(cfg)
        super().tearDownClass()

    def tearDown(self):
        importlib.reload(cfg)

    def resolved(self, source, value, **extra):
        name, fake = settings_under(supplied(source, value), **extra)
        self.assertEqual(name, value)
        return name, fake

    def test_xendit_key_choice(self):
        for source, value, _ in CASES:
            name, _fake = self.resolved(source, value)
            for debug in (False, True):
                with self.subTest(source=source, value=value, debug=debug), \
                        override_settings(APP_ENVIRONMENT=name, DEBUG=debug):
                    if value == "production":
                        expected = {services.LIVE: True, services.TEST: False}
                    elif value == "staging":
                        expected = {services.LIVE: False, services.TEST: True}
                    else:  # a local run: either key only while DEBUG is on
                        expected = {services.LIVE: debug, services.TEST: debug}
                    for mode, allowed in expected.items():
                        self.assertEqual(services.environment_allows(mode), allowed, mode)

    def test_fake_xendit_is_on_only_for_a_local_debug_run(self):
        for source, value, _ in CASES:
            for debug in ("true", "false"):
                with self.subTest(source=source, value=value, debug=debug):
                    _name, fake = self.resolved(source, value, XENDIT_FAKE="1", DJANGO_DEBUG=debug)
                    self.assertEqual(fake, debug == "true" and value == "")

    def test_fake_page_is_refused_on_a_server(self):
        for source, value, _ in CASES:
            name, _fake = self.resolved(source, value)
            with self.subTest(source=source, value=value), \
                    override_settings(APP_ENVIRONMENT=name, XENDIT_FAKE=True), \
                    mock.patch.object(fake_views.fake_xendit, "get", return_value=None) as get:
                with self.assertRaises(Http404):
                    fake_views.invoice(RequestFactory().get("/x"), "any")
                # The environment gate comes first: a server never looks the invoice up.
                self.assertEqual(get.called, value == "")

    def test_deploy_check_e002(self):
        for source, value, _ in CASES:
            name, _fake = self.resolved(source, value)
            for debug in (False, True):
                with self.subTest(source=source, value=value, debug=debug), \
                        mock.patch.dict(os.environ, {"XENDIT_FAKE": "1"}), \
                        override_settings(APP_ENVIRONMENT=name, DEBUG=debug):
                    ids = [e.id for e in check_xendit_fake_is_local(None)]
                    self.assertEqual(ids, [] if (debug and value == "") else ["payments.E002"])

    def test_delete_commands_refused_on_production(self):
        refusal = "staging and local only"
        for command in ("pack_delete_test_order", "shop_delete_test_order"):
            for source, value, _ in CASES:
                name, _fake = self.resolved(source, value)
                for debug in (False, True):
                    argv = ["--site", "site_none", "--order", "none"]
                    with self.subTest(command=command, source=source, value=value, debug=debug), \
                            override_settings(APP_ENVIRONMENT=name, DEBUG=debug):
                        allowed = value == "staging" or (debug and value != "production")
                        try:
                            call_command(command, *argv, stdout=StringIO())
                        except CommandError as error:
                            self.assertEqual(refusal in str(error), not allowed, str(error))
                        else:
                            self.fail("expected a CommandError")
