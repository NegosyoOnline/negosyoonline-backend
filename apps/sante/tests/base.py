"""Helpers shared by the Santé tests."""
from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

switched_on = override_settings(SANTE_SETTINGS_ENABLED=True)
MISSING_ID = "site_00000000000000000000"


def settings_url(site_id):
    return f"/v1/sites/{site_id}/sante/settings/"


def operator(username="op"):
    return get_user_model().objects.create_superuser(username, f"{username}@example.test", "pw")


def retemplate(site, template="other"):
    """Make this a site of another template, which the API cannot create in the MVP."""
    Site.objects.filter(pk=site.pk).update(template=template)
    site.refresh_from_db()
    return site


@switched_on
class SanteApiTestCase(SitesApiTestCase):
    """Signed in as Ana, who has one Santé site: self.site and its public id self.site_id."""

    def setUp(self):
        super().setUp()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.url = settings_url(self.site_id)

    def ben_with_site(self):
        """Ben signed in on his own client, with his own Santé site."""
        client = self.other_client()
        site_id = self.create_site(client=client, subdomain="bens-store")["id"]
        return client, Site.objects.get(public_id=site_id)

    def read(self, url=None, client=None):
        response = self.get(url or self.url, client=client)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()
