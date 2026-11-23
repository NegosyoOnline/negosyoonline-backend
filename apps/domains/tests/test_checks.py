import io

from django.core.checks import run_checks
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.domains import checks, names
from apps.domains.tests.base import PLATFORM, PSL


def ids(found):
    return {m.id for m in found}


class DomainsChecksTests(SimpleTestCase):
    def run_it(self, **settings_):
        names._cache.update(key=None, rules=None)
        with override_settings(**settings_):
            return checks.check_domains(None)

    def test_missing_suffix_list_warns_even_when_off(self):
        self.assertEqual(ids(self.run_it(DOMAINS_PSL_FILE="/nonexistent.dat", DOMAINS_ENABLED=False)), {"domains.W001"})

    def test_with_the_list_and_the_switch_off_there_is_nothing(self):
        self.assertEqual(self.run_it(DOMAINS_PSL_FILE=PSL, DOMAINS_ENABLED=False), [])

    def test_real_mode_names_what_is_missing_and_never_a_value(self):
        found = self.run_it(
            DOMAINS_PSL_FILE=PSL, DOMAINS_ENABLED=True, DOMAINS_PROVIDER="real", DOMAINS_PLATFORM_ZONES=(),
            CLOUDFLARE_DNS_API_TOKEN="", VERCEL_API_TOKEN="a-real-looking-token", VERCEL_STOREFRONTS_PROJECT_ID="",
            CLOUDFLARE_ACCOUNT_ID="",
        )
        self.assertEqual(ids(found), {"domains.W002", "domains.W003"})
        text = " ".join(m.msg for m in found)
        self.assertIn("CLOUDFLARE_DNS_API_TOKEN", text)
        self.assertNotIn("VERCEL_API_TOKEN", text.replace("VERCEL_STOREFRONTS", ""))
        self.assertNotIn("a-real-looking-token", text)

    def test_real_mode_complete_is_quiet(self):
        self.assertEqual(self.run_it(
            DOMAINS_PSL_FILE=PSL, DOMAINS_ENABLED=True, DOMAINS_PROVIDER="real", DOMAINS_PLATFORM_ZONES=PLATFORM,
            CLOUDFLARE_DNS_API_TOKEN="t", VERCEL_API_TOKEN="t", VERCEL_STOREFRONTS_PROJECT_ID="p", CLOUDFLARE_ACCOUNT_ID="a",
        ), [])

    def test_purchase_on_names_the_unset_money_settings_and_never_a_value(self):
        found = self.run_it(
            DOMAINS_PSL_FILE=PSL, DOMAINS_ENABLED=True, DOMAINS_PURCHASE_ENABLED=True,
            CLOUDFLARE_REGISTRAR_API_TOKEN="a-registrar-secret", DOMAINS_MAX_COST_CENTS=0,
            DOMAINS_DAILY_PURCHASE_CAP=3, DOMAINS_DAILY_SPEND_CAP_CENTS=0, DOMAINS_ALLOWED_TLDS=(),
        )
        self.assertEqual(ids(found), {"domains.W004"})
        text = found[0].msg
        for name in ("DOMAINS_MAX_COST_USD", "DOMAINS_DAILY_SPEND_CAP_USD", "DOMAINS_ALLOWED_TLDS"):
            self.assertIn(name, text)
        for name in ("CLOUDFLARE_REGISTRAR_API_TOKEN", "DOMAINS_DAILY_PURCHASE_CAP", "a-registrar-secret"):
            self.assertNotIn(name, text)

    def test_purchase_off_is_quiet_whatever_the_money_settings_hold(self):
        self.assertEqual(self.run_it(
            DOMAINS_PSL_FILE=PSL, DOMAINS_ENABLED=True, DOMAINS_PURCHASE_ENABLED=False, DOMAINS_MAX_COST_CENTS=0,
        ), [])

    def test_the_fake_provider_without_debug_is_an_error(self):
        with override_settings(DOMAINS_PROVIDER="fake", DEBUG=False):
            self.assertEqual(ids(checks.check_domains_fake(None)), {"domains.E001"})
        with override_settings(DOMAINS_PROVIDER="fake", DEBUG=True):
            self.assertEqual(checks.check_domains_fake(None), [])

    def test_the_checks_are_registered(self):
        found = run_checks(include_deployment_checks=True)
        self.assertIn("domains.W001", ids(found)) if not names.psl_available() else None


class MigrationTests(TestCase):
    def test_makemigrations_is_clean(self):
        out = io.StringIO()
        call_command("makemigrations", "--check", "--dry-run", stdout=out)

    def test_domains_has_exactly_one_migration(self):
        from pathlib import Path
        folder = Path(__file__).resolve().parent.parent / "migrations"
        self.assertEqual(sorted(p.name for p in folder.glob("0*.py")), ["0001_initial.py"])
