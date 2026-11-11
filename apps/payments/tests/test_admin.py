"""The payment lists in the hq admin: read-only, the account number shown by its last four
digits only, and both lists closed while the switch is off."""
from django.test import TestCase, override_settings

from apps.operators.tests.base import HqAdminMixin, on_hq
from apps.payments.models import SitePaymentAccount, SitePaymentSettings
from apps.payments.tests.base import BANK, make_payment_account, payment_rows, switched_on
from apps.sante.tests.base import operator
from apps.sites.tests.base import make_account, make_site

ACCOUNTS = "admin:payments_sitepaymentaccount"
SETTINGS = "admin:payments_sitepaymentsettings"
NUMBER = BANK["account_number"]


@on_hq
@switched_on
class PaymentsAdminTestCase(HqAdminMixin, TestCase):
    def setUp(self):
        self.open_hq_session(operator())
        self.site = make_site(make_account(), "site-a", "Site A")
        self.row = make_payment_account(self.site)
        self.settings = SitePaymentSettings.objects.create(site=self.site, direct_enabled=False)


class ListTests(PaymentsAdminTestCase):
    def test_the_account_pages_show_the_last_four_digits_and_never_the_number(self):
        for page in (
            self.hq_get(self.admin_url(f"{ACCOUNTS}_changelist")),
            self.hq_get(self.admin_url(f"{ACCOUNTS}_change", self.row.pk)),
        ):
            self.assertContains(page, "ending 7890")
            self.assertContains(page, "BDO")
            self.assertNotContains(page, NUMBER)
            self.assertNotContains(page, BANK["account_name"])

    def test_the_index_lists_both(self):
        index = self.hq_get(self.admin_url("admin:index"))
        self.assertContains(index, "payment account")
        self.assertContains(index, "payment settings")

    def test_the_settings_list_opens_and_has_no_xendit_column(self):
        page = self.hq_get(self.admin_url(f"{SETTINGS}_changelist"))
        self.assertContains(page, "Site A")
        self.assertNotContains(page, "endit")

    def test_nothing_can_be_added_changed_or_deleted(self):
        before = payment_rows()
        for name, pk in ((ACCOUNTS, self.row.pk), (SETTINGS, self.settings.pk)):
            with self.subTest(name):
                self.assertEqual(self.hq_get(self.admin_url(f"{name}_add")).status_code, 403)
                change = self.hq_post(
                    self.admin_url(f"{name}_change", pk),
                    {"account_number": "1", "direct_enabled": "on"},
                )
                self.assertEqual(change.status_code, 403)
                delete = self.hq_post(self.admin_url(f"{name}_delete", pk), {"post": "yes"})
                self.assertEqual(delete.status_code, 403)
        self.assertEqual(payment_rows(), before)

    def test_without_an_hq_session_nothing_is_shown(self):
        self.client.cookies.clear()
        response = self.hq_get(self.admin_url(f"{ACCOUNTS}_changelist"))
        self.assertNotEqual(response.status_code, 200)


@override_settings(PAYMENT_METHODS_ENABLED=False)
class SwitchedOffAdminTests(PaymentsAdminTestCase):
    def test_both_lists_are_closed(self):
        index = self.hq_get(self.admin_url("admin:index"))
        self.assertEqual(index.status_code, 200)
        self.assertNotContains(index, "payment account")
        self.assertNotContains(index, "payment settings")
        for name in (f"{ACCOUNTS}_changelist", f"{SETTINGS}_changelist"):
            with self.subTest(name):
                self.assertEqual(self.hq_get(self.admin_url(name)).status_code, 403)
        change = self.hq_get(self.admin_url(f"{ACCOUNTS}_change", self.row.pk))
        self.assertEqual(change.status_code, 403)
