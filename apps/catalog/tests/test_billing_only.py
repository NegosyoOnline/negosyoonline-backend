"""Billing's rule for a suspended or ended site (plan D11), on the catalog routes: they are
owner functions, so all four answer 403 `billing_only`, after the 404 check and before the
body is read. The route inventory is Billing's own, in apps/sites/tests/test_billing_only.py,
which walks the catalog router too; these are the catalog behaviour tests. Restates TWCinc's
test_billing_only_states."""
from django.test import override_settings

from apps.billing.tests.base import days
from apps.catalog.tests.base import (
    VALID_CREATE, catalog_rows, product_url, products_url, retemplate, revisions_url,
)
from apps.sites.tests.test_billing_only import MESSAGE, BillingOnlyTestCase as BillingOnlyBase


class CatalogBillingOnlyTests(BillingOnlyBase):
    """The base names self.site the catalog owner and makes self.product."""

    def calls(self, client=None, create=None):
        product_id = self.product.public_id
        yield "list", self.get(products_url(self.site_id), client=client)
        yield "create", self.post(
            products_url(self.site_id), create or {**VALID_CREATE, "code": "FX-MADE"}, client=client
        )
        yield "update", self.patch(
            product_url(self.site_id, product_id), {"revision": 1, "name": "Changed"}, client=client
        )
        yield "revisions", self.get(revisions_url(self.site_id, product_id), client=client)

    def test_they_work_while_the_site_is_served(self):
        answers = {name: response.status_code for name, response in self.calls()}
        self.assertEqual(answers, {"list": 200, "create": 201, "update": 200, "revisions": 200})

    def test_a_suspended_owner_site_answers_403_and_nothing_changes(self):
        self.suspend()
        before = catalog_rows()
        for name, response in self.calls():
            with self.subTest(name):
                body = self.error(response, 403, "billing_only")
                self.assertEqual(body, {"code": "billing_only", "message": MESSAGE})
                self.assertNotIn(self.product.name, response.content.decode())
        self.assertEqual(catalog_rows(), before)

    def test_the_refusal_comes_before_the_body_is_read(self):
        self.suspend()
        for name, response in self.calls(create={"bad": 1}):
            with self.subTest(name):
                self.error(response, 403, "billing_only")

    def test_an_ended_site_that_only_reads_the_catalog_answers_403_too(self):
        ben = self.other_client()
        ben_site_id = self.create_site(client=ben, subdomain="bens-store")["id"]
        self.assertEqual(self.get(products_url(ben_site_id), client=ben).status_code, 200)
        self.at(self.trial_end + days(2))  # his trial, a day younger, ended with no payment method
        self.error(self.get(products_url(ben_site_id), client=ben), 403, "billing_only")

    def test_a_site_that_is_not_mine_or_not_sante_still_answers_404_never_403(self):
        self.suspend()
        ben = self.other_client()
        for name, response in self.calls(client=ben):
            with self.subTest(name, who="another account"):
                self.error(response, 404, "not_found")
        retemplate(self.site)
        for name, response in self.calls():
            with self.subTest(name, who="not a Santé site"):
                self.error(response, 404, "not_found")

    @override_settings(BILLING_ENABLED=False)
    def test_with_billing_off_nothing_is_refused(self):
        self.suspend()
        answers = {name: response.status_code for name, response in self.calls()}
        self.assertEqual(answers, {"list": 200, "create": 201, "update": 200, "revisions": 200})
