from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.sites.tests.base import make_account, make_site


class SitesAdminTests(TestCase):
    def setUp(self):
        operator = get_user_model().objects.create_superuser("op", "op@example.test", "pw")
        self.client.force_login(operator)
        self.site = make_site(make_account())

    def test_the_lists_open_and_search_works(self):
        for model in ("site", "subdomain", "sitebusinessprofile", "onboarding"):
            with self.subTest(model=model):
                url = reverse(f"admin:sites_{model}_changelist")
                self.assertEqual(self.client.get(url, secure=True).status_code, 200)
        url = reverse("admin:sites_site_changelist") + "?q=anas-store"
        self.assertContains(self.client.get(url, secure=True), "Ana&#x27;s Store")

    def test_nothing_can_be_added_changed_or_deleted(self):
        add = reverse("admin:sites_site_add")
        self.assertEqual(self.client.get(add, secure=True).status_code, 403)
        change = reverse("admin:sites_site_change", args=[self.site.pk])
        response = self.client.post(change, {"display_name": "Changed"}, secure=True)
        self.assertEqual(response.status_code, 403)
        delete = reverse("admin:sites_site_delete", args=[self.site.pk])
        self.assertEqual(self.client.post(delete, {"post": "yes"}, secure=True).status_code, 403)
        self.site.refresh_from_db()
        self.assertEqual(self.site.display_name, "Ana's Store")
