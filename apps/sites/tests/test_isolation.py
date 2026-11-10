"""Tenant isolation over HTTP: Ana and Ben each have a site; neither can reach the other's."""
from django.utils import timezone

from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL
from apps.sites.api import router
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_site

MISSING_ID = "site_00000000000000000000"


def site_routes():
    """(method, path template) for every route registered under a {site_id}."""
    routes = []
    for path, view in router.path_operations.items():
        if "{site_id}" in path:
            for operation in view.operations:
                routes.extend((method, path) for method in operation.methods)
    return routes


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
        # Send a field the route accepts, so the only reason to refuse is the site itself.
        key = "business_name" if "business-profile" in path else "display_name"
        return self.send(method.lower(), path, {key: "Taken Over"}, client=client)

    def test_every_site_route_is_covered(self):
        self.assertEqual(
            sorted(site_routes()),
            [
                ("GET", "/sites/{site_id}/"),
                ("GET", "/sites/{site_id}/business-profile/"),
                ("PATCH", "/sites/{site_id}/"),
                ("PATCH", "/sites/{site_id}/business-profile/"),
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
            path = "/v1" + template.replace("{site_id}", self.theirs["id"])
            with self.subTest(method=method, template=template):
                self.assertEqual(self.call(method, path, client=self.ben).status_code, 200)

    def test_a_patch_by_another_account_changes_nothing(self):
        before = snapshot()

        self.patch(
            f"/v1/sites/{self.mine['id']}/",
            {"display_name": "Taken Over", "contact_email": "ben@example.test"},
            client=self.ben,
        )
        self.patch(
            f"/v1/sites/{self.mine['id']}/business-profile/",
            {"business_name": "Taken Over"}, client=self.ben,
        )

        self.assertEqual(snapshot(), before)

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
