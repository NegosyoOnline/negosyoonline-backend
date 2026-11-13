"""Tenant isolation over the whole owner API (ClickUp z8uq9m7ycc).

A. The guard: every route under /v1/sites/{site_id}/ on the API is in OWNER_ROUTES, so a route
   added later cannot merge without a cross-tenant test.
B. A foreign site id answers the very same 404 as a missing one, and nothing changes.
C. A foreign child id (an object of site B) under the caller's own site is the same 404.
Control. The owner reaches every route, so the 404s above are not a broken fixture.
"""
from apps.common.tests.test_tenant_isolation.owner_base import (
    MISSING_CHILD, MISSING_SITE, OWNER_ROUTES, TwoOwnersTestCase, app_of, child_variants,
    urlconf_routes,
)

NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
# What the guard expects from the URLconf today (7 + 9 + 2 + 6 + 5 + 5 + 4 = 38).
EXPECTED_COUNTS = {
    "sites": 7, "billing": 9, "sante": 2, "catalog": 6, "payments": 5, "pack_owner": 5,
    "meta_ads": 4,
}


class RegistryGuardTests(TwoOwnersTestCase):
    def test_every_route_under_a_site_has_a_cross_tenant_test(self):
        mounted = set(urlconf_routes())
        for method, template in sorted(mounted - set(OWNER_ROUTES)):
            self.fail(
                f"Route {method} {template} under {{site_id}} has no cross-tenant test: add it to "
                "OWNER_ROUTES in apps/common/tests/test_tenant_isolation/owner_base.py and make "
                "the isolation tests call it."
            )
        for method, template in sorted(set(OWNER_ROUTES) - mounted):
            self.fail(f"{method} {template} is in OWNER_ROUTES but no longer mounted: remove it.")

    def test_each_route_is_listed_once_and_counts_per_app_are_as_recorded(self):
        routes = urlconf_routes()
        self.assertEqual(len(routes), len(set(routes)))
        counts = {}
        for _, template in routes:
            counts[app_of(template)] = counts.get(app_of(template), 0) + 1
        self.assertEqual(counts, EXPECTED_COUNTS)
        self.assertEqual(len(OWNER_ROUTES), 38)


class ForeignSiteTests(TwoOwnersTestCase):
    def test_every_route_answers_a_foreign_site_like_a_missing_one_and_changes_nothing(self):
        before = self.state()
        for method, template in OWNER_ROUTES:
            with self.subTest(method=method, template=template):
                foreign = self.call(method, template, self.ben_site_id, self.ben_ids)
                missing = self.call(method, template, MISSING_SITE, self.ben_ids)
                self.assertEqual(foreign.status_code, 404, foreign.content)
                self.assertEqual(foreign.json(), NOT_FOUND)
                self.assertEqual(
                    (foreign.status_code, foreign.content), (missing.status_code, missing.content)
                )
                self.assertUnchanged(before, f"{method} {template}")

    def test_the_other_way_round_ben_cannot_reach_anas_site_either(self):
        ana_ids = self.make_children(self.site)
        before = self.state()
        for method, template in OWNER_ROUTES:
            with self.subTest(method=method, template=template):
                response = self.call(method, template, self.site_id, ana_ids, client=self.ben)
                self.assertEqual(response.status_code, 404, response.content)
                self.assertEqual(response.json(), NOT_FOUND)
                self.assertUnchanged(before, f"{method} {template}")


class ForeignChildTests(TwoOwnersTestCase):
    """The caller's own site id with an object of the other site in the path. The answer is the
    one a missing object gets, and the other site's object is not read, changed or used up."""

    def child_routes(self):
        return [
            (method, template) for method, template in OWNER_ROUTES
            if any("{%s}" % name in template for name in MISSING_CHILD if name != "product_id")
        ]

    def test_the_routes_with_a_child_id_are_the_ones_expected(self):
        self.assertEqual(sorted(self.child_routes()), [
            ("DELETE", "/sites/{site_id}/payment-methods/accounts/{account_id}/"),
            ("GET", "/sites/{site_id}/billing/payments/{payment_id}/"),
            ("GET", "/sites/{site_id}/billing/payments/{payment_id}/proof/"),
            ("GET", "/sites/{site_id}/pack/orders/{order_id}/"),
            ("GET", "/sites/{site_id}/pack/orders/{order_id}/proofs/{proof_id}/"),
            ("POST", "/sites/{site_id}/pack/orders/{order_id}/approve/"),
            ("POST", "/sites/{site_id}/pack/orders/{order_id}/reject/"),
            ("POST", "/sites/{site_id}/payment-methods/accounts/{account_id}/"),
        ])

    def test_a_foreign_child_under_my_own_site_is_the_same_404_as_a_missing_child(self):
        for method, template in self.child_routes():
            own = self.own_ids(template)
            for variant in child_variants(template, own, self.ben_ids):
                with self.subTest(method=method, template=template, ids=variant):
                    before = self.state()
                    response = self.call(method, template, self.site_id, variant)
                    self.assertEqual(response.status_code, 404, response.content)
                    self.assertEqual(response.json(), NOT_FOUND)
                    self.assertUnchanged(before, f"{method} {template}")

    def test_the_answer_is_the_same_bytes_as_for_an_id_that_does_not_exist(self):
        for method, template in self.child_routes():
            own = self.own_ids(template)
            for variant in child_variants(template, own, self.ben_ids):
                swapped = {
                    name: (MISSING_CHILD[name] if variant[name] != own[name] else variant[name])
                    for name in variant
                }
                with self.subTest(method=method, template=template, ids=variant):
                    foreign = self.call(method, template, self.site_id, variant)
                    missing = self.call(method, template, self.site_id, swapped)
                    self.assertEqual(foreign.status_code, missing.status_code)
                    self.assertEqual(foreign.content, missing.content)


class OwnerControlTests(TwoOwnersTestCase):
    def test_the_owner_reaches_every_route_of_their_own_site(self):
        for method, template in OWNER_ROUTES:
            with self.subTest(method=method, template=template):
                response = self.call(method, template, self.site_id, self.own_ids(template))
                self.assertEqual(
                    response.status_code, OWNER_ROUTES[(method, template)], response.content
                )

    def test_ben_reaches_his_own_routes_too_and_never_anas_data_in_them(self):
        ana_ids = self.make_children(self.site)
        self.call("POST", "/sites/{site_id}/business-profile/seal/", self.ben_site_id, self.ben_ids,
                  client=self.ben)  # so Ben's seal read has a badge to answer
        for method, template in OWNER_ROUTES:
            if method != "GET" or app_of(template) == "catalog":  # Ben's site is not the catalog owner
                continue
            with self.subTest(method=method, template=template):
                response = self.call(method, template, self.ben_site_id, self.ben_ids, client=self.ben)
                self.assertEqual(response.status_code, 200, response.content)
                for value in (self.site_id, ana_ids["order_id"], ana_ids["account_id"]):
                    self.assertNotIn(value.encode(), response.content)
