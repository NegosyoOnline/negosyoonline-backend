"""The Subscriber's Santé settings over HTTP: sponsor ID, the read-only catalog owner flag,
tenant isolation and the switch. Restates TWCinc's test_sponsor_id, the API half of
test_template_behavior and the API half of test_catalog_owner for public ids and the 404 rule."""
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import AccountAuditEvent
from apps.operators.models import OperatorAuditEntry
from apps.sante import services
from apps.sante.api import router
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sante.tests.base import (
    MISSING_ID, SanteApiTestCase, operator, retemplate, settings_url,
)
from apps.sites.models import Site
from apps.sites.tests.test_isolation import routes_under_a_site

BLANK = {"sponsor_id": "", "registration_ready": False, "catalog_owner": False}


def sante_rows():
    return (
        list(SanteSiteSettings.objects.order_by("pk").values()),
        list(SanteCatalogOwner.objects.order_by("pk").values()),
    )


class SponsorIdTests(SanteApiTestCase):
    def test_it_is_never_defaulted_and_not_ready_until_set(self):
        self.assertEqual(self.read(), BLANK)
        self.assertFalse(SanteSiteSettings.objects.exists())  # reading writes nothing
        self.assertFalse(services.registration_ready(self.site))

    def test_set_then_cleared(self):
        response = self.patch(self.url, {"sponsor_id": "SB-12345"})
        self.assertEqual(response.status_code, 200, response.content)
        ready = {"sponsor_id": "SB-12345", "registration_ready": True, "catalog_owner": False}
        self.assertEqual(response.json(), ready)
        self.assertEqual(self.read(), ready)
        self.assertTrue(services.registration_ready(self.site))

        self.assertEqual(self.patch(self.url, {"sponsor_id": ""}).json(), BLANK)
        self.assertEqual(self.read(), BLANK)
        self.assertFalse(services.registration_ready(self.site))
        self.assertEqual(SanteSiteSettings.objects.count(), 1)  # cleared, the row stays

    def test_null_clears_it_too(self):
        self.patch(self.url, {"sponsor_id": "SB-1"})
        self.assertEqual(self.patch(self.url, {"sponsor_id": None}).json(), BLANK)

    def test_the_value_is_stripped_and_its_case_kept(self):
        self.assertEqual(self.patch(self.url, {"sponsor_id": "  Ab-12  "}).json()["sponsor_id"], "Ab-12")

    def test_a_bad_format_is_refused_and_changes_nothing(self):
        self.patch(self.url, {"sponsor_id": "SB-1"})
        for bad in ("bad id!", "x" * 33, "ñ-1", "a_b", "12 34"):
            with self.subTest(bad):
                body = self.error(self.patch(self.url, {"sponsor_id": bad}), 400, "invalid_request")
                self.assertEqual(
                    body["fields"], {"sponsor_id": ["Use 1 to 32 letters, digits or hyphens."]}
                )
        self.assertEqual(self.read()["sponsor_id"], "SB-1")

    def test_32_characters_fit(self):
        longest = "A1-" + "9" * 29
        self.assertEqual(self.patch(self.url, {"sponsor_id": longest}).json()["sponsor_id"], longest)

    def test_two_sites_may_hold_the_same_sponsor_id(self):
        client, theirs = self.ben_with_site()
        self.patch(self.url, {"sponsor_id": "SAME-1"})
        response = self.patch(settings_url(theirs.public_id), {"sponsor_id": "SAME-1"}, client=client)
        self.assertEqual(response.status_code, 200, response.content)

    def test_an_empty_body_changes_nothing_and_answers_the_settings(self):
        self.patch(self.url, {"sponsor_id": "SB-1"})
        before = sante_rows()
        self.assertEqual(self.patch(self.url, {}).json()["sponsor_id"], "SB-1")
        self.assertEqual(sante_rows(), before)

    def test_a_missing_sponsor_id_blocks_nothing_else(self):
        response = self.patch(f"/v1/sites/{self.site_id}/", {"display_name": "Still works"})
        self.assertEqual(response.status_code, 200, response.content)
        response = self.patch(
            f"/v1/sites/{self.site_id}/business-profile/", {"business_name": "Still Works Trading"}
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_the_account_event_names_the_field_and_never_the_value(self):
        self.patch(self.url, {"sponsor_id": "SB-12345"})
        event = AccountAuditEvent.objects.get(event="sante_settings_updated")
        self.assertEqual(event.account, self.account)
        self.assertEqual(event.detail, {"site": self.site_id, "fields": ["sponsor_id"]})
        self.assertFalse(OperatorAuditEntry.objects.exists())

    def test_a_signed_out_request_is_refused(self):
        self.client.cookies.clear()
        self.error(self.get(self.url), 401, "unauthenticated")
        self.error(self.patch(self.url, {"sponsor_id": "SB-1"}), 401, "unauthenticated")
        self.assertFalse(SanteSiteSettings.objects.exists())


class TemplateBehaviorTests(SanteApiTestCase):
    def test_settings_exist_per_sante_site_and_stay_separate(self):
        client, theirs = self.ben_with_site()
        self.patch(self.url, {"sponsor_id": "A-1"})
        self.assertEqual(self.read(settings_url(theirs.public_id), client=client), BLANK)
        self.patch(settings_url(theirs.public_id), {"sponsor_id": "B-2"}, client=client)
        self.assertEqual(self.read()["sponsor_id"], "A-1")
        self.assertEqual(SanteSiteSettings.objects.get(site=theirs).sponsor_id, "B-2")

    def test_a_non_sante_site_has_no_sante_settings(self):
        retemplate(self.site)
        self.error(self.get(self.url), 404, "not_found")
        self.error(self.patch(self.url, {"sponsor_id": "X"}), 404, "not_found")
        self.assertFalse(SanteSiteSettings.objects.exists())
        # The site itself still answers: only the Santé capability is missing.
        self.assertEqual(self.get(f"/v1/sites/{self.site_id}/").status_code, 200)


class CatalogOwnerFlagTests(SanteApiTestCase):
    def test_a_subscriber_body_naming_the_catalog_owner_is_refused_and_changes_nothing(self):
        for body in ({"catalog_owner": True}, {"sponsor_id": "SB-1", "catalog_owner": True},
                     {"registration_ready": True}, {"site": self.site_id}):
            with self.subTest(body):
                self.error(self.patch(self.url, body), 400, "invalid_request")
        self.assertEqual(sante_rows(), ([], []))

    def test_the_flag_is_read_only_and_follows_the_operators_choice(self):
        client, theirs = self.ben_with_site()
        their_url = settings_url(theirs.public_id)
        self.assertFalse(self.read()["catalog_owner"])

        services.set_catalog_owner(self.site, operator=operator())
        self.assertTrue(self.read()["catalog_owner"])
        self.assertFalse(self.read(their_url, client=client)["catalog_owner"])

        self.error(self.patch(their_url, {"catalog_owner": True}, client=client), 400, "invalid_request")
        self.assertEqual(services.catalog_owner_site(), self.site)

    def test_no_route_of_the_api_can_name_the_catalog_owner(self):
        methods = {m for m, _ in routes_under_a_site(router)}
        self.assertEqual(methods, {"GET", "PATCH"})
        self.assertEqual(len(router.path_operations), 1)


class IsolationTests(SanteApiTestCase):
    """A foreign, missing, removed or non-Santé site answers the same 404 on every route."""

    def setUp(self):
        super().setUp()
        self.ben, self.theirs = self.ben_with_site()
        self.patch(settings_url(self.theirs.public_id), {"sponsor_id": "BEN-1"}, client=self.ben)
        services.set_catalog_owner(self.theirs, operator=operator())

    def call(self, method, site_id, client=None):
        path = settings_url(site_id)
        if method == "GET":
            return self.get(path, client=client)
        return self.send(method.lower(), path, {"sponsor_id": "TAKEN-1"}, client=client)

    def assert_same_404(self, site_id, client=None):
        routes = routes_under_a_site(router)
        self.assertTrue(routes)
        reference = self.call("GET", MISSING_ID).json()
        before = sante_rows()
        for method, path in routes:
            with self.subTest(method=method, path=path):
                response = self.call(method, site_id, client=client)
                self.assertEqual(response.status_code, 404, response.content)
                self.assertEqual(response.json(), reference)
                self.assertNotIn("BEN-1", response.content.decode())
        self.assertEqual(sante_rows(), before)

    def test_every_route_under_a_site_is_one_this_test_knows(self):
        self.assertEqual(
            sorted(routes_under_a_site(router)),
            [("GET", "/sites/{site_id}/sante/settings/"), ("PATCH", "/sites/{site_id}/sante/settings/")],
        )

    def test_another_accounts_site(self):
        self.assert_same_404(self.theirs.public_id)

    def test_a_missing_site(self):
        self.assert_same_404(MISSING_ID)

    def test_a_removed_site(self):
        Site.objects.filter(pk=self.theirs.pk).update(deleted_at=timezone.now())
        self.assert_same_404(self.theirs.public_id, client=self.ben)

    def test_a_non_sante_site(self):
        retemplate(self.theirs)
        self.assert_same_404(self.theirs.public_id, client=self.ben)

    def test_a_row_number_is_not_an_id(self):
        self.assert_same_404(str(self.theirs.pk))


@override_settings(SANTE_SETTINGS_ENABLED=False)
class SwitchedOffTests(SanteApiTestCase):
    def test_every_route_answers_404_signed_in_or_not_and_reads_no_sante_table(self):
        unknown = self.get("/v1/no-such-endpoint/").json()["error"]["code"]
        self.assertEqual(unknown, "not_found")
        for signed_in in (True, False):
            if not signed_in:
                self.client.cookies.clear()
            for method, _ in routes_under_a_site(router):
                with self.subTest(method=method, signed_in=signed_in), self.assertNumQueries(0):
                    response = (
                        self.get(self.url) if method == "GET"
                        else self.send(method.lower(), self.url, {"sponsor_id": "SB-1"})
                    )
                    self.error(response, 404, "not_found")
        self.assertEqual(sante_rows(), ([], []))
