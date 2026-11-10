from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from apps.operators import crypto

KEY = Fernet.generate_key().decode()


class CryptoTests(SimpleTestCase):
    @override_settings(ADMIN_SECRETS_KEY=KEY)
    def test_round_trip_and_the_stored_value_is_not_the_secret(self):
        token = crypto.encrypt("JBSWY3DPEHPK3PXP")
        self.assertNotIn("JBSWY3DPEHPK3PXP", token)
        self.assertEqual(crypto.decrypt(token), "JBSWY3DPEHPK3PXP")
        self.assertTrue(crypto.key_ready())

    def test_an_empty_or_invalid_key_is_not_ready_and_refuses(self):
        for key in ("", "not-a-key", "abcd"):
            with override_settings(ADMIN_SECRETS_KEY=key):
                self.assertFalse(crypto.key_ready())
                with self.assertRaises(crypto.SecretsKeyError):
                    crypto.encrypt("x")
                with self.assertRaises(crypto.SecretsKeyError):
                    crypto.decrypt("x")

    def test_a_different_key_cannot_decrypt(self):
        with override_settings(ADMIN_SECRETS_KEY=KEY):
            token = crypto.encrypt("secret")
        with override_settings(ADMIN_SECRETS_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(crypto.SecretsKeyError):
                crypto.decrypt(token)

    @override_settings(ADMIN_SECRETS_KEY=KEY)
    def test_an_empty_or_damaged_value_cannot_be_decrypted(self):
        for value in ("", "garbage"):
            with self.assertRaises(crypto.SecretsKeyError):
                crypto.decrypt(value)
