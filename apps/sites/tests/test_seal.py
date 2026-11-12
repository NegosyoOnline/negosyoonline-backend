"""The owner's BIR registration badge: owner-only upload, replace and remove, the owner's preview,
and the storefront route that serves it."""
from django.core.cache import cache
from django.test import override_settings

from apps.accounts.tests.base import ORIGIN
from apps.billing.tests.base import make_subscription
from apps.common import private_files
from apps.payments.tests.base import PNG, JPEG, qr_file, stored_files, use_local_private_store
from apps.sites.models import SiteBusinessProfile
from apps.sites.tests.api_base import SitesApiTestCase
from apps.storefront.tests.base import HOST, NOT_FOUND, SECRET, SurfaceTestCase


def seal_url(site_id):
    return f"/v1/sites/{site_id}/business-profile/seal/"


class OwnerSealTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.private_dir = use_local_private_store(self)
        self.site_id = self.create_site()["id"]
        self.url = seal_url(self.site_id)

    def upload(self, file=None, client=None, field="seal"):
        data = {field: file or qr_file(name="seal.png")}
        return (client or self.client).post(self.url, data=data, secure=True, HTTP_ORIGIN=ORIGIN)

    def profile(self):
        return SiteBusinessProfile.objects.get(site__public_id=self.site_id)

    def test_a_profile_reads_has_seal_false_until_one_is_uploaded(self):
        response = self.get(f"/v1/sites/{self.site_id}/business-profile/")
        self.assertFalse(response.json()["has_seal"])

    def test_an_upload_is_stored_privately_and_answers_the_profile_without_a_key(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["has_seal"])
        self.assertNotIn("seal_key", body)
        key = self.profile().seal_key
        self.assertRegex(key, r"^sites/seal/site-\d+/[0-9a-f]{32}\.png$")
        self.assertEqual(stored_files(self.private_dir), [key])

    def test_the_owner_can_see_the_image_and_a_missing_one_is_404(self):
        self.assertEqual(self.get(self.url).status_code, 404)
        self.upload()
        response = self.get(self.url)
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/png"))
        self.assertEqual(response["Cache-Control"], "private, no-cache")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

    def test_a_replacement_removes_the_old_file_once_committed(self):
        self.upload()
        old = self.profile().seal_key
        with self.captureOnCommitCallbacks(execute=True):
            response = self.upload(qr_file(JPEG, "image/jpeg", "seal.jpg"))
        self.assertEqual(response.status_code, 200, response.content)
        new = self.profile().seal_key
        self.assertNotEqual(new, old)
        self.assertEqual(stored_files(self.private_dir), [new])

    def test_a_remove_clears_the_key_and_the_file(self):
        self.upload()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.send("delete", self.url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["has_seal"])
        self.assertEqual((self.profile().seal_key, stored_files(self.private_dir)), ("", []))
        self.assertEqual(self.send("delete", self.url).status_code, 200)  # removing none is harmless

    def test_a_bad_type_a_big_file_or_no_file_is_refused_and_nothing_is_stored(self):
        for file in (qr_file(b"%PDF-1.4 x", "application/pdf", "a.pdf"),
                     qr_file(PNG + b"0" * (2 * 1024 * 1024), "image/png", "big.png"),
                     qr_file(b"text", "text/plain", "a.txt")):
            fields = self.error(self.upload(file), 400, "invalid_request")["fields"]
            self.assertEqual(set(fields), {"seal"})
        fields = self.error(self.upload(field="other"), 400, "invalid_request")["fields"]
        self.assertEqual(set(fields), {"seal"})
        self.assertEqual((stored_files(self.private_dir), self.profile().seal_key), ([], ""))

    def test_another_account_and_a_signed_out_caller_cannot_reach_it(self):
        self.upload()
        ben = self.other_client()
        for call in (lambda: self.upload(client=ben), lambda: ben.get(self.url, secure=True),
                     lambda: self.send("delete", self.url, client=ben)):
            self.assertEqual(call().status_code, 404)
        from django.test import Client
        self.assertEqual(Client().get(self.url, secure=True).status_code, 401)
        self.assertTrue(self.profile().seal_key)

    def test_a_billing_only_site_refuses_the_owner_routes(self):
        from unittest import mock
        with mock.patch("apps.sites.api.billing.position") as position:
            position.return_value.billing_only = True
            for response in (self.upload(), self.get(self.url), self.send("delete", self.url)):
                self.error(response, 403, "billing_only")


class StorefrontSealTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.private_dir = use_local_private_store(self)
        self.site = make_subscription().site
        self.path = "/storefront/v1/seal/"

    def give_seal(self):
        key = private_files.save("sites/seal/site-1", PNG, "image/png")
        SiteBusinessProfile.objects.filter(site=self.site).update(seal_key=key)
        return key

    def test_none_uploaded_is_the_unknown_host_404_and_has_seal_false(self):
        response = self.call(path=self.path)
        self.assert_answer(response, 404, NOT_FOUND)
        self.assertFalse(self.call().json()["business"]["has_seal"])

    def test_an_uploaded_badge_is_served_and_has_seal_is_true(self):
        self.give_seal()
        self.assertTrue(self.call().json()["business"]["has_seal"])
        response = self.call(path=self.path)
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/png"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.content, PNG)

    def test_an_unknown_host_and_a_missing_credential_are_refused(self):
        self.give_seal()
        self.assert_answer(self.call("nobody.negosyotest.com", path=self.path), 404, NOT_FOUND)
        self.assertEqual(self.call(path=self.path, secret=None).status_code, 401)

    def test_an_unreadable_file_reads_as_missing(self):
        key = self.give_seal()
        private_files.delete(key)
        self.assert_answer(self.call(path=self.path), 404, NOT_FOUND)
