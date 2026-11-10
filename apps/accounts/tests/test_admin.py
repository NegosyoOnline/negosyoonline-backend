import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    ExternalIdentity,
    PolicyAcceptance,
    Session,
)
from apps.operators.tests.base import HqAdminMixin, on_hq
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site

EMAIL = "ana.reyes@example.test"
MASKED = "a***@example.test"


@on_hq
class AccountsAdminMaskingTests(HqAdminMixin, TestCase):
    """The admin lists show a subscriber's email masked, never in full."""

    def setUp(self):
        operator = get_user_model().objects.create_superuser("op", "op@operators.test", "pw")
        self.open_hq_session(operator)
        now = timezone.now()
        self.account = Account.objects.create(email=EMAIL, name="Ana")
        Session.objects.create(
            account=self.account, token_hash="0" * 64, expires_at=now + timedelta(days=1),
            last_used_at=now,
        )
        PolicyAcceptance.objects.create(
            account=self.account, policy_version="2026-11-10", policies=["terms", "privacy"]
        )
        AccountAuditEvent.objects.create(account=self.account, event="signed_in")
        ExternalIdentity.objects.create(
            account=self.account, provider="google", provider_user_id="g-1"
        )

    def test_every_list_shows_the_email_masked(self):
        for model in ("account", "session", "policyacceptance", "accountauditevent",
                      "externalidentity"):
            with self.subTest(model=model):
                url = self.admin_url(f"admin:accounts_{model}_changelist")
                response = self.hq_get(url)
                self.assertContains(response, MASKED)
                self.assertNotContains(response, EMAIL)

    def test_search_by_full_email_still_finds_the_account_and_stays_masked(self):
        url = self.admin_url("admin:accounts_account_changelist")
        response = self.hq_get(url, {"q": EMAIL})
        self.assertContains(response, MASKED)
        self.assertContains(response, self.account.public_id)
        # The search box echoes what the operator typed; the result rows must not repeat it.
        self.assertEqual(response.content.decode().count(EMAIL), 1)

    def test_the_masked_columns_sort_by_the_real_email(self):
        Account.objects.create(email="zed@example.test")
        url = self.admin_url("admin:accounts_account_changelist")
        response = self.hq_get(url, {"o": "1"})
        body = response.content.decode()
        self.assertLess(body.index(MASKED), body.index("z***@example.test"))


OWNER = "sante_site_owner"


def owner_column(response):
    """The yes/no values of the Santé Site Owner column, in row order."""
    cells = re.findall(
        rf'class="field-{OWNER}[^"]*"><img[^>]*alt="(True|False)"', response.content.decode()
    )
    return [cell == "True" for cell in cells]


@on_hq
class SubscribersSanteSiteOwnerTests(HqAdminMixin, TestCase):
    """The Subscribers list shows the derived label to operators and lets them filter by it."""

    def setUp(self):
        operator = get_user_model().objects.create_superuser("op", "op@operators.test", "pw")
        self.open_hq_session(operator)
        self.url = self.admin_url("admin:accounts_account_changelist")
        self.ana = make_account()
        self.site = make_site(self.ana)

    def test_the_column_is_yes_for_an_eligible_owner_and_no_after_the_site_is_held(self):
        response = self.hq_get(self.url)
        self.assertContains(response, "Santé Site Owner")
        self.assertEqual(owner_column(response), [True])

        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)

        self.assertEqual(owner_column(self.hq_get(self.url)), [False])

    def test_an_account_with_no_site_and_a_closed_account_show_no(self):
        make_account("ben@example.test")
        self.assertEqual(sorted(owner_column(self.hq_get(self.url))), [False, True])

        Account.objects.filter(pk=self.ana.pk).update(closed_at=timezone.now())

        self.assertEqual(owner_column(self.hq_get(self.url)), [False, False])

    def test_the_filter_returns_only_labelled_accounts(self):
        ben = make_account("ben@example.test")
        cora = make_account("cora@example.test")
        make_site(cora, name="coras-store", operator_hold=True)

        yes = self.hq_get(self.url, {OWNER: "yes"})
        self.assertContains(yes, self.ana.public_id)
        self.assertNotContains(yes, ben.public_id)
        self.assertNotContains(yes, cora.public_id)
        self.assertEqual(owner_column(yes), [True])

        no = self.hq_get(self.url, {OWNER: "no"})
        self.assertNotContains(no, self.ana.public_id)
        self.assertContains(no, ben.public_id)
        self.assertContains(no, cora.public_id)
        self.assertEqual(owner_column(no), [False, False])

    def test_the_list_does_not_run_a_query_per_row(self):
        for params in ({}, {OWNER: "yes"}):
            with self.subTest(params=params):
                with CaptureQueriesContext(connection) as before:
                    self.hq_get(self.url, params)
                for number in range(3):
                    account = make_account(f"more{len(params)}{number}@example.test")
                    make_site(account, name=f"more-{len(params)}-{number}")
                with CaptureQueriesContext(connection) as after:
                    self.hq_get(self.url, params)
                self.assertEqual(len(after), len(before))

    def test_the_column_is_display_only_and_opening_the_list_writes_nothing(self):
        def snapshot():
            return (
                list(Account.objects.order_by("pk").values()),
                list(Site.objects.order_by("pk").values()),
            )

        before = snapshot()
        response = self.hq_get(self.url)
        self.hq_get(self.url, {OWNER: "yes"})

        self.assertNotContains(response, 'name="form-0-')
        self.assertEqual(snapshot(), before)

    def test_the_change_page_has_no_field_for_the_label(self):
        change = self.admin_url("admin:accounts_account_change", self.ana.pk)
        response = self.hq_get(change)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, OWNER)
