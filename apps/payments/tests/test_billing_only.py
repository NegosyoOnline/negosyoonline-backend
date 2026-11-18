"""Billing's rule for a suspended or ended site (plan D11), on the payment routes: they are
owner functions, so every one answers 403 `billing_only`. The route inventory is Billing's own,
in apps/sites/tests/test_billing_only.py, which walks the payment router too; these are the
payment behaviour tests."""
from django.test import override_settings

from apps.payments.api import router
from apps.payments.tests.base import methods_url, payment_rows, payments_call, switched_on
from apps.sites.tests.test_billing_only import MESSAGE, BillingOnlyTestCase as BillingOnlyBase
from apps.sites.tests.test_isolation import routes_under_a_site


@switched_on
class PaymentsBillingOnlyTests(BillingOnlyBase):
    def setUp(self):
        super().setUp()
        self.url = methods_url(self.site_id)
        payments_call(self, "POST", f"{self.url}accounts/")

    def calls(self, client=None):
        for method, template in sorted(routes_under_a_site(router)):
            path = "/v1" + template.replace("{site_id}", self.site_id)
            yield method, template, payments_call(self, method, path, client=client)

    def test_they_work_while_the_site_is_served(self):
        for method, template, response in self.calls():
            with self.subTest(method=method, template=template):
                self.assertIn(response.status_code, (200, 201), response.content)

    def test_a_suspended_site_answers_403_and_nothing_changes(self):
        self.suspend()
        before = payment_rows()
        for method, template, response in self.calls():
            with self.subTest(method=method, template=template):
                body = self.error(response, 403, "billing_only")
                self.assertEqual(body, {"code": "billing_only", "message": MESSAGE})
                self.assertNotIn("001234567890", response.content.decode())
        self.assertEqual(payment_rows(), before)

    def test_a_site_that_is_not_mine_still_answers_404_never_403(self):
        self.suspend()
        ben = self.other_client()
        for method, template, response in self.calls(client=ben):
            with self.subTest(method=method, template=template):
                self.error(response, 404, "not_found")

    @override_settings(BILLING_ENABLED=False)
    def test_with_billing_off_nothing_is_refused(self):
        self.suspend()
        for method, template, response in self.calls():
            with self.subTest(method=method, template=template):
                self.assertIn(response.status_code, (200, 201), response.content)


@switched_on
class XenditBillingOnlyTests(BillingOnlyBase):
    """The owner's Xendit key routes are owner functions too: 403 `billing_only` once the site is
    suspended or ended, 404 for a site that is not mine, and nothing is stored."""

    def setUp(self):
        super().setUp()
        from cryptography.fernet import Fernet
        patch = override_settings(
            XENDIT_ENABLED=True, XENDIT_FAKE=True, DEBUG=True, PAYMENT_CREDENTIALS_KEY=Fernet.generate_key().decode()
        )
        patch.enable()
        self.addCleanup(patch.disable)
        self.url = f"{methods_url(self.site_id)}xendit/"
        self.body = {"api_key": "xnd_development_abc", "webhook_token": "t"}

    def test_they_work_while_the_site_is_served(self):
        self.assertEqual(self.send("put", self.url, self.body).status_code, 200)
        self.assertEqual(self.send("delete", self.url, None).status_code, 200)

    def test_a_suspended_site_cannot_save_or_remove_a_key(self):
        from apps.payments.models import SiteXenditCredential
        self.suspend()
        self.error(self.send("put", self.url, self.body), 403, "billing_only")
        self.error(self.send("delete", self.url, None), 403, "billing_only")
        self.assertFalse(SiteXenditCredential.objects.exists())

    def test_a_site_that_is_not_mine_still_answers_404_never_403(self):
        self.suspend()
        ben = self.other_client()
        self.error(self.send("put", self.url, self.body, client=ben), 404, "not_found")
        self.error(self.send("delete", self.url, None, client=ben), 404, "not_found")

    @override_settings(BILLING_ENABLED=False)
    def test_with_billing_off_nothing_is_refused(self):
        self.suspend()
        self.assertEqual(self.send("put", self.url, self.body).status_code, 200)
