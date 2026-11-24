"""Tenant isolation over the whole owner API (ClickUp z8uq9m7ycc).

A. The guard: every route under /v1/sites/{site_id}/ on the API is in OWNER_ROUTES, so a route
   added later cannot merge without a cross-tenant test.
B. A foreign site id answers the very same 404 as a missing one, and nothing changes.
C. A foreign child id (an object of site B) under the caller's own site is the same 404.
Control. The owner reaches every route, so the 404s above are not a broken fixture.
"""
from unittest import mock

from django.test import override_settings

from apps.billing import plan
from apps.common.tests.test_tenant_isolation.owner_base import (
    MISSING_CHILD, MISSING_SITE, OWNER_ROUTES, TwoOwnersTestCase, app_of, child_variants,
    urlconf_routes,
)

NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
# What the guard expects from the URLconf today (12 + 17 + 2 + 10 + 7 + 13 + 10 + 4 + 31 + 6 + 28 + 11 + 2 + 4 + 4 + 12 = 173). The
# "sites" count includes the 5 member-recovery routes under /sites/{site_id}/members/.
EXPECTED_COUNTS = {
    "sites": 12, "billing": 17, "sante": 2, "catalog": 10, "payments": 7, "pack_owner": 13,
    "shop_owner": 10, "meta_ads": 4, "inquiries": 31, "member_notices": 6,
    "training": 28, "media": 11, "shared_content": 2, "domains": 4, "sms_line": 4, "contacts": 12,
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
        self.assertEqual(len(OWNER_ROUTES), 173)


class ForeignSiteTests(TwoOwnersTestCase):
    def test_every_route_answers_a_foreign_site_like_a_missing_one_and_changes_nothing(self):
        before = self.state()
        for method, template in OWNER_ROUTES:
            with self.subTest(method=method, template=template):
                foreign = self.call(method, template, self.ben_site_id, self.foreign_ids(template))
                missing = self.call(method, template, MISSING_SITE, self.foreign_ids(template))
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
            ("DELETE", "/sites/{site_id}/contacts/groups/{group_id}/"),
            ("DELETE", "/sites/{site_id}/contacts/{contact_id}/"),
            ("DELETE", "/sites/{site_id}/inquiries/comment-rules/{ref}/"),
            ("DELETE", "/sites/{site_id}/media/{asset_id}/"),
            ("DELETE", "/sites/{site_id}/media/{asset_id}/image/"),
            ("DELETE", "/sites/{site_id}/member-notices/{notice_id}/"),
            ("DELETE", "/sites/{site_id}/member-notices/{notice_id}/image/"),
            ("DELETE", "/sites/{site_id}/payment-methods/accounts/{account_id}/"),
            ("DELETE", "/sites/{site_id}/training/courses/{course_id}/"),
            ("DELETE", "/sites/{site_id}/training/courses/{course_id}/image/"),
            ("DELETE", "/sites/{site_id}/training/lessons/{lesson_id}/"),
            ("DELETE", "/sites/{site_id}/training/lessons/{lesson_id}/image/"),
            ("DELETE", "/sites/{site_id}/training/lessons/{lesson_id}/video/"),
            ("DELETE", "/sites/{site_id}/training/live/{live_id}/"),
            ("DELETE", "/sites/{site_id}/training/live/{live_id}/thumbnail/"),
            ("DELETE", "/sites/{site_id}/training/modules/{module_id}/"),
            ("GET", "/sites/{site_id}/billing/invoices/{invoice_id}/copy/"),
            ("GET", "/sites/{site_id}/billing/payments/{payment_id}/"),
            ("GET", "/sites/{site_id}/billing/payments/{payment_id}/proof/"),
            ("GET", "/sites/{site_id}/billing/statements/{number}/"),
            ("GET", "/sites/{site_id}/billing/statements/{number}/pdf/"),
            ("GET", "/sites/{site_id}/contacts/{contact_id}/"),
            ("GET", "/sites/{site_id}/inquiries/comment-rules/{ref}/"),
            ("GET", "/sites/{site_id}/inquiries/comments/{ref}/"),
            ("GET", "/sites/{site_id}/inquiries/conversations/{ref}/"),
            ("GET", "/sites/{site_id}/members/{member_id}/recovery/"),
            ("GET", "/sites/{site_id}/pack/orders/{order_id}/"),
            ("GET", "/sites/{site_id}/pack/orders/{order_id}/proofs/{proof_id}/"),
            ("GET", "/sites/{site_id}/shop/orders/{order_id}/"),
            ("GET", "/sites/{site_id}/shop/orders/{order_id}/proofs/{proof_id}/"),
            ("GET", "/sites/{site_id}/training/courses/{course_id}/"),
            ("GET", "/sites/{site_id}/training/courses/{course_id}/watch/"),
            ("GET", "/sites/{site_id}/training/lessons/{lesson_id}/video/"),
            ("PATCH", "/sites/{site_id}/contacts/groups/{group_id}/"),
            ("PATCH", "/sites/{site_id}/contacts/{contact_id}/"),
            ("PATCH", "/sites/{site_id}/media/{asset_id}/"),
            ("PATCH", "/sites/{site_id}/member-notices/{notice_id}/"),
            ("PATCH", "/sites/{site_id}/training/courses/{course_id}/"),
            ("PATCH", "/sites/{site_id}/training/lessons/{lesson_id}/"),
            ("PATCH", "/sites/{site_id}/training/live/{live_id}/"),
            ("PATCH", "/sites/{site_id}/training/modules/{module_id}/"),
            ("POST", "/sites/{site_id}/inquiries/comments/{ref}/hide/"),
            ("POST", "/sites/{site_id}/inquiries/comments/{ref}/private-reply/"),
            ("POST", "/sites/{site_id}/inquiries/comments/{ref}/read/"),
            ("POST", "/sites/{site_id}/inquiries/comments/{ref}/reply/"),
            ("POST", "/sites/{site_id}/inquiries/conversations/{ref}/close/"),
            ("POST", "/sites/{site_id}/inquiries/conversations/{ref}/read/"),
            ("POST", "/sites/{site_id}/inquiries/conversations/{ref}/reopen/"),
            ("POST", "/sites/{site_id}/inquiries/conversations/{ref}/reply/"),
            ("POST", "/sites/{site_id}/media/{asset_id}/image/"),
            ("POST", "/sites/{site_id}/media/{asset_id}/publish/"),
            ("POST", "/sites/{site_id}/media/{asset_id}/unpublish/"),
            ("POST", "/sites/{site_id}/media/{asset_id}/video/attach/"),
            ("POST", "/sites/{site_id}/media/{asset_id}/video/token/"),
            ("POST", "/sites/{site_id}/member-notices/{notice_id}/image/"),
            ("POST", "/sites/{site_id}/members/{member_id}/recovery/"),
            ("POST", "/sites/{site_id}/members/{member_id}/recovery/check/"),
            ("POST", "/sites/{site_id}/pack/orders/{order_id}/approve/"),
            ("POST", "/sites/{site_id}/pack/orders/{order_id}/delivery/"),
            ("POST", "/sites/{site_id}/pack/orders/{order_id}/reject/"),
            ("POST", "/sites/{site_id}/pack/orders/{order_id}/shipment/"),
            ("POST", "/sites/{site_id}/pack/orders/{order_id}/shipment/correct/"),
            ("POST", "/sites/{site_id}/pack/registrations/{member_id}/registered/"),
            ("POST", "/sites/{site_id}/pack/registrations/{member_id}/start/"),
            ("POST", "/sites/{site_id}/payment-methods/accounts/{account_id}/"),
            ("POST", "/sites/{site_id}/shop/orders/{order_id}/cancel/"),
            ("POST", "/sites/{site_id}/shop/orders/{order_id}/confirm-receipt/"),
            ("POST", "/sites/{site_id}/shop/orders/{order_id}/deliver/"),
            ("POST", "/sites/{site_id}/shop/orders/{order_id}/ship/"),
            ("POST", "/sites/{site_id}/shop/reviews/{review_id}/hide/"),
            ("POST", "/sites/{site_id}/shop/reviews/{review_id}/show/"),
            ("POST", "/sites/{site_id}/training/courses/{course_id}/image/"),
            ("POST", "/sites/{site_id}/training/courses/{course_id}/modules/"),
            ("POST", "/sites/{site_id}/training/courses/{course_id}/modules/reorder/"),
            ("POST", "/sites/{site_id}/training/lessons/{lesson_id}/image/"),
            ("POST", "/sites/{site_id}/training/lessons/{lesson_id}/video/upload/"),
            ("POST", "/sites/{site_id}/training/live/{live_id}/thumbnail/"),
            ("POST", "/sites/{site_id}/training/modules/{module_id}/lessons/"),
            ("POST", "/sites/{site_id}/training/modules/{module_id}/lessons/reorder/"),
            ("PUT", "/sites/{site_id}/inquiries/comment-rules/{ref}/"),
        ])

    def test_a_foreign_child_under_my_own_site_is_the_same_404_as_a_missing_child(self):
        for method, template in self.child_routes():
            own = self.own_ids(template)
            for variant in child_variants(template, own, self.foreign_ids(template)):
                with self.subTest(method=method, template=template, ids=variant):
                    before = self.state()
                    response = self.call(method, template, self.site_id, variant)
                    self.assertEqual(response.status_code, 404, response.content)
                    self.assertEqual(response.json(), NOT_FOUND)
                    self.assertUnchanged(before, f"{method} {template}")

    def test_the_answer_is_the_same_bytes_as_for_an_id_that_does_not_exist(self):
        for method, template in self.child_routes():
            own = self.own_ids(template)
            for variant in child_variants(template, own, self.foreign_ids(template)):
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
        ana_shop = self.make_children(self.site, shop=True)
        self.call("POST", "/sites/{site_id}/business-profile/seal/", self.ben_site_id, self.ben_ids,
                  client=self.ben)  # so Ben's seal read has a badge to answer
        for method, template in OWNER_ROUTES:
            if method != "GET" or app_of(template) == "catalog":  # Ben's site is not the catalog owner
                continue
            with self.subTest(method=method, template=template):
                response = self.call(method, template, self.ben_site_id, self.foreign_ids(template), client=self.ben)
                # the Messenger start (302) and pick list (410) are not 200 for anyone, Ben included
                self.assertEqual(response.status_code, OWNER_ROUTES[(method, template)], response.content)
                for value in (self.site_id, ana_ids["order_id"], ana_ids["account_id"], ana_shop["order_id"],
                              ana_ids["member_id"], ana_ids["invoice_id"], ana_ids["number"]):
                    self.assertNotIn(value.encode(), response.content)


class UsageSwitchTests(TwoOwnersTestCase):
    """GET .../billing/usage/ is open only while BILLING_USAGE_ENABLED is on AND storage is
    metered (plan.STORAGE_METERED, False at launch). The table above runs in the usage-on world;
    here the route is tried with the switch on and off."""
    PATH = "/sites/{site_id}/billing/usage/"

    def usage(self, site_id, client=None):
        return self.call("GET", self.PATH, site_id, {}, client=client)

    def test_on_the_owner_gets_200_and_every_other_tenant_the_same_404_as_a_missing_site(self):
        self.assertEqual(self.usage(self.site_id).status_code, 200)
        self.assertEqual(self.usage(self.ben_site_id, client=self.ben).status_code, 200)
        before = self.state()
        foreign = self.usage(self.ben_site_id)
        missing = self.usage(MISSING_SITE)
        other_way = self.usage(self.site_id, client=self.ben)
        for response in (foreign, other_way):
            self.assertEqual(response.status_code, 404, response.content)
            self.assertEqual(response.json(), NOT_FOUND)
        self.assertEqual((foreign.status_code, foreign.content), (missing.status_code, missing.content))
        self.assertUnchanged(before, "usage")

    def test_off_every_tenant_gets_404_and_nothing_changes(self):
        cases = {
            "the switch off": ({"BILLING_USAGE_ENABLED": False}, True),
            "billing off": ({"BILLING_ENABLED": False}, True),
            "storage not metered (launch)": ({}, False),
        }
        for name, (settings_off, metered) in cases.items():
            with self.subTest(case=name), override_settings(**settings_off), \
                    mock.patch.object(plan, "STORAGE_METERED", metered):
                before = self.state()
                for site_id, client in ((self.site_id, None), (self.ben_site_id, self.ben),
                                        (self.ben_site_id, None), (MISSING_SITE, None)):
                    response = self.usage(site_id, client=client)
                    self.assertEqual(response.status_code, 404, response.content)
                    self.assertEqual(response.json(), NOT_FOUND)
                self.assertUnchanged(before, "usage")
