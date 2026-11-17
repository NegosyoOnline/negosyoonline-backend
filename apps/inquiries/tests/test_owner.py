"""The Subscriber's inquiry routes: access, isolation between sites, and the routes that are gone.
The routes themselves (list, thread, reply, close, reopen, read, channels) are tested in
test_stored.py."""
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from apps.inquiries.models import InquiryConnection, InquiryReply
from apps.inquiries.tests.base import WC_REF, make_connection, make_stored, switched_on
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase


@switched_on
class InquiryOwnerTestCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.base = f"/v1/sites/{self.site_id}/inquiries/"
        make_stored(self.site)


class AccessTests(InquiryOwnerTestCase):
    def walk(self, client=None, base=None):
        base = base or self.base
        return [
            self.get(base + "conversations/", client=client),
            self.get(base + f"conversations/{WC_REF}/", client=client),
            self.send("post", base + f"conversations/{WC_REF}/reply/", {"text": "hi", "idempotency_key": "key-12345678"}, client=client),
            self.get(base + "channels/", client=client),
        ]

    def test_switch_off_every_route_answers_404_and_touches_no_table(self):
        with override_settings(INQUIRIES_ENABLED=False):
            for response in self.walk():
                self.error(response, 404, "not_found")
        self.assertEqual(InquiryReply.objects.count(), 0)

    def test_tables_not_yet_created_answers_404(self):
        with mock.patch("apps.inquiries.api_owner.tables_exist", return_value=False):
            for response in self.walk():
                self.error(response, 404, "not_found")

    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        for response in self.walk():
            self.error(response, 401, "unauthenticated")

    def test_another_accounts_site_is_404_with_the_same_body_as_a_missing_site(self):
        ben = self.other_client()
        missing = self.walk(base="/v1/sites/s_doesnotexist/inquiries/")
        foreign = self.walk(client=ben)
        for a, b in zip(foreign, missing):
            self.error(a, 404, "not_found")
            self.assertEqual(a.json(), b.json())
        self.assertEqual(InquiryReply.objects.count(), 0)

    def test_a_removed_site_and_a_non_sante_site_are_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        for response in self.walk():
            self.error(response, 404, "not_found")
        Site.objects.filter(pk=self.site.pk).update(template="sante", deleted_at=self.site.created_at)
        for response in self.walk():
            self.error(response, 404, "not_found")

    def test_billing_only_is_403(self):
        with mock.patch("apps.sites.api.billing.position", return_value=SimpleNamespace(billing_only=True)):
            for response in self.walk():
                self.error(response, 403, "billing_only")

    def test_an_unknown_body_key_is_refused(self):
        response = self.send(
            "post", self.base + f"conversations/{WC_REF}/reply/",
            {"text": "hi", "idempotency_key": "key-12345678", "owner_id": 7},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(InquiryReply.objects.count(), 0)


class RemovedRoutesTests(InquiryOwnerTestCase):
    """The provider connection routes are gone: nothing answers them, for any method."""

    def test_the_connection_routes_no_longer_exist(self):
        for path in ("connection/", "connection/test/"):
            for method, body in (("get", None), ("put", {"label": "x"}), ("post", None), ("delete", None)):
                with self.subTest(path=path, method=method):
                    response = self.get(self.base + path) if method == "get" else self.send(method, self.base + path, body)
                    self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(InquiryConnection.objects.count(), 0)

    def test_a_provider_cursor_is_ignored_and_a_provider_ref_is_not_found(self):
        response = self.get(self.base + "conversations/?cursor=abc")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {"items", "stored_next_cursor"})
        self.error(self.get(self.base + "conversations/1000000000001_7000000000001/"), 404, "not_found")

    def test_a_connection_row_changes_nothing_on_the_routes(self):
        make_connection(self.site)
        body = self.get(self.base + "conversations/").json()
        self.assertEqual([i["ref"] for i in body["items"]], [WC_REF])
