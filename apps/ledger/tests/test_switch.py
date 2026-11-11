import itertools
import os
import runpy
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from apps.ledger import switch

NAMES = ("LEDGER_ENABLED", "LEDGER_POST_BILLING_ENABLED", "LEDGER_LIVE_BOOKS")


def load_settings(**env):
    """Run config/settings.py with only the given ledger variables set."""
    clean = {k: v for k, v in os.environ.items() if k not in NAMES}
    clean.update(env)
    with mock.patch.dict("os.environ", clean, clear=True):
        return runpy.run_path("config/settings.py")


class SettingsTests(SimpleTestCase):
    def test_1_every_switch_is_off_without_its_variable(self):
        loaded = load_settings()
        for name in NAMES:
            self.assertIs(loaded[name], False, name)

    def test_1b_one_turns_on_padded_one_turns_on_other_words_do_not(self):
        for name in NAMES:
            self.assertIs(load_settings(**{name: "1"})[name], True)
            self.assertIs(load_settings(**{name: " 1 "})[name], True)
            self.assertIs(load_settings(**{name: "true"})[name], False)
            self.assertIs(load_settings(**{name: ""})[name], False)


class SwitchTests(SimpleTestCase):
    def test_2_post_billing_needs_the_ledger(self):
        for ledger, post in itertools.product([False, True], repeat=2):
            with override_settings(LEDGER_ENABLED=ledger, LEDGER_POST_BILLING_ENABLED=post):
                self.assertIs(switch.post_billing_on(), ledger and post)

    def test_3_live_books_needs_the_ledger(self):
        for ledger, live in itertools.product([False, True], repeat=2):
            with override_settings(LEDGER_ENABLED=ledger, LEDGER_LIVE_BOOKS=live):
                self.assertIs(switch.live_books_on(), ledger and live)

    def test_3b_ledger_on_follows_its_setting(self):
        with override_settings(LEDGER_ENABLED=True):
            self.assertIs(switch.ledger_on(), True)
        with override_settings(LEDGER_ENABLED=False):
            self.assertIs(switch.ledger_on(), False)


class TablesTests(TestCase):
    def test_4_tables_exist_is_true_after_migrate(self):
        self.assertIs(switch.tables_exist(), True)
