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
