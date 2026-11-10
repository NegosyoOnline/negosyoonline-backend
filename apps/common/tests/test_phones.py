from django.test import SimpleTestCase

from apps.common.phones import normalise_ph_mobile


class NormalisePhMobileTests(SimpleTestCase):
    def test_every_accepted_form_becomes_plus_639(self):
        for raw in ("09171234567", "9171234567", "+639171234567", "639171234567",
                    "0917 123-4567", " (0917) 123.4567 "):
            with self.subTest(raw=raw):
                self.assertEqual(normalise_ph_mobile(raw), "+639171234567")

    def test_anything_else_is_refused(self):
        for raw in ("0917123456", "+6391712345678", "08171234567", "abc", "", None,
                    "+19171234567"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalise_ph_mobile(raw)
