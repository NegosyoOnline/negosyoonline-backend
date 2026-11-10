from django.core.cache import cache
from django.test import SimpleTestCase

from apps.accounts import codes
from apps.common.errors import ApiError

EMAIL = "ana@example.test"
IP = "203.0.113.9"


def wrong(code):
    return "000000" if code != "000000" else "111111"


class EmailCodeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_issued_code_is_six_digits_and_checks_true(self):
        code = codes.issue_code(EMAIL, IP)

        self.assertRegex(code, r"^\d{6}$")
        self.assertTrue(codes.check_code(EMAIL, code))

    def test_wrong_code_and_unknown_email_check_false(self):
        code = codes.issue_code(EMAIL, IP)

        self.assertFalse(codes.check_code(EMAIL, wrong(code)))
        self.assertFalse(codes.check_code("other@example.test", code))

    def test_code_is_dead_after_five_wrong_guesses(self):
        code = codes.issue_code(EMAIL, IP)

        for _ in range(codes.MAX_ATTEMPTS):
            self.assertFalse(codes.check_code(EMAIL, wrong(code)))

        self.assertFalse(codes.check_code(EMAIL, code))

    def test_cleared_code_no_longer_checks(self):
        code = codes.issue_code(EMAIL, IP)

        codes.clear_code(EMAIL)

        self.assertFalse(codes.check_code(EMAIL, code))

    def test_second_code_within_a_minute_is_refused(self):
        codes.issue_code(EMAIL, IP)

        with self.assertRaises(ApiError) as raised:
            codes.issue_code(EMAIL, IP)

        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.code, "rate_limited")

    def test_sixth_code_for_one_email_in_an_hour_is_refused(self):
        for _ in range(codes.PER_EMAIL_PER_HOUR):
            codes.issue_code(EMAIL, IP)
            cache.delete(codes._key("cooldown", "signin", EMAIL))

        with self.assertRaises(ApiError) as raised:
            codes.issue_code(EMAIL, IP)

        self.assertEqual(raised.exception.status, 429)

    def test_a_code_for_one_purpose_does_not_work_for_another(self):
        code = codes.issue_code(EMAIL, IP, purpose="change:1")

        self.assertFalse(codes.check_code(EMAIL, code))
        self.assertTrue(codes.check_code(EMAIL, code, purpose="change:1"))
