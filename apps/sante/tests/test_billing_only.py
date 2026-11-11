"""Billing's rule for a suspended or ended site (plan D11), on the Santé routes: they are owner
functions, so both answer 403 `billing_only`. The route inventory is Billing's own, in
apps/sites/tests/test_billing_only.py, which walks the Santé router too; these are the Santé
behaviour tests."""
from django.test import override_settings

from apps.sante.models import SanteSiteSettings
from apps.sante.tests.base import retemplate, settings_url, switched_on
from apps.sites.tests.test_billing_only import MESSAGE, BillingOnlyTestCase as BillingOnlyBase


@switched_on
class SanteBillingOnlyTests(BillingOnlyBase):
    def setUp(self):
        super().setUp()
        self.url = settings_url(self.site_id)
        self.patch(self.url, {"sponsor_id": "SB-1"})

    def calls(self, client=None):
        yield "GET", self.get(self.url, client=client)
        yield "PATCH", self.patch(self.url, {"sponsor_id": "CHANGED-1"}, client=client)

    def test_they_work_while_the_site_is_served(self):
        self.assertEqual(self.get(self.url).json()["sponsor_id"], "SB-1")
        self.assertEqual(self.patch(self.url, {"sponsor_id": "SB-2"}).status_code, 200)

    def test_a_suspended_site_answers_403_and_nothing_changes(self):
        self.suspend()
        for method, response in self.calls():
            with self.subTest(method):
                body = self.error(response, 403, "billing_only")
                self.assertEqual(body, {"code": "billing_only", "message": MESSAGE})
                self.assertNotIn("SB-1", response.content.decode())
        self.assertEqual(SanteSiteSettings.objects.get(site=self.site).sponsor_id, "SB-1")

    def test_a_site_that_is_not_mine_or_not_sante_still_answers_404_never_403(self):
        self.suspend()
        ben = self.other_client()
        for method, response in self.calls(client=ben):
            with self.subTest(method, who="another account"):
                self.error(response, 404, "not_found")
        retemplate(self.site)
        for method, response in self.calls():
            with self.subTest(method, who="not a Santé site"):
                self.error(response, 404, "not_found")

    @override_settings(BILLING_ENABLED=False)
    def test_with_billing_off_nothing_is_refused(self):
        self.suspend()
        for method, response in self.calls():
            with self.subTest(method):
                self.assertEqual(response.status_code, 200, response.content)
