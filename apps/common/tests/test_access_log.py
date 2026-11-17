from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase

from config.access_log import MASK, MaskingLogger, mask_query


class MaskQueryTests(SimpleTestCase):
    def test_dotted_key_masked_and_others_intact(self):
        out = mask_query("hub.mode=subscribe&hub.verify_token=DUMMY123&hub.challenge=1")
        self.assertEqual(out, "hub.mode=subscribe&hub.verify_token=***&hub.challenge=1")

    def test_all_key_forms_masked_case_insensitively(self):
        for key in ("hub.verify_token", "hub_verify_token", "hub%2Everify_token",
                    "hub%2everify_token", "HUB.Verify_Token"):
            out = mask_query(f"a=1&{key}=SECRET&b=2")
            self.assertNotIn("SECRET", out, key)
            self.assertEqual(out, f"a=1&{key}={MASK}&b=2")

    def test_query_without_token_unchanged(self):
        for value in ("", "a=1&b=2", "hub.mode=subscribe", None):
            self.assertEqual(mask_query(value), value)

    def test_request_line_masked_keeping_protocol(self):
        line = "GET /v1/webhooks/messenger/?hub.verify_token=DUMMY123 HTTP/1.1"
        self.assertEqual(
            mask_query(line), "GET /v1/webhooks/messenger/?hub.verify_token=*** HTTP/1.1"
        )

    def test_token_last_param_and_empty_value(self):
        self.assertEqual(mask_query("x=1&hub.verify_token=abc"), "x=1&hub.verify_token=***")
        self.assertEqual(mask_query("hub.verify_token=&x=1"), "hub.verify_token=***&x=1")

    def test_oauth_callback_request_line_masks_code_and_state(self):
        line = "GET /v1/inquiries/messenger/callback/?code=AQDabc&state=xyz123 HTTP/1.1"
        self.assertEqual(
            mask_query(line),
            "GET /v1/inquiries/messenger/callback/?code=***&state=*** HTTP/1.1",
        )

    def test_state_first_and_code_second(self):
        self.assertEqual(mask_query("?state=xyz123&code=AQDabc"), "?state=***&code=***")

    def test_bare_query_string(self):
        self.assertEqual(mask_query("code=a&state=b"), "code=***&state=***")

    def test_similar_names_not_masked(self):
        for value in ("zipcode=1100", "a=1&promo_code=SAVE10", "country_state=PH&x=1",
                      "?zipcode=1&promo_code=2&country_state=3"):
            self.assertEqual(mask_query(value), value)

    def test_other_params_intact(self):
        self.assertEqual(
            mask_query("a=1&code=AQD&b=2&state=s&error=none"),
            "a=1&code=***&b=2&state=***&error=none",
        )

    def test_case_insensitive_and_fragment_stop(self):
        self.assertEqual(mask_query("?CODE=abc#frag"), "?CODE=***#frag")

    def test_verify_token_and_code_together(self):
        self.assertEqual(
            mask_query("hub.verify_token=SECRET&code=AQD&x=1"),
            "hub.verify_token=***&code=***&x=1",
        )


class MaskingLoggerTests(SimpleTestCase):
    def test_atoms_mask_q_and_r_only(self):
        query = "hub.mode=subscribe&hub.verify_token=DUMMY123&hub.challenge=1"
        path = "/v1/webhooks/messenger/"
        environ = {
            "REMOTE_ADDR": "1.2.3.4",
            "REQUEST_METHOD": "GET",
            "RAW_URI": f"{path}?{query}",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "PATH_INFO": path,
            "QUERY_STRING": query,
        }
        resp = SimpleNamespace(status="200 OK", headers=[], sent=5)
        logger = MaskingLogger.__new__(MaskingLogger)
        atoms = logger.atoms(resp, [], environ, timedelta(milliseconds=7))
        self.assertEqual(atoms["q"], "hub.mode=subscribe&hub.verify_token=***&hub.challenge=1")
        self.assertEqual(
            atoms["r"],
            f"GET {path}?hub.mode=subscribe&hub.verify_token=***&hub.challenge=1 HTTP/1.1",
        )
        self.assertEqual(atoms["U"], path)
        self.assertEqual(atoms["s"], "200")
        self.assertEqual(atoms["M"], 7)
        self.assertNotIn("DUMMY123", repr(atoms))

    def test_atoms_mask_oauth_callback(self):
        query = "code=AQDabc&state=xyz123"
        path = "/v1/inquiries/messenger/callback/"
        environ = {
            "REMOTE_ADDR": "1.2.3.4",
            "REQUEST_METHOD": "GET",
            "RAW_URI": f"{path}?{query}",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "PATH_INFO": path,
            "QUERY_STRING": query,
        }
        resp = SimpleNamespace(status="302 Found", headers=[], sent=0)
        logger = MaskingLogger.__new__(MaskingLogger)
        atoms = logger.atoms(resp, [], environ, timedelta(milliseconds=3))
        self.assertEqual(atoms["q"], "code=***&state=***")
        self.assertEqual(atoms["r"], f"GET {path}?code=***&state=*** HTTP/1.1")
        self.assertNotIn("AQDabc", repr(atoms))
        self.assertNotIn("xyz123", repr(atoms))


class UvicornAccessFilterTests(SimpleTestCase):
    """Under ASGI the request line is logged by `uvicorn.access`, not gunicorn's logger class."""

    def record(self, path):
        import logging
        return logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:5000", "GET", path, "1.1", 200), None,
        )

    def test_the_filter_masks_verify_token_code_and_state_in_the_logged_line(self):
        from config.access_log import MaskingFilter

        for path, masked in (
            ("/v1/webhooks/messenger/?hub.mode=subscribe&hub.verify_token=DUMMY123&hub.challenge=1",
             "/v1/webhooks/messenger/?hub.mode=subscribe&hub.verify_token=***&hub.challenge=1"),
            ("/v1/inquiries/messenger/callback/?code=AQDabc&state=xyz123", "/v1/inquiries/messenger/callback/?code=***&state=***"),
            ("/v1/stores/?zipcode=1234&promo_code=A", "/v1/stores/?zipcode=1234&promo_code=A"),
        ):
            record = self.record(path)
            self.assertTrue(MaskingFilter().filter(record))
            line = record.getMessage()
            self.assertEqual(line, f'127.0.0.1:5000 - "GET {masked} HTTP/1.1" 200')
            for secret in ("DUMMY123", "AQDabc", "xyz123"):
                self.assertNotIn(secret, line)

    def test_install_attaches_it_once_to_the_uvicorn_access_logger(self):
        import logging
        from config.access_log import MaskingFilter, install_access_filter

        target = logging.getLogger("uvicorn.access")
        before = [f for f in target.filters if isinstance(f, MaskingFilter)]
        install_access_filter()
        install_access_filter()
        after = [f for f in target.filters if isinstance(f, MaskingFilter)]
        self.assertEqual(len(after), max(len(before), 1))
        for f in after:
            if f not in before:
                target.removeFilter(f)
