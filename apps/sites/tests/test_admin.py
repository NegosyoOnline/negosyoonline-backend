from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.operators.tests.base import HqAdminMixin, on_hq
from apps.sites.tests.base import make_account, make_site


@on_hq
class SitesAdminTests(HqAdminMixin, TestCase):
    def setUp(self):
        operator = get_user_model().objects.create_superuser("op", "op@example.test", "pw")
        self.open_hq_session(operator)
        self.site = make_site(make_account())

    def test_the_lists_open_and_search_works(self):
        for model in ("site", "subdomain", "sitebusinessprofile", "onboarding"):
            with self.subTest(model=model):
                url = self.admin_url(f"admin:sites_{model}_changelist")
                self.assertEqual(self.hq_get(url).status_code, 200)
        url = self.admin_url("admin:sites_site_changelist") + "?q=anas-store"
        self.assertContains(self.hq_get(url), "Ana&#x27;s Store")

    def test_the_site_and_onboarding_lists_show_the_account_email_masked(self):
        for model in ("site", "onboarding"):
            with self.subTest(model=model):
                url = self.admin_url(f"admin:sites_{model}_changelist")
                response = self.hq_get(url)
                self.assertContains(response, "a***@example.test")
                self.assertNotContains(response, "ana@example.test")

    def test_the_lists_do_not_read_the_account_once_per_row(self):
        for model in ("site", "onboarding"):
            with self.subTest(model=model):
                url = self.admin_url(f"admin:sites_{model}_changelist")
                with CaptureQueriesContext(connection) as one_row:
                    self.hq_get(url)
                for number in range(3):
                    account = make_account(f"{model}{number}@example.test")
                    make_site(account, name=f"{model}-{number}")
                with CaptureQueriesContext(connection) as four_rows:
                    self.hq_get(url)
                self.assertEqual(len(four_rows), len(one_row))

    def test_nothing_can_be_added_changed_or_deleted(self):
        add = self.admin_url("admin:sites_site_add")
        self.assertEqual(self.hq_get(add).status_code, 403)
        change = self.admin_url("admin:sites_site_change", self.site.pk)
        response = self.hq_post(change, {"display_name": "Changed"})
        self.assertEqual(response.status_code, 403)
        delete = self.admin_url("admin:sites_site_delete", self.site.pk)
        self.assertEqual(self.hq_post(delete, {"post": "yes"}).status_code, 403)
        self.site.refresh_from_db()
        self.assertEqual(self.site.display_name, "Ana's Store")
