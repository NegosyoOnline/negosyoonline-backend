from django.contrib.auth import get_user_model
from django.test import TestCase


class OperatorUserTests(TestCase):
    def test_user_model_is_the_operators_user(self):
        self.assertEqual(get_user_model()._meta.label, "operators.User")

    def test_superuser_can_be_created_and_can_open_the_admin(self):
        get_user_model().objects.create_superuser(
            username="operator", email="operator@example.test", password="a-long-test-only-passphrase"
        )
        self.client.login(username="operator", password="a-long-test-only-passphrase")

        response = self.client.get("/admin/", secure=True)

        self.assertEqual(response.status_code, 200)
