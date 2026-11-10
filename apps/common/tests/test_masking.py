from django.test import SimpleTestCase

from apps.common.masking import mask_email, mask_mobile


class MaskEmailTests(SimpleTestCase):
    def test_keeps_the_first_character_and_the_domain(self):
        self.assertEqual(mask_email("ana@example.test"), "a***@example.test")
        self.assertEqual(mask_email("  ana.reyes@example.test "), "a***@example.test")
        self.assertEqual(mask_email("a@example.test"), "a***@example.test")

    def test_a_value_that_is_not_an_address_is_fully_hidden(self):
        for value in ("ana", "@example.test", "ana@"):
            with self.subTest(value=value):
                self.assertEqual(mask_email(value), "***")

    def test_nothing_stays_nothing(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertEqual(mask_email(value), "")


class MaskMobileTests(SimpleTestCase):
    def test_hides_the_middle_digits(self):
        self.assertEqual(mask_mobile("+639171234567"), "+63917***4567")

    def test_a_short_value_is_fully_hidden(self):
        self.assertEqual(mask_mobile("0917123"), "***")

    def test_nothing_stays_nothing(self):
        for value in ("", None):
            with self.subTest(value=value):
                self.assertEqual(mask_mobile(value), "")
