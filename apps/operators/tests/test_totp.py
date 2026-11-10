from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase

from apps.operators import totp

# RFC 6238 appendix B, SHA-1, secret "12345678901234567890", last six digits.
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
    (20000000000, "353130"),
]


def at(seconds):
    return datetime.fromtimestamp(seconds, tz=dt_timezone.utc)


class TotpTests(SimpleTestCase):
    def test_rfc_6238_vectors(self):
        for seconds, code in VECTORS:
            self.assertEqual(totp.code_at(SECRET, seconds // 30), code)

    def test_a_code_matches_its_own_step_and_one_either_side(self):
        now = at(1111111109)
        current = totp.current_step(now)
        for offset in (-1, 0, 1):
            code = totp.code_at(SECRET, current + offset)
            self.assertEqual(totp.matching_step(SECRET, code, now), current + offset)

    def test_a_code_two_steps_away_does_not_match(self):
        now = at(1111111109)
        current = totp.current_step(now)
        for offset in (-2, 2):
            self.assertIsNone(totp.matching_step(SECRET, totp.code_at(SECRET, current + offset), now))

    def test_malformed_codes_do_not_match(self):
        for bad in ("", "12345", "1234567", "12a456", None, 123456):
            self.assertIsNone(totp.matching_step(SECRET, bad, at(59)))

    def test_a_new_secret_is_32_base32_characters_and_differs_each_time(self):
        first, second = totp.new_secret(), totp.new_secret()
        self.assertRegex(first, r"^[A-Z2-7]{32}$")
        self.assertNotEqual(first, second)

    def test_setup_link_names_the_issuer_and_the_secret(self):
        link = totp.setup_link(SECRET, "admin@example.test")
        self.assertTrue(link.startswith("otpauth://totp/NegosyoOnline%20admin%3Aadmin%40example.test?"))
        self.assertIn(f"secret={SECRET}", link)
        self.assertIn("digits=6", link)
