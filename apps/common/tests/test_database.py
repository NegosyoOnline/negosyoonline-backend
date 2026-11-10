import json
import os
import subprocess
import sys

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from apps.common.database import require_database_tls

POSTGRES = "django.db.backends.postgresql"


def database(host="db.internal.example", engine=POSTGRES, **options):
    return {"ENGINE": engine, "HOST": host, "NAME": "app", "OPTIONS": dict(options)}


class RequireDatabaseTlsTests(SimpleTestCase):
    def test_a_deployed_postgres_connection_requires_tls(self):
        result = require_database_tls(database(), debug=False)
        self.assertEqual(result["OPTIONS"]["sslmode"], "require")

    def test_a_stricter_mode_is_kept(self):
        for mode in ("require", "verify-ca", "verify-full"):
            with self.subTest(mode=mode):
                result = require_database_tls(database(sslmode=mode), debug=False)
                self.assertEqual(result["OPTIONS"]["sslmode"], mode)

    def test_a_mode_that_allows_no_encryption_is_refused(self):
        for mode in ("disable", "allow", "prefer"):
            with self.subTest(mode=mode):
                with self.assertRaises(ImproperlyConfigured):
                    require_database_tls(database(sslmode=mode), debug=False)

    def test_other_options_are_kept_and_the_input_is_not_changed(self):
        original = database(connect_timeout=5)
        result = require_database_tls(original, debug=False)
        self.assertEqual(result["OPTIONS"], {"connect_timeout": 5, "sslmode": "require"})
        self.assertEqual(original["OPTIONS"], {"connect_timeout": 5})

    def test_debug_leaves_the_connection_alone(self):
        original = database(sslmode="disable")
        self.assertEqual(require_database_tls(original, debug=True), original)

    def test_a_database_on_this_machine_is_left_alone(self):
        for host in ("localhost", "127.0.0.1", "::1", "", "/var/run/postgresql", "LOCALHOST"):
            with self.subTest(host=host):
                original = database(host=host)
                self.assertEqual(require_database_tls(original, debug=False), original)

    def test_a_host_that_only_looks_local_still_requires_tls(self):
        for host in ("localhost.example.com", "127.0.0.1.example.com", "10.0.0.5"):
            with self.subTest(host=host):
                result = require_database_tls(database(host=host), debug=False)
                self.assertEqual(result["OPTIONS"]["sslmode"], "require")

    def test_sqlite_is_left_alone(self):
        original = database(host="", engine="django.db.backends.sqlite3")
        self.assertEqual(require_database_tls(original, debug=False), original)


class DeployedSettingsTests(SimpleTestCase):
    """Loads config.settings in a fresh process the way a deployed service does."""

    def load(self, database_url, debug="false"):
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_DEBUG": debug,
            "DJANGO_SECRET_KEY": "test-only-" + "x" * 50,
            "DATABASE_URL": database_url,
        }
        code = (
            "import json; from django.conf import settings; "
            "print(json.dumps(settings.DATABASES['default'].get('OPTIONS', {})))"
        )
        return subprocess.run(
            [sys.executable, "-c", code], env=env, cwd=settings.BASE_DIR,
            capture_output=True, text=True, timeout=60,
        )

    def test_deployed_settings_require_tls(self):
        result = self.load("postgresql://app:pw@db.internal.example:5432/app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["sslmode"], "require")

    def test_deployed_settings_refuse_a_url_that_turns_tls_off(self):
        result = self.load("postgresql://app:pw@db.internal.example:5432/app?sslmode=disable")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must require TLS", result.stderr)

    def test_local_development_settings_do_not_require_tls(self):
        result = self.load("postgresql://app:pw@db.internal.example:5432/app", debug="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("sslmode", json.loads(result.stdout))
