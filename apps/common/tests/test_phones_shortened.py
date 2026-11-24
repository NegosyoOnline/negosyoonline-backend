from django.test import SimpleTestCase

from apps.common.phones import is_shortened_by_excel


class ShortenedTests(SimpleTestCase):
    def test_scientific_notation_is_found_and_plain_numbers_are_not(self):
        for raw in ("6.39563E+11", "6.39E11", " 6.39563e+11 ", "6E+11", "639E10"):
            self.assertTrue(is_shortened_by_excel(raw), raw)
        for raw in ("639563012345", "09170000101", "+639170000101", "E+11", "6.E+11", "6.39E-11", "1e", "", None, "abc"):
            self.assertFalse(is_shortened_by_excel(raw), raw)
