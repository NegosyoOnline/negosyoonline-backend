"""Tenant isolation over HTTP: Ana and Ben each have a site; neither can reach the other's."""
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Account, AccountAuditEvent
from apps.accounts.tests.base import EMAIL
from apps.billing import services as billing
from apps.billing.api import router as billing_router
from apps.billing.models import BillingCycle, Subscription, SubscriptionTransition
from apps.sites import services
from apps.sites.api import router
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_site

MISSING_ID = "site_00000000000000000000"
# Every field a subscriber may edit, with a valid value that differs from a new site's.
SITE_BODY = {
    "display_name": "Taken Over",
    "contact_email": "taken@example.test",
    "contact_mobile": "09181234567",
    "facebook_url": "https://facebook.com/taken",
    "instagram_url": "https://instagram.com/taken",
    "tiktok_url": "https://tiktok.com/@taken",
}
PROFILE_BODY = {
    "business_name": "Taken Over Trading",
    "registered_name": "Taken Over Inc.",
    "tin": "987654321",
    "dti_business_number": "7654321",
    "bir_rsn": "113RC20260000017598",
    "business_address": "9 Taken St, Cebu",
    "complaints_contact_name": "Taken Office",
    "privacy_contact_email": "privacy-taken@example.test",
    "shipping_processing_time": "9 days",
    "shipping_metro_manila_estimate": "9 days",
    "shipping_luzon_estimate": "9 days",
    "shipping_visayas_estimate": "9 days",
    "shipping_mindanao_estimate": "9 days",
    "shipping_other_areas_estimate": "9 days",
    "shipping_estimates_confirmed": True,
}


def body_for(path):
    return PROFILE_BODY if "business-profile" in path else SITE_BODY


def routes_under_a_site(a_router):
    """(method, path template) for every route a router registers under a {site_id}."""
    routes = []
    for path, view in a_router.path_operations.items():
        if "{site_id}" in path:
            for operation in view.operations:
                routes.extend((method, path) for method in operation.methods)
    return routes


def site_routes():
    return routes_under_a_site(router)


def billing_routes():
    return routes_under_a_site(billing_router)


def snapshot():
    return (
        list(Site.objects.order_by("pk").values()),
        list(SiteBusinessProfile.objects.order_by("pk").values()),
        list(Subdomain.objects.order_by("pk").values()),
        list(Onboarding.objects.order_by("pk").values()),
    )


class IsolationTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.mine = self.create_site()
        self.ben = self.other_client()
        self.theirs = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")
        deleted = make_site(self.account, name="old-store", first=False, deleted_at=timezone.now())
        self.deleted_id = deleted.public_id

    def call(self, method, path, client=None):
        if method == "GET":
            return self.get(path, client=client)
        # Send every field the route accepts, so the only reason to refuse is the site itself.
        return self.send(method.lower(), path, body_for(path), client=client)

    def rows_of(self, site_id):
        """Every stored row that belongs to one site and its account."""
        site = Site.objects.get(public_id=site_id)
        return (
            list(Site.objects.filter(pk=site.pk).values()),
            list(SiteBusinessProfile.objects.filter(site=site).values()),
            list(Subdomain.objects.filter(site=site).values()),
            list(Onboarding.objects.filter(account=site.account).values()),
        )

    def test_the_bodies_cover_every_editable_field(self):
        self.assertEqual(
            (sorted(SITE_BODY), sorted(PROFILE_BODY)),
            (sorted(services.SITE_FIELDS), sorted(services.PROFILE_FIELDS)),
            "An editable field was added or removed: keep the isolation bodies in step.",
        )

    def test_every_site_route_is_covered(self):
        self.assertEqual(
            sorted(site_routes()),
            [
                ("DELETE", "/sites/{site_id}/business-profile/seal/"),
                ("GET", "/sites/{site_id}/"),
                ("GET", "/sites/{site_id}/business-profile/"),
                ("GET", "/sites/{site_id}/business-profile/seal/"),
                ("PATCH", "/sites/{site_id}/"),
                ("PATCH", "/sites/{site_id}/business-profile/"),
                ("POST", "/sites/{site_id}/business-profile/seal/"),
            ],
            "A site route was added or removed: add it to the isolation tests.",
        )

    def test_another_accounts_site_is_404_on_every_site_route_and_method(self):
        before = snapshot()

        for method, template in site_routes():
            answers = set()
            for site_id in (self.theirs["id"], MISSING_ID, self.deleted_id):
                path = "/v1" + template.replace("{site_id}", site_id)
                response = self.call(method, path)
                self.assertEqual(response.status_code, 404, (method, path, response.content))
                answers.add(response.content)
            with self.subTest(method=method, template=template):
                # Foreign, missing and deleted answer byte for byte the same.
                self.assertEqual(len(answers), 1)
                self.assertEqual(
                    self.error(response, 404, "not_found"),
                    {"code": "not_found", "message": "Not found."},
                )

        self.assertEqual(snapshot(), before)

    def test_the_owner_still_reaches_their_own_site_on_every_route(self):
        for method, template in site_routes():
            if template.endswith("/seal/"):
                continue  # needs a file or a stored badge: the owner's own tests (test_seal.py) reach all three
            path = "/v1" + template.replace("{site_id}", self.theirs["id"])
            with self.subTest(method=method, template=template):
                self.assertEqual(self.call(method, path, client=self.ben).status_code, 200)

    def test_a_patch_by_another_account_changes_nothing(self):
        before = snapshot()

        site_path = f"/v1/sites/{self.mine['id']}/"
        for path in (site_path, site_path + "business-profile/"):
            whole = self.patch(path, body_for(path), client=self.ben)
            self.assertEqual(whole.status_code, 404, whole.content)
            for key, value in body_for(path).items():
                with self.subTest(path=path, key=key):
                    response = self.patch(path, {key: value}, client=self.ben)
                    self.assertEqual(response.status_code, 404, response.content)

        self.assertEqual(snapshot(), before)
        self.assertFalse(AccountAuditEvent.objects.filter(
            event__in=("site_updated", "business_profile_updated")
        ).exists())

    def test_each_patch_on_my_site_changes_only_my_rows(self):
        theirs = self.rows_of(self.theirs["id"])
        deleted = self.rows_of(self.deleted_id)[:3]
        mine = Site.objects.get(public_id=self.mine["id"])

        for path, row in (
            (f"/v1/sites/{self.mine['id']}/", lambda: Site.objects.get(pk=mine.pk)),
            (f"/v1/sites/{self.mine['id']}/business-profile/",
             lambda: SiteBusinessProfile.objects.get(site=mine)),
        ):
            for key, value in body_for(path).items():
                with self.subTest(path=path, key=key):
                    was = getattr(row(), key)
                    response = self.patch(path, {key: value})
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertNotEqual(getattr(row(), key), was)
                    self.assertEqual(self.rows_of(self.theirs["id"]), theirs)
                    self.assertEqual(self.rows_of(self.deleted_id)[:3], deleted)

    def test_a_patch_to_my_second_site_leaves_my_first_site_unchanged(self):
        # The service caps creation at one site, so the second live site is written directly.
        second = make_site(
            self.account, name="anas-second", display_name="Ana's Second", first=False
        )
        first = self.rows_of(self.mine["id"])
        theirs = self.rows_of(self.theirs["id"])

        for path, row in (
            (f"/v1/sites/{second.public_id}/", lambda: Site.objects.get(pk=second.pk)),
            (f"/v1/sites/{second.public_id}/business-profile/",
             lambda: SiteBusinessProfile.objects.get(site=second)),
        ):
            for key, value in body_for(path).items():
                with self.subTest(path=path, key=key):
                    was = getattr(row(), key)
                    response = self.patch(path, {key: value})
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertNotEqual(getattr(row(), key), was)
                    self.assertEqual(self.rows_of(self.mine["id"]), first)
                    self.assertEqual(self.rows_of(self.theirs["id"]), theirs)

        # Each read of the first site still answers its own values, not the second site's.
        self.assertEqual(self.get(f"/v1/sites/{self.mine['id']}/").json()["display_name"],
                         self.mine["display_name"])
        self.assertNotEqual(
            self.get(f"/v1/sites/{self.mine['id']}/business-profile/").json()["business_name"],
            PROFILE_BODY["business_name"],
        )

    def test_the_audit_event_for_each_save_names_my_account_and_my_site_only(self):
        ben = Account.objects.get(email="ben@example.test")
        AccountAuditEvent.objects.all().delete()

        for path, event in (
            (f"/v1/sites/{self.mine['id']}/", "site_updated"),
            (f"/v1/sites/{self.mine['id']}/business-profile/", "business_profile_updated"),
        ):
            for key, value in body_for(path).items():
                with self.subTest(path=path, key=key):
                    self.assertEqual(self.patch(path, {key: value}).status_code, 200)
                    saved = AccountAuditEvent.objects.order_by("-pk").first()
                    self.assertEqual((saved.account_id, saved.event), (self.account.pk, event))
                    self.assertEqual(saved.detail, {"site": self.mine["id"], "fields": [key]})

        self.assertEqual(AccountAuditEvent.objects.count(), len(SITE_BODY) + len(PROFILE_BODY))
        self.assertFalse(AccountAuditEvent.objects.filter(account=ben).exists())
        for saved in AccountAuditEvent.objects.all():
            self.assertNotIn(self.theirs["id"], str(saved.detail))
            self.assertNotIn(ben.public_id, str(saved.detail))

    def test_the_list_never_contains_another_accounts_site(self):
        mine = [site["id"] for site in self.get("/v1/sites/").json()["items"]]
        theirs = [site["id"] for site in self.get("/v1/sites/", client=self.ben).json()["items"]]

        self.assertEqual(mine, [self.mine["id"]])
        self.assertEqual(theirs, [self.theirs["id"]])

    def test_setup_never_returns_another_accounts_first_site(self):
        self.assertEqual(self.get("/v1/account/setup/").json()["first_site"]["id"], self.mine["id"])
        self.assertEqual(
            self.get("/v1/account/setup/", client=self.ben).json()["first_site"]["id"],
            self.theirs["id"],
        )

    def test_a_site_or_account_id_in_the_body_or_query_is_never_authorization(self):
        account = Account.objects.get(email=EMAIL)
        before = snapshot()

        listed = self.get(
            f"/v1/sites/?account={account.public_id}&site={self.mine['id']}", client=self.ben
        )
        patched = self.patch(
            f"/v1/sites/{self.theirs['id']}/?site_id={self.mine['id']}",
            {"display_name": "Ben's New Name"}, client=self.ben,
        )
        smuggled = self.patch(
            f"/v1/sites/{self.theirs['id']}/",
            {"display_name": "x", "site_id": self.mine["id"], "account": account.public_id},
            client=self.ben,
        )

        self.assertEqual([s["id"] for s in listed.json()["items"]], [self.theirs["id"]])
        self.assertEqual(patched.json()["id"], self.theirs["id"])
        self.error(smuggled, 400, "invalid_request")
        after = snapshot()
        self.assertEqual(after[0][0], before[0][0])  # Ana's site row is untouched
        self.assertEqual(Site.objects.get(public_id=self.theirs["id"]).display_name, "Ben's New Name")

    def test_one_accounts_taken_name_tells_the_other_nothing_but_taken(self):
        response = self.post(
            "/v1/sites/subdomain-check/", {"subdomain": "anas-store"}, client=self.ben
        )

        self.assertEqual(response.json(), {
            "subdomain": "anas-store", "status": "taken",
            "message": "That name is already taken. Please choose another.",
        })


BILLING_EVENTS = ("subscription_cancelled", "subscription_cancellation_withdrawn")


def billing_snapshot():
    return (
        list(Subscription.objects.order_by("pk").values()),
        list(SubscriptionTransition.objects.order_by("pk").values()),
        list(BillingCycle.objects.order_by("pk").values()),
    )


@override_settings(BILLING_ENABLED=True)
class BillingIsolationTests(SitesApiTestCase):
    """The billing routes: another account's site, a missing one and a removed one are 404."""

    def setUp(self):
        super().setUp()
        self.mine = self.create_site()
        self.ben = self.other_client()
        self.theirs = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")
        # A removed site that still has its subscription: the 404 comes before any billing read.
        deleted = make_site(self.account, name="old-store", first=False)
        billing.start_subscription(deleted, self.account)
        Site.objects.filter(pk=deleted.pk).update(deleted_at=timezone.now())
        self.deleted_id = deleted.public_id

    def call(self, method, path, client=None):
        return self.get(path, client=client) if method == "GET" else self.post(path, client=client)

    def test_every_billing_route_is_covered(self):
        self.assertEqual(
            sorted(billing_routes()),
            [
                ("GET", "/sites/{site_id}/billing/"),
                ("GET", "/sites/{site_id}/billing/payment/"),
                ("GET", "/sites/{site_id}/billing/payments/"),
                ("GET", "/sites/{site_id}/billing/payments/{payment_id}/"),
                ("GET", "/sites/{site_id}/billing/payments/{payment_id}/proof/"),
                ("GET", "/sites/{site_id}/billing/usage/"),
                ("POST", "/sites/{site_id}/billing/cancel/"),
                ("POST", "/sites/{site_id}/billing/payments/"),
                ("POST", "/sites/{site_id}/billing/reactivate/"),
            ],
            "A billing route was added or removed: add it to the isolation tests.",
        )
        # The one route outside a site is the public plan list (Billing Build B3): it names no
        # site, needs no sign-in and reads no billing table.
        outside = [p for p in billing_router.path_operations if "{site_id}" not in p]
        self.assertEqual(outside, ["/plan/"])
        self.assertEqual(len(billing_router.path_operations), 9)

    def test_another_accounts_site_is_404_on_every_billing_route_and_changes_nothing(self):
        before = billing_snapshot()

        for method, template in billing_routes():
            answers = set()
            for site_id in (self.theirs["id"], MISSING_ID, self.deleted_id):
                path = "/v1" + template.replace("{site_id}", site_id)
                response = self.call(method, path)
                self.assertEqual(response.status_code, 404, (method, path, response.content))
                answers.add(response.content)
            with self.subTest(method=method, template=template):
                # Foreign, missing and removed answer byte for byte the same.
                self.assertEqual(len(answers), 1)
                self.assertEqual(
                    self.error(response, 404, "not_found"),
                    {"code": "not_found", "message": "Not found."},
                )

        self.assertEqual(billing_snapshot(), before)
        self.assertFalse(AccountAuditEvent.objects.filter(event__in=BILLING_EVENTS).exists())

    def test_a_site_id_in_the_body_or_query_is_never_authorization(self):
        before = billing_snapshot()
        path = f"/v1/sites/{self.mine['id']}/billing/"

        read = self.get(f"{path}?site_id={self.theirs['id']}", client=self.ben)
        cancel = self.post(f"{path}cancel/", {"site_id": self.theirs["id"]}, client=self.ben)

        self.error(read, 404, "not_found")
        self.error(cancel, 404, "not_found")  # the site is checked before the body is read
        self.assertEqual(billing_snapshot(), before)

    def test_the_owner_reaches_their_own_site_on_every_billing_route(self):
        for method, template in billing_routes():
            path = "/v1" + template.replace("{site_id}", self.theirs["id"])
            with self.subTest(method=method, template=template):
                # Reactivate on a running trial is refused by the rule, not by the site.
                expected = 409 if template.endswith("reactivate/") else 200
                if template.endswith("/billing/usage/"):
                    expected = 404  # B4's switch is off here; test_usage_api.py covers it on
                if "/billing/payment" in template:
                    # Bank transfer is switched off here, so its routes are 404 for the
                    # owner too; apps/billing/tests/test_transfer_api.py covers them on.
                    expected = 404
                response = self.call(method, path, client=self.ben)
                self.assertEqual(response.status_code, expected, response.content)

    def test_a_cancel_on_my_site_changes_only_my_subscription(self):
        ben = Account.objects.get(email="ben@example.test")
        theirs = Subscription.objects.filter(site__public_id=self.theirs["id"])
        removed = Subscription.objects.filter(site__public_id=self.deleted_id)
        before = (list(theirs.values()), list(removed.values()))

        response = self.post(f"/v1/sites/{self.mine['id']}/billing/cancel/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["state"], "cancelled")
        self.assertEqual((list(theirs.values()), list(removed.values())), before)
        self.assertEqual(
            list(theirs.get().transitions.values_list("code", flat=True)), ["trial_started"]
        )
        event = AccountAuditEvent.objects.get(event="subscription_cancelled")
        self.assertEqual((event.account_id, event.detail), (self.account.pk, {"site": self.mine["id"]}))
        self.assertFalse(AccountAuditEvent.objects.filter(account=ben, event__in=BILLING_EVENTS).exists())
        mine = self.get(f"/v1/sites/{self.theirs['id']}/billing/", client=self.ben).json()
        self.assertEqual(mine["state"], "trial_active")
