from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL
from apps.sites.models import Onboarding, Site, SiteBusinessProfile
from apps.sites.tests.api_base import SitesApiTestCase

SETUP = "/v1/account/setup/"
SETUP_KEYS = {
    "state", "blocked_reason", "missing", "first_site", "can_create_site", "templates",
    "site_host_suffix", "started_at", "minimum_completed_at",
}
ALL = ["business_name", "site_name", "public_contact"]


class SetupApiTests(SitesApiTestCase):
    def setup(self, client=None):
        response = self.get(SETUP, client=client)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_the_shape_for_a_fresh_account(self):
        self.assertEqual(self.setup(), {
            "state": "not_started",
            "blocked_reason": None,
            "missing": ALL,
            "first_site": None,
            "can_create_site": True,
            "templates": [{"id": "sante", "label": "Santé"}],
            "site_host_suffix": "negosyotest.com",
            "started_at": None,
            "minimum_completed_at": None,
        })

    def test_create_then_complete_in_order(self):
        site = self.create_site()
        path = f"/v1/sites/{site['id']}/"

        after_create = self.setup()
        self.assertEqual(set(after_create), SETUP_KEYS)
        self.assertEqual(after_create["state"], "in_progress")
        self.assertEqual(after_create["missing"], ["business_name", "public_contact"])
        self.assertEqual(after_create["first_site"], {
            "id": site["id"], "subdomain": "anas-store", "display_name": "Ana's Store",
            "deleted": False,
        })
        self.assertFalse(after_create["can_create_site"])
        self.assertIsNotNone(after_create["started_at"])
        self.assertIsNone(after_create["minimum_completed_at"])

        self.patch(path, {"contact_email": "a@example.test"})
        self.assertEqual(self.setup()["missing"], ["business_name", "public_contact"])
        self.patch(path, {"contact_mobile": "09171234567"})
        self.assertEqual(self.setup()["missing"], ["business_name"])
        self.patch(path + "business-profile/", {"business_name": "Ana Trading"})

        done = self.setup()
        self.assertEqual(done["state"], "minimum_complete")
        self.assertEqual(done["missing"], [])
        self.assertEqual(done["started_at"], after_create["started_at"])
        self.assertIsNotNone(done["minimum_completed_at"])

        # The API refuses to blank a required contact, so a finished site stays finished.
        self.assertEqual(self.patch(path, {"contact_mobile": ""}).status_code, 400)
        self.assertEqual(self.setup(), done)

        # The state is still derived from what is stored, and the stamp is never cleared.
        Site.objects.filter(public_id=site["id"]).update(contact_mobile="")
        cleared = self.setup()
        self.assertEqual(cleared["state"], "in_progress")
        self.assertEqual(cleared["missing"], ["public_contact"])
        self.assertEqual(cleared["minimum_completed_at"], done["minimum_completed_at"])

    def test_a_new_sign_in_on_another_client_returns_the_same_setup(self):
        site = self.create_site()
        self.patch(f"/v1/sites/{site['id']}/", {"contact_email": "a@example.test"})
        before = self.setup()

        second_device = Client()
        self.sign_in(client=second_device)  # same email: this also signs the first client out

        self.assertEqual(self.setup(client=second_device), before)
        again = self.post(
            "/v1/sites/",
            {"template": "sante", "subdomain": "second-store", "display_name": "Second"},
            client=second_device,
        )
        self.error(again, 409, "site_limit_reached")
        self.assertEqual(Site.objects.count(), 1)

    def test_a_client_cannot_mark_onboarding_complete(self):
        site = self.create_site()
        path = f"/v1/sites/{site['id']}/"

        for key, value in (
            ("state", "minimum_complete"), ("minimum_completed_at", "2026-01-01T00:00:00Z"),
            ("missing", []), ("onboarding_complete", True), ("started_at", None),
        ):
            for target in (path, path + "business-profile/"):
                with self.subTest(key=key, target=target):
                    response = self.patch(target, {key: value})
                    self.assertIn(key, self.error(response, 400, "invalid_request")["fields"])
        for method in ("post", "patch", "put", "delete"):
            with self.subTest(method=method):
                response = self.send(method, SETUP, {"state": "minimum_complete"})
                self.assertIn(response.status_code, (404, 405))

        setup = self.setup()
        self.assertEqual(setup["state"], "in_progress")
        self.assertIsNone(setup["minimum_completed_at"])

    def test_the_get_ignores_any_account_or_site_selector(self):
        other = self.other_client()
        theirs = self.create_site(client=other, subdomain="bens-store")
        other_account = Account.objects.exclude(email=EMAIL).get()

        for query in (
            f"?site={theirs['id']}", f"?account={other_account.public_id}",
            f"?account_id={other_account.pk}", "?state=minimum_complete",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.get(SETUP + query).json()["state"], "not_started")
                self.assertIsNone(self.get(SETUP + query).json()["first_site"])

    def test_can_create_site_is_true_only_when_not_started(self):
        self.assertTrue(self.setup()["can_create_site"])
        self.create_site()
        self.assertFalse(self.setup()["can_create_site"])

    def test_an_unverified_owner_is_blocked(self):
        Account.objects.filter(pk=self.account.pk).update(email_verified_at=None)

        setup = self.setup()

        self.assertEqual(
            (setup["state"], setup["blocked_reason"]), ("blocked", "owner_email_unverified")
        )
        self.assertFalse(setup["can_create_site"])
        response = self.post(
            "/v1/sites/",
            {"template": "sante", "subdomain": "anas-store", "display_name": "Store"},
        )
        self.error(response, 403, "email_not_verified")

    def test_a_removed_first_site_is_blocked(self):
        site = self.create_site()
        Site.objects.update(deleted_at=timezone.now())

        setup = self.setup()

        self.assertEqual((setup["state"], setup["blocked_reason"]), ("blocked", "first_site_removed"))
        self.assertEqual(setup["first_site"]["id"], site["id"])
        self.assertTrue(setup["first_site"]["deleted"])
        self.assertFalse(setup["can_create_site"])

    def test_a_complete_site_changed_outside_a_write_reads_complete_without_a_stamp(self):
        self.create_site()
        Site.objects.update(contact_email="a@example.test", contact_mobile="+639171234567")
        SiteBusinessProfile.objects.update(business_name="Ana Trading")

        setup = self.setup()

        self.assertEqual(setup["state"], "minimum_complete")
        self.assertIsNone(setup["minimum_completed_at"])

    def test_the_get_writes_nothing(self):
        for _ in range(3):
            self.setup()
        self.assertFalse(Onboarding.objects.exists())

        self.create_site()
        before = Onboarding.objects.values().get()
        for _ in range(3):
            self.setup()
        self.assertEqual(Onboarding.objects.values().get(), before)

    @override_settings(SITE_PARENT_HOST="")
    def test_the_suffix_is_empty_without_a_parent_host(self):
        self.assertEqual(self.setup()["site_host_suffix"], "")
