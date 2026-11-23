"""FD1: the default offer is `.com` only, `DOMAINS_ALLOWED_TLDS` still overrides it, the cap is untouched."""
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


def read(value):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOMAINS_") and k != "DATABASE_URL"}
    env["DJANGO_DEBUG"] = "true"
    if value is not None:
        env["DOMAINS_ALLOWED_TLDS"] = value
    out = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    tlds, cap = out.stdout.strip().splitlines()[-2:]
    return tlds, cap


class DefaultTldTests(SimpleTestCase):
    def test_unset_or_empty_offers_com_only(self):
        self.assertEqual(read(None)[0], "('com',)")
        self.assertEqual(read("  ")[0], "('com',)")

    def test_the_variable_overrides_the_default(self):
        self.assertEqual(read("net, .Org")[0], "('net', 'org')")

    def test_the_cost_cap_default_is_unchanged(self):
        self.assertEqual(read(None)[1], "0")
