from django.test import SimpleTestCase

from apps.common.ids import is_public_id, new_public_id


class PublicIdTests(SimpleTestCase):
    def test_new_id_has_prefix_and_twenty_random_characters(self):
        value = new_public_id("site")

        self.assertTrue(value.startswith("site_"))
        self.assertEqual(len(value), len("site_") + 20)

    def test_new_ids_are_different_each_time(self):
        self.assertNotEqual(new_public_id("acc"), new_public_id("acc"))

    def test_is_public_id_accepts_its_own_output(self):
        self.assertTrue(is_public_id(new_public_id("acc"), "acc"))

    def test_is_public_id_rejects_wrong_prefix_and_bad_shapes(self):
        value = new_public_id("acc")

        self.assertFalse(is_public_id(value, "site"))
        self.assertFalse(is_public_id("acc_short", "acc"))
        self.assertFalse(is_public_id(value.upper(), "acc"))
        self.assertFalse(is_public_id("42", "acc"))

    def test_prefix_must_be_lowercase_letters(self):
        with self.assertRaises(ValueError):
            new_public_id("Site_1")
