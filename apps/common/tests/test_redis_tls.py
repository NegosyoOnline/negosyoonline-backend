import json
import os
import ssl
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

from apps.common.redis_tls import redis_tls_options

PEM = "-----BEGIN CERTIFICATE-----\nTEST-ONLY\n-----END CERTIFICATE-----"


class RedisTlsOptionsTests(SimpleTestCase):
    def test_a_plain_redis_url_gets_no_options(self):
        self.assertEqual(redis_tls_options("redis://:pw@host:6379/0"), {})
        self.assertEqual(redis_tls_options(None), {})
        self.assertEqual(redis_tls_options("", ca_file="/x"), {})

    def test_without_a_certificate_the_choice_is_explicit_none(self):
        self.assertEqual(redis_tls_options("rediss://:pw@host:1/0"), {"ssl_cert_reqs": ssl.CERT_NONE})

    def test_a_ca_file_pins_the_certificate(self):
        options = redis_tls_options("rediss://:pw@host:1/0", ca_file="/etc/ca.pem")
        self.assertEqual(options["ssl_cert_reqs"], ssl.CERT_REQUIRED)
        self.assertEqual(options["ssl_ca_certs"], "/etc/ca.pem")
        self.assertFalse(options["ssl_check_hostname"])

    def test_ca_text_is_written_to_a_private_file(self):
        options = redis_tls_options("rediss://:pw@host:1/0", ca_pem=PEM)
        path = options["ssl_ca_certs"]
        self.addCleanup(os.remove, path)
        self.assertEqual(options["ssl_cert_reqs"], ssl.CERT_REQUIRED)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path) as handle:
            self.assertEqual(handle.read(), PEM + "\n")
        self.assertEqual(redis_tls_options("rediss://:pw@host:1/0", ca_pem=PEM)["ssl_ca_certs"], path)

    def test_a_url_with_its_own_ssl_cert_reqs_is_left_alone(self):
        self.assertEqual(redis_tls_options("rediss://:pw@host:1/0?ssl_cert_reqs=required", ca_file="/x"), {})


class RedisTlsSettingsTests(SimpleTestCase):
    def load(self, url):
        env = {k: v for k, v in os.environ.items() if k not in ("REDIS_URL", "REDIS_TLS_CA_FILE", "REDIS_TLS_CA_PEM")}
        env.update({"DJANGO_SECRET_KEY": "x" * 60, "DJANGO_DEBUG": "true"})
        if url:
            env["REDIS_URL"] = url
        code = (
            "import json; from django.conf import settings as s; "
            "print(json.dumps([s.CACHES['default']['OPTIONS'], getattr(s, 'CELERY_BROKER_USE_SSL', None), s.CELERY_BROKER_URL]))"
        )
        return subprocess.run(
            [sys.executable, "-c", code], env=env, cwd=settings.BASE_DIR, capture_output=True, text=True, timeout=60
        )

    def test_rediss_gives_the_cache_and_celery_the_same_tls_options(self):
        result = self.load("rediss://:pw@data.example:16379/0")
        self.assertEqual(result.returncode, 0, result.stderr)
        cache_options, broker_ssl, broker = json.loads(result.stdout)
        self.assertEqual(cache_options["CONNECTION_POOL_KWARGS"], broker_ssl)
        self.assertEqual(broker_ssl, {"ssl_cert_reqs": ssl.CERT_NONE})
        self.assertTrue(broker.startswith("rediss://"))
        self.assertTrue(broker.endswith("/1"))

    def test_plain_redis_sets_no_tls_options(self):
        result = self.load("redis://:pw@data.example:6379/0")
        self.assertEqual(result.returncode, 0, result.stderr)
        cache_options, broker_ssl, _ = json.loads(result.stdout)
        self.assertNotIn("CONNECTION_POOL_KWARGS", cache_options)
        self.assertIsNone(broker_ssl)
