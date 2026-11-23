import re

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Account
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.api_base import OTHER_EMAIL, SitesApiTestCase
from apps.sites.tests.base import make_site

SITE_KEYS = {
    "id", "template", "template_label", "display_name", "subdomain", "host", "business_name",
    "contact_email", "contact_mobile", "accent_color", "facebook_url",
    "instagram_url", "tiktok_url",
    "held", "created_at",
}
MISSING = "/v1/sites/site_00000000000000000000/"


class AccessTests(SitesApiTestCase):
    def test_every_endpoint_needs_a_session(self):
        site = self.create_site()["id"]
        self.client.cookies.clear()

        for method, path in (
            ("post", "/v1/sites/subdomain-check/"),
            ("get", "/v1/sites/"),
            ("post", "/v1/sites/"),
            ("get", f"/v1/sites/{site}/"),
            ("patch", f"/v1/sites/{site}/"),
            ("get", f"/v1/sites/{site}/business-profile/"),
            ("patch", f"/v1/sites/{site}/business-profile/"),
            ("get", "/v1/account/setup/"),
        ):
            with self.subTest(method=method, path=path):
                response = self.get(path) if method == "get" else self.send(method, path, {})
                self.error(response, 401, "unauthenticated")

    def test_every_write_needs_an_allowed_origin(self):
        site = self.create_site()["id"]

        for method, path, data in (
            ("post", "/v1/sites/subdomain-check/", {"subdomain": "x"}),
            ("post", "/v1/sites/", {}),
            ("patch", f"/v1/sites/{site}/", {"display_name": "Changed"}),
            ("patch", f"/v1/sites/{site}/business-profile/", {"business_name": "Changed"}),
        ):
            for origin in (None, "https://anas-store.negosyotest.com"):
                with self.subTest(path=path, origin=origin):
                    response = self.send(method, path, data, HTTP_ORIGIN=origin)
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(Site.objects.get().display_name, "Ana's Store")


class SubdomainCheckTests(SitesApiTestCase):
    def check(self, subdomain):
        return self.post("/v1/sites/subdomain-check/", {"subdomain": subdomain})

    def test_subdomain_check_statuses_and_messages(self):
        make_site(Account.objects.create(email=OTHER_EMAIL), name="bens-store")

        for subdomain, status, message in (
            ("anas-store", "available", ""),
            ("bens-store", "taken", "That name is already taken. Please choose another."),
            ("api", "reserved", "That name is reserved. Please choose another."),
            ("a_b", "invalid", "Use 3 to 30 lowercase letters, digits or single hyphens."),
        ):
            with self.subTest(subdomain=subdomain):
                response = self.check(subdomain)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {"subdomain": subdomain, "status": status, "message": message},
                )

    def test_subdomain_check_returns_the_normalised_name_and_creates_nothing(self):
        self.assertEqual(self.check("  Anas-Store ").json()["subdomain"], "anas-store")
        self.assertFalse(Subdomain.objects.exists())

    def test_subdomain_check_is_limited_to_60_in_ten_minutes(self):
        for _ in range(60):
            self.assertEqual(self.check("anas-store").status_code, 200)

        self.error(self.check("anas-store"), 429, "rate_limited")

    def test_unknown_keys_and_a_missing_name_are_400(self):
        self.error(self.post("/v1/sites/subdomain-check/", {}), 400, "invalid_request")
        response = self.post("/v1/sites/subdomain-check/", {"subdomain": "x", "site": "y"})
        self.assertIn("site", self.error(response, 400, "invalid_request")["fields"])


class CreateTests(SitesApiTestCase):
    def test_create_answers_201_with_the_site_shape(self):
        site = self.create_site(subdomain=" Anas-Store ")

        self.assertEqual(set(site), SITE_KEYS)
        self.assertRegex(site["id"], r"^site_[0-9a-z]{20}$")
        self.assertEqual(site["template"], "sante")
        self.assertEqual(site["template_label"], "Santé")
        self.assertEqual(site["subdomain"], "anas-store")
        self.assertEqual(site["host"], "anas-store.negosyotest.com")
        self.assertEqual(site["business_name"], "")
        row = Site.objects.get()
        self.assertEqual(row.account, self.account)
        # No database key leaves the API.
        self.assertNotIn(row.pk, [value for value in site.values() if isinstance(value, int)])

    def test_create_refuses_basic_and_every_other_template(self):
        for template in ("standard", "basic", "Sante", "", "shop"):
            with self.subTest(template=template):
                response = self.post(
                    "/v1/sites/",
                    {"template": template, "subdomain": "anas-store", "display_name": "Store"},
                )
                body = self.error(response, 400, "invalid_template")
                self.assertEqual(body["fields"], {"template": ["Choose a site type."]})
        self.assertFalse(Site.objects.exists())
        self.assertFalse(Subdomain.objects.exists())

    def test_create_refuses_a_missing_or_null_template(self):
        for body in (
            {"subdomain": "anas-store", "display_name": "Store"},
            {"template": None, "subdomain": "anas-store", "display_name": "Store"},
        ):
            response = self.post("/v1/sites/", body)
            self.assertIn("template", self.error(response, 400, "invalid_request")["fields"])
        self.assertFalse(Site.objects.exists())

    def test_create_refuses_unknown_keys(self):
        for key, value in (
            ("account", "acc_00000000000000000000"), ("operator_hold", True),
            ("business_name", "x"), ("id", "site_00000000000000000000"),
            ("contact_email", "a@example.test"), ("state", "minimum_complete"),
        ):
            with self.subTest(key=key):
                response = self.post(
                    "/v1/sites/",
                    {"template": "sante", "subdomain": "anas-store", "display_name": "S",
                     key: value},
                )
                self.assertIn(key, self.error(response, 400, "invalid_request")["fields"])
        self.assertFalse(Site.objects.exists())

    def test_create_subdomain_errors(self):
        make_site(Account.objects.create(email=OTHER_EMAIL), name="bens-store")

        for subdomain, status, code in (
            ("a_b", 400, "subdomain_invalid"),
            ("official-store", 400, "subdomain_reserved"),
            ("bens-store", 409, "subdomain_taken"),
        ):
            with self.subTest(subdomain=subdomain):
                response = self.post(
                    "/v1/sites/",
                    {"template": "sante", "subdomain": subdomain, "display_name": "Store"},
                )
                self.assertEqual(list(self.error(response, status, code)["fields"]), ["subdomain"])
        self.assertFalse(Site.objects.filter(account=self.account).exists())

    def test_create_display_name_errors(self):
        for display_name in ("", "  ", "x" * 81):
            response = self.post(
                "/v1/sites/",
                {"template": "sante", "subdomain": "anas-store", "display_name": display_name},
            )
            body = self.error(response, 400, "invalid_request")
            self.assertEqual(body["fields"], {"display_name": ["Use 1 to 80 characters."]})

    def test_a_repeated_create_is_409_site_limit_reached_and_the_list_still_has_one_site(self):
        first = self.create_site()

        same = self.post(
            "/v1/sites/",
            {"template": "sante", "subdomain": "anas-store", "display_name": "Ana's Store"},
        )
        other = self.post(
            "/v1/sites/",
            {"template": "sante", "subdomain": "second-store", "display_name": "Second"},
        )

        self.error(same, 409, "site_limit_reached")
        self.error(other, 409, "site_limit_reached")
        self.assertEqual([s["id"] for s in self.get("/v1/sites/").json()["items"]], [first["id"]])
        self.assertEqual(Site.objects.count(), 1)
        self.assertEqual(Subdomain.objects.count(), 1)

    def test_a_removed_first_site_refuses_creation(self):
        self.create_site()
        Site.objects.update(deleted_at=timezone.now())

        response = self.post(
            "/v1/sites/",
            {"template": "sante", "subdomain": "second-store", "display_name": "Second"},
        )

        self.error(response, 409, "first_site_removed")

    def test_create_is_limited_to_10_a_day(self):
        body = {"template": "standard", "subdomain": "anas-store", "display_name": "Store"}
        for _ in range(10):
            self.error(self.post("/v1/sites/", body), 400, "invalid_template")

        self.error(self.post("/v1/sites/", {**body, "template": "sante"}), 429, "rate_limited")
        self.assertFalse(Site.objects.exists())


class ListTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.sites = [
            make_site(self.account, name=f"store-{n}", display_name=f"Store {n}", first=n == 0)
            for n in range(3)
        ]

    def ids(self, response):
        return [site["id"] for site in response.json()["items"]]

    def test_list_shows_only_my_sites_oldest_first(self):
        make_site(Account.objects.create(email=OTHER_EMAIL), name="bens-store")

        response = self.get("/v1/sites/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ids(response), [site.public_id for site in self.sites])
        self.assertIsNone(response.json()["next_cursor"])
        self.assertEqual(set(response.json()["items"][0]), SITE_KEYS)
        self.assertEqual(response.json()["items"][1]["subdomain"], "store-1")

    def test_list_paginates_with_a_cursor(self):
        first = self.get("/v1/sites/?limit=2")
        cursor = first.json()["next_cursor"]
        second = self.get(f"/v1/sites/?limit=2&cursor={cursor}")

        self.assertEqual(self.ids(first), [site.public_id for site in self.sites[:2]])
        self.assertEqual(cursor, self.sites[1].public_id)
        self.assertEqual(self.ids(second), [self.sites[2].public_id])
        self.assertIsNone(second.json()["next_cursor"])

    def test_a_cursor_from_another_account_or_a_bad_cursor_is_400(self):
        other = make_site(Account.objects.create(email=OTHER_EMAIL), name="bens-store")

        for cursor in (other.public_id, "nonsense", "1"):
            with self.subTest(cursor=cursor):
                self.error(self.get(f"/v1/sites/?cursor={cursor}"), 400, "invalid_cursor")

    def test_list_hides_a_deleted_site(self):
        Site.objects.filter(pk=self.sites[1].pk).update(deleted_at=timezone.now())

        self.assertEqual(
            self.ids(self.get("/v1/sites/")), [self.sites[0].public_id, self.sites[2].public_id]
        )

    def test_the_list_runs_a_fixed_number_of_queries(self):
        with self.assertNumQueries(3):  # session, sites with their profile, subdomains
            self.get("/v1/sites/")


class DetailAndPatchTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.site = self.create_site()
        self.path = f"/v1/sites/{self.site['id']}/"

    def test_detail_and_patch(self):
        patched = self.patch(self.path, {
            "display_name": " New Name ", "contact_email": "Hello@Example.Test",
            "contact_mobile": "0917 123 4567", "instagram_url": "https://instagram.com/anas",
        })
        fetched = self.get(self.path)

        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json(), fetched.json())
        body = fetched.json()
        self.assertEqual(set(body), SITE_KEYS)
        self.assertEqual(body["display_name"], "New Name")
        self.assertEqual(body["contact_email"], "hello@example.test")
        self.assertEqual(body["contact_mobile"], "+639171234567")
        self.assertEqual(body["accent_color"], "")
        self.assertEqual(body["subdomain"], "anas-store")

    def test_a_null_clears_a_field_and_an_unsent_field_is_kept(self):
        self.patch(self.path, {
            "instagram_url": "https://instagram.com/anas", "contact_mobile": "09171234567",
        })

        body = self.patch(self.path, {"instagram_url": None}).json()

        self.assertEqual(body["instagram_url"], "")
        self.assertEqual(body["contact_mobile"], "+639171234567")

    def test_a_facebook_link_is_saved_read_back_and_cleared(self):
        body = self.patch(self.path, {"facebook_url": "https://www.facebook.com/anas"}).json()
        self.assertEqual(body["facebook_url"], "https://www.facebook.com/anas")
        self.assertEqual(self.get(self.path).json()["facebook_url"], "https://www.facebook.com/anas")
        listed = self.get("/v1/sites/").json()
        self.assertIn("https://www.facebook.com/anas", str(listed))

        refused = self.patch(self.path, {"facebook_url": "https://fb.com/anas"})
        self.assertEqual(
            self.error(refused, 400, "invalid_request")["fields"],
            {"facebook_url": ["Enter the full https link to your Facebook page."]},
        )

        self.assertEqual(self.patch(self.path, {"facebook_url": None}).json()["facebook_url"], "")

    def test_a_blank_store_email_or_mobile_is_400_and_nothing_in_the_request_saves(self):
        self.patch(self.path, {"contact_email": "a@example.test", "contact_mobile": "09171234567"})
        before = self.get(self.path).json()

        for key, message in (
            ("contact_email", "Enter your store email."),
            ("contact_mobile", "Enter your store mobile."),
        ):
            for value in ("", "  ", None):
                with self.subTest(key=key, value=value):
                    response = self.patch(self.path, {
                        "display_name": "Changed", "tiktok_url": "https://tiktok.com/@anas",
                        key: value,
                    })
                    self.assertEqual(
                        self.error(response, 400, "invalid_request")["fields"], {key: [message]}
                    )

        self.assertEqual(self.get(self.path).json(), before)

    def test_an_accent_colour_is_refused_and_the_stored_value_is_unchanged(self):
        Site.objects.update(accent_color="#173DB8")

        for value in ("#000000", "", None):
            with self.subTest(value=value):
                response = self.patch(self.path, {"display_name": "Changed", "accent_color": value})
                self.assertIn("accent_color", self.error(response, 400, "invalid_request")["fields"])
        alone = self.patch(self.path, {"accent_color": "#000000"})

        self.assertIn("accent_color", self.error(alone, 400, "invalid_request")["fields"])
        body = self.get(self.path).json()
        self.assertEqual((body["accent_color"], body["display_name"]), ("#173DB8", "Ana's Store"))
        self.assertEqual(Site.objects.get().accent_color, "#173DB8")

    def test_server_owned_keys_are_refused_on_both_patch_routes(self):
        owned = {
            "account": "acc_00000000000000000000", "template": "standard",
            "subdomain": "new-name", "operator_hold": True,
            "public_id": "site_00000000000000000000", "deleted_at": "2026-01-01T00:00:00Z",
        }

        def rows():
            return (
                list(Site.objects.values()), list(Subdomain.objects.values()),
                list(SiteBusinessProfile.objects.values()),
            )

        before = rows()

        for path, allowed in (
            (self.path, {"display_name": "Changed"}),
            (self.path + "business-profile/", {"business_address": "1 Rizal St, Manila"}),
        ):
            for key, value in owned.items():
                with self.subTest(path=path, key=key):
                    response = self.patch(path, {**allowed, key: value})
                    self.assertIn(key, self.error(response, 400, "invalid_request")["fields"])

        self.assertEqual(rows(), before)

    def test_patch_refuses_template_subdomain_and_unknown_keys(self):
        for key, value in (
            ("template", "standard"), ("subdomain", "new-name"), ("business_name", "x"),
            ("operator_hold", True), ("account", "acc_00000000000000000000"),
            ("deleted_at", None), ("id", "site_00000000000000000000"),
        ):
            with self.subTest(key=key):
                response = self.patch(self.path, {"display_name": "Changed", key: value})
                self.assertIn(key, self.error(response, 400, "invalid_request")["fields"])
        body = self.get(self.path).json()
        self.assertEqual(
            (body["template"], body["subdomain"], body["display_name"]),
            ("sante", "anas-store", "Ana's Store"),
        )

    def test_patch_reports_all_invalid_fields_together(self):
        response = self.patch(self.path, {
            "display_name": "Changed", "contact_email": "nope", "contact_mobile": "123",
        })

        body = self.error(response, 400, "invalid_request")
        self.assertEqual(body["fields"], {
            "contact_email": ["Enter a valid email address."],
            "contact_mobile": ["Enter a valid Philippine mobile number."],
        })
        self.assertEqual(self.get(self.path).json()["display_name"], "Ana's Store")

    def test_an_email_longer_than_the_column_is_a_400_not_a_server_error(self):
        too_long = "a" * 64 + "@" + ".".join(["b" * 60] * 3) + ".example.test"
        self.assertGreater(len(too_long), 254)

        site = self.patch(self.path, {"contact_email": too_long})
        profile = self.patch(
            self.path + "business-profile/", {"privacy_contact_email": too_long}
        )

        self.assertEqual(
            self.error(site, 400, "invalid_request")["fields"],
            {"contact_email": ["Enter a valid email address."]},
        )
        self.assertEqual(
            self.error(profile, 400, "invalid_request")["fields"],
            {"privacy_contact_email": ["Enter a valid email address."]},
        )

    def test_missing_and_deleted_sites_are_404(self):
        self.error(self.get(MISSING), 404, "not_found")
        self.error(self.patch(MISSING, {"display_name": "x"}), 404, "not_found")
        Site.objects.update(deleted_at=timezone.now())
        self.error(self.get(self.path), 404, "not_found")
        self.error(self.get(self.path + "business-profile/"), 404, "not_found")

    @override_settings(SITE_PARENT_HOST="")
    def test_host_is_null_without_a_parent_host(self):
        self.assertIsNone(self.get(self.path).json()["host"])


class BusinessProfileTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.site = self.create_site()
        self.path = f"/v1/sites/{self.site['id']}/business-profile/"

    def test_business_profile_get_and_patch(self):
        fresh = self.get(self.path).json()
        self.assertEqual(fresh["business_name"], "")
        self.assertEqual(fresh["shipping_luzon_estimate"], "2-5 business days")
        self.assertFalse(fresh["shipping_estimates_confirmed"])

        patched = self.patch(self.path, {
            "business_name": "Ana Trading", "tin": "123456789", "bir_rsn": "113rc20260000017598",
            "shipping_estimates_confirmed": True,
        })

        self.assertEqual(patched.status_code, 200, patched.content)
        body = self.get(self.path).json()
        self.assertEqual(body["business_name"], "Ana Trading")
        self.assertEqual(body["tin"], "123-456-789")
        self.assertEqual(body["bir_rsn"], "113RC20260000017598")
        self.assertTrue(body["shipping_estimates_confirmed"])
        self.assertEqual(body["shipping_luzon_estimate"], "2-5 business days")
        self.assertNotIn("id", body)
        self.assertNotIn("site", body)

    def test_site_business_name_follows_the_profile(self):
        self.patch(self.path, {"business_name": "Ana Trading"})

        self.assertEqual(
            self.get(f"/v1/sites/{self.site['id']}/").json()["business_name"], "Ana Trading"
        )
        self.assertEqual(self.get("/v1/sites/").json()["items"][0]["business_name"], "Ana Trading")

    def test_invalid_and_unknown_fields_are_400_and_nothing_saves(self):
        response = self.patch(self.path, {"business_name": "Ana Trading", "tin": "12"})
        self.assertEqual(list(self.error(response, 400, "invalid_request")["fields"]), ["tin"])
        response = self.patch(self.path, {"business_name": "Ana Trading", "seal_key": "x"})
        self.assertIn("seal_key", self.error(response, 400, "invalid_request")["fields"])

        self.assertEqual(self.get(self.path).json()["business_name"], "")

    def test_a_blank_business_name_is_400_and_nothing_in_the_request_saves(self):
        self.patch(self.path, {"business_name": "Ana Trading"})
        before = self.get(self.path).json()

        for value in ("", "  ", None):
            with self.subTest(value=value):
                response = self.patch(
                    self.path, {"business_name": value, "business_address": "1 Rizal St, Manila"}
                )
                self.assertEqual(
                    self.error(response, 400, "invalid_request")["fields"],
                    {"business_name": ["Enter your business name."]},
                )

        self.assertEqual(self.get(self.path).json(), before)


class OpenApiTests(SitesApiTestCase):
    def test_openapi_lists_the_site_paths(self):
        paths = self.get("/v1/openapi.json").json()["paths"]

        for path, methods in (
            ("/v1/sites/subdomain-check/", {"post"}),
            ("/v1/sites/", {"get", "post"}),
            ("/v1/sites/{site_id}/", {"get", "patch"}),
            ("/v1/sites/{site_id}/business-profile/", {"get", "patch"}),
            ("/v1/account/setup/", {"get"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(set(paths[path]), methods)


class HeldTests(SitesApiTestCase):
    def test_the_owner_sees_held_on_the_list_and_the_detail(self):
        site_id = self.create_site()["id"]
        self.assertIs(self.get(f"/v1/sites/{site_id}/").json()["held"], False)
        Site.objects.update(operator_hold=True)
        self.assertIs(self.get(f"/v1/sites/{site_id}/").json()["held"], True)
        self.assertIs(self.get("/v1/sites/").json()["items"][0]["held"], True)
