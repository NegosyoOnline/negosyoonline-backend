from django.test import Client, override_settings

from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL, ApiTestCase

OTHER_EMAIL = "ben@example.test"
CREATE = {"template": "sante", "subdomain": "anas-store", "display_name": "Ana's Store"}


@override_settings(SITE_PARENT_HOST="negosyotest.com")
class SitesApiTestCase(ApiTestCase):
    """Signed in as Ana on self.client. other_client() signs Ben in on a second client."""

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.account = Account.objects.get(email=EMAIL)

    def other_client(self, email=OTHER_EMAIL):
        client = Client()
        self.sign_in(email=email, client=client)
        return client

    def patch(self, path, data=None, **extra):
        return self.send("patch", path, data, **extra)

    def create_site(self, client=None, **overrides):
        response = self.post("/v1/sites/", {**CREATE, **overrides}, client=client)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()["error"]
        self.assertEqual(body["code"], code)
        return body
