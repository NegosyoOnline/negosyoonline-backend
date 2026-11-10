import re

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.operators.tests.base import HqAdminMixin, on_hq
from apps.accounts.models import Account
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site

COUNTS = "counts_for_sante_site_owner"


def column(response, name):
    """The yes/no values of one list column, in row order."""
    cells = re.findall(rf'class="field-{name}[^"]*"><img[^>]*alt="(True|False)"', response.content.decode())
    return [cell == "True" for cell in cells]


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

    def test_the_sites_list_says_whether_each_site_counts_for_sante_site_owner(self):
        url = self.admin_url("admin:sites_site_changelist")
        response = self.hq_get(url)
        self.assertContains(response, "Counts for Santé Site Owner")
        self.assertEqual(column(response, COUNTS), [True])

        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)
        self.assertEqual(column(self.hq_get(url), COUNTS), [False])

        Site.objects.filter(pk=self.site.pk).update(operator_hold=False, deleted_at=timezone.now())
        self.assertEqual(column(self.hq_get(url), COUNTS), [False])

        Site.objects.filter(pk=self.site.pk).update(deleted_at=None)
        self.assertEqual(column(self.hq_get(url), COUNTS), [True])
        Account.objects.filter(pk=self.site.account_id).update(closed_at=timezone.now())
        self.assertEqual(column(self.hq_get(url), COUNTS), [False])

    def test_the_counts_column_is_display_only(self):
        url = self.admin_url("admin:sites_site_changelist")
        response = self.hq_get(url)
        self.assertNotContains(response, 'name="form-0-')
        self.hq_post(url, {COUNTS: "False", "operator_hold": "on"})
        self.site.refresh_from_db()
        self.assertFalse(self.site.operator_hold)
        self.assertEqual(column(self.hq_get(url), COUNTS), [True])
