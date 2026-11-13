"""The Santé catalog owner routes under /staff/v1/sante/owner/: reached through the staff
routes' own origin and session checks, closed while SANTE_SETTINGS_ENABLED is off, and always
written through services.set_catalog_owner."""
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.sante import services, staff
from apps.sante.models import SanteCatalogOwner
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site

BASE = "/staff/v1/sante/owner/"
PATHS = ("get/", "set/")


@on_staff
@override_settings(SANTE_SETTINGS_ENABLED=True)
class OwnerRouteTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()
        account = make_account()
        self.first = make_site(account, "first-store", "First Store")
        self.second = make_site(account, "second-store", "Second Store", first=False)

    def owner(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def entries(self):
        return list(OperatorAuditEntry.objects.filter(action="sante_catalog_owner_set").order_by("pk"))

    def test_the_routes_are_in_the_table_and_are_posts(self):
        self.assertEqual(sorted(staff.ROUTES), ["sante/owner/get/", "sante/owner/set/"])
        for path in staff.ROUTES:
            self.assertEqual(staff_views.ROUTES[path][0], "POST")

    def test_another_origin_is_refused(self):
        for path in PATHS:
            self.assertError(self.owner(path, origin="https://evil.example"), 403, "origin_not_allowed")

    def test_no_session_and_a_session_before_the_second_step_are_refused(self):
        for path in PATHS:
            self.assertError(self.owner(path, {"site": None}), 401, "unauthenticated")
        self.staff_email_step()
        for path in PATHS:
            self.assertError(self.owner(path, {"site": None}), 401, "second_step_required")
        self.assertFalse(SanteCatalogOwner.objects.exists())
        self.assertEqual(self.entries(), [])

    @override_settings(SANTE_SETTINGS_ENABLED=False)
    def test_switched_off_both_routes_are_404_and_read_nothing(self):
        self.staff_sign_in()
        with mock.patch.object(staff.services, "catalog_owner_site") as read:
            for path in PATHS:
                self.assertError(self.owner(path, {"site": self.first.public_id}), 404, "not_found")
            read.assert_not_called()
        self.assertFalse(SanteCatalogOwner.objects.exists())
        self.assertEqual(self.entries(), [])

    def test_get_lists_the_candidates_and_no_owner(self):
        self.staff_sign_in()
        # Not candidates: a deleted site and a site of another template.
        gone = make_site(make_account("b@example.test"), "gone-store", "Gone")
        Site.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())
        other = make_site(make_account("c@example.test"), "other-store", "Other")
        Site.objects.filter(pk=other.pk).update(template="other")
        response = self.owner("get/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.json(), {
            "owner": None,
            "candidates": [
                {"id": self.first.public_id, "name": "First Store"},
                {"id": self.second.public_id, "name": "Second Store"},
            ],
        })

    def test_set_names_the_owner_through_the_service_and_audits(self):
        self.staff_sign_in()
        with mock.patch.object(
            staff.services, "set_catalog_owner", wraps=services.set_catalog_owner
        ) as service:
            response = self.owner("set/", {"site": self.second.public_id})
        self.assertEqual(response.status_code, 200)
        service.assert_called_once()
        self.assertEqual(service.call_args.args[0].pk, self.second.pk)
        self.assertEqual(service.call_args.kwargs["operator"], self.identity.user)
        self.assertEqual(
            response.json()["owner"], {"id": self.second.public_id, "name": "Second Store"}
        )
        self.assertEqual(SanteCatalogOwner.objects.get().site_id, self.second.pk)
        (entry,) = self.entries()
        self.assertEqual(entry.operator, self.identity.user)
        self.assertEqual(entry.detail, {"old_site": None, "new_site": self.second.public_id})
        # Replacing it, then reading it back.
        self.owner("set/", {"site": self.first.public_id})
        self.assertEqual(
            self.owner("get/").json()["owner"], {"id": self.first.public_id, "name": "First Store"}
        )
        self.assertEqual(self.entries()[-1].detail["old_site"], self.second.public_id)

    def test_null_clears_the_owner_and_audits(self):
        self.staff_sign_in()
        self.owner("set/", {"site": self.first.public_id})
        response = self.owner("set/", {"site": None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["owner"])
        self.assertIsNone(SanteCatalogOwner.objects.get().site_id)
        self.assertEqual(
            self.entries()[-1].detail, {"old_site": self.first.public_id, "new_site": None}
        )

    def test_bad_values_are_refused_and_change_nothing(self):
        self.staff_sign_in()
        gone = make_site(make_account("b@example.test"), "gone-store", "Gone")
        Site.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())
        for body in ({}, {"site": ""}, {"site": 7}, {"site": "site_unknown"}, {"site": gone.public_id}):
            with self.subTest(body=body):
                response = self.owner("set/", body)
                self.assertError(response, 400, "invalid_request")
                self.assertIn("site", response.json()["error"]["fields"])
        self.assertFalse(SanteCatalogOwner.objects.exists())
        self.assertEqual(self.entries(), [])
