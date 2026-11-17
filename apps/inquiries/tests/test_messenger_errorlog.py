"""The refused/failed Messenger call warnings: what they log and what they never log. Fake payloads only."""
from unittest import mock

from django.test import SimpleTestCase

from apps.inquiries import messenger, outcomes

LOGGER = "apps.inquiries.messenger"
FAKE_TOKEN = "EAAfaketoken" + "x" * 40
FAKE_PROOF = "f" * 64


def refused(status, error, method="GET", path="/103742474938731", params=None):
    with mock.patch.object(messenger, "_transport", return_value=(status, {"error": error})):
        with SimpleTestCase().assertLogs(LOGGER, level="WARNING") as logs:
            result = messenger.call(method, path, params=params, token=FAKE_TOKEN)
    return result, "\n".join(logs.output)


class RefusedLogTests(SimpleTestCase):
    def test_all_fields_and_outcome(self):
        error = {"code": 100, "error_subcode": 33, "type": "OAuthException", "fbtrace_id": "AbC123",
                 "message": "Unsupported get request."}
        result, log = refused(400, error)
        self.assertEqual(result.code, outcomes.BAD_RESPONSE)
        for part in ("(bad_response) GET /{id} ", "status=400", "code=100", "subcode=33",
                     "type=OAuthException", "fbtrace=AbC123", "msg=Unsupported get request."):
            self.assertIn(part, log)

    def test_subresource_template_kept(self):
        _, log = refused(400, {"code": 100}, method="POST", path="/103742474938731/subscribed_apps")
        self.assertIn("POST /{id}/subscribed_apps ", log)

    def test_debug_token_path_and_no_params_logged(self):
        params = {"input_token": FAKE_TOKEN, "access_token": "APPID|" + "s" * 32, "appsecret_proof": FAKE_PROOF}
        _, log = refused(400, {"code": 100, "message": "bad"}, path="/debug_token", params=params)
        self.assertIn("GET /debug_token ", log)
        for secret in (FAKE_TOKEN, "s" * 32, FAKE_PROOF, "input_token", "access_token"):
            self.assertNotIn(secret, log)

    def test_query_string_never_logged(self):
        _, log = refused(400, {"code": 100}, path="/me/accounts?access_token=" + FAKE_TOKEN)
        self.assertIn("/me/accounts ", log)
        self.assertNotIn(FAKE_TOKEN, log)

    def test_token_in_message_masked(self):
        _, log = refused(400, {"code": 100, "message": f"Invalid token {FAKE_TOKEN} here"})
        self.assertNotIn(FAKE_TOKEN, log)
        self.assertIn("Invalid token *** here", log)

    def test_message_truncated(self):
        _, log = refused(400, {"code": 100, "message": "word " * 100})
        msg = log.split("msg=", 1)[1]
        self.assertLessEqual(len(msg), 200)
        self.assertGreater(len(msg), 150)

    def test_newlines_stripped(self):
        _, log = refused(400, {"code": 100, "message": "line one\nINJECTED\r\nline two", "type": "A\nB"})
        self.assertEqual(len(log.splitlines()), 1)
        self.assertIn("msg=line one INJECTED line two", log)

    def test_non_dict_error_logs_empty_fields(self):
        result, log = refused(400, "oops")
        self.assertEqual(result.code, outcomes.BAD_RESPONSE)
        self.assertIn("code= subcode= type= fbtrace= msg=", log)

    def test_weird_types_log_empty_fields(self):
        error = {"code": "100", "error_subcode": [1], "type": {"a": 1}, "fbtrace_id": 5, "message": None}
        result, log = refused(400, error)
        self.assertEqual(result.code, outcomes.BAD_RESPONSE)
        self.assertIn("code= subcode= type= fbtrace= msg=", log)

    def test_mapped_code_unchanged(self):
        result, log = refused(400, {"code": 190, "message": "expired"})
        self.assertEqual(result.code, outcomes.TOKEN_INVALID)
        self.assertIn("(token_invalid)", log)


class FailedLogTests(SimpleTestCase):
    def test_oserror_logs_method_and_template(self):
        with mock.patch.object(messenger, "_transport", side_effect=OSError("down " + FAKE_TOKEN)):
            with self.assertLogs(LOGGER, level="WARNING") as logs:
                result = messenger.call("GET", "/103742474938731", params={"access_token": FAKE_TOKEN})
        log = "\n".join(logs.output)
        self.assertEqual(result.code, outcomes.PROVIDER_DOWN)
        self.assertIn("(OSError) GET /{id}", log)
        self.assertNotIn(FAKE_TOKEN, log)
