"""FD1 (price decides): the default sentinel list, the unset cap (0), and the overrides."""
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
CODE = (
    "import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'\n"
    "from django.conf import settings\n"
    "print(repr(settings.DOMAINS_ALLOWED_TLDS)); print(settings.DOMAINS_MAX_COST_CENTS)"
)


DEFAULT = "('com', 'net', 'org', 'store', 'shop', 'online', 'site', 'xyz')"


def read(value, cap=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOMAINS_") and k != "DATABASE_URL"}
    env["DJANGO_DEBUG"] = "true"
    if value is not None:
        env["DOMAINS_ALLOWED_TLDS"] = value
    if cap is not None:
        env["DOMAINS_MAX_COST_USD"] = cap
    out = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    tlds, cost = out.stdout.strip().splitlines()[-2:]
    return tlds, cost


class DefaultTldTests(SimpleTestCase):
    def test_unset_or_empty_reads_the_default_list(self):
        self.assertEqual(read(None)[0], DEFAULT)
        self.assertEqual(read("  ")[0], DEFAULT)

    def test_the_variable_overrides_the_default(self):
        self.assertEqual(read("net, .Org")[0], "('net', 'org')")

    def test_the_cost_cap_unset_is_zero(self):
        self.assertEqual(read(None)[1], "0")
        self.assertEqual(read(None, cap="  ")[1], "0")

    def test_a_set_cost_cap_overrides_the_default(self):
        self.assertEqual(read(None, cap="12.00")[1], "1200")
        self.assertEqual(read(None, cap="0")[1], "0")  # an explicit 0 still refuses everything
