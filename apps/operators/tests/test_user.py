from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.operators.tests.base import HQ, on_hq


@on_hq
class OperatorUserTests(TestCase):
    def test_user_model_is_the_operators_user(self):
        self.assertEqual(get_user_model()._meta.label, "operators.User")

    def test_a_superuser_with_a_password_finds_no_admin(self):
        get_user_model().objects.create_superuser(
            username="operator", email="operator@example.test", password="a-long-test-only-passphrase"
        )
        self.client.login(username="operator", password="a-long-test-only-passphrase")

        for path in ("/admin/", "/admin/login/"):
            self.assertEqual(self.client.get(path, secure=True).status_code, 404, path)
            self.assertEqual(
                self.client.get(path, secure=True, HTTP_HOST=HQ).status_code, 404, path
            )

    def test_django_admin_is_not_installed(self):
        self.assertFalse(apps.is_installed("django.contrib.admin"))
