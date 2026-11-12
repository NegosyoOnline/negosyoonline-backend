"""The Subscriber's Meta settings routes: access, isolation, validation, the write-only token."""
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from apps.meta_ads import crypto
from apps.meta_ads.models import MetaDelivery, MetaSettings
from apps.meta_ads.tests.base import CODE, PIXEL, TOKEN, FakeMeta, http_error, switched_on
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_site


@switched_on
class MetaOwnerTestCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.path = f"/v1/sites/{self.site_id}/meta/"

    def save(self, **body):
        return self.send("put", self.path, {"pixel_id": PIXEL, "access_token": TOKEN, "test_event_code": CODE, **body})


class AccessTests(MetaOwnerTestCase):
    def walk(self, client=None, path=None):
        path = path or self.path
        return [
            self.get(path, client=client),
            self.send("put", path, {"pixel_id": PIXEL}, client=client),
            self.send("delete", path + "token/", None, client=client),
            self.send("post", path + "test/", None, client=client),
        ]

    def test_switch_off_every_route_answers_404(self):
        with override_settings(META_EVENTS_ENABLED=False):
            for response in self.walk():
                self.error(response, 404, "not_found")
        self.assertEqual(MetaSettings.objects.count(), 0)

    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        for response in self.walk():
            self.error(response, 401, "unauthenticated")

    def test_another_accounts_site_is_404_and_nothing_changes(self):
        ben = self.other_client()
        for response in self.walk(client=ben):
            self.error(response, 404, "not_found")
        self.assertEqual(MetaSettings.objects.count(), 0)

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
        response = self.send("put", self.path, {"pixel_id": PIXEL, "owner_id": 7})
        self.assertEqual(response.status_code, 400)  # the API's refusal of an unknown key
        self.assertEqual(MetaSettings.objects.count(), 0)


class SettingsTests(MetaOwnerTestCase):
    def test_a_fresh_site_reads_empty(self):
        body = self.get(self.path).json()
        self.assertEqual(
            body, {"pixel_id": "", "token_set": False, "test_event_code": "", "last_test": None, "events": []}
        )

    def test_saving_and_reading_never_returns_the_token(self):
        saved = self.save()
        self.assertEqual(saved.status_code, 200, saved.content)
        for response in (saved, self.get(self.path)):
            self.assertNotIn(TOKEN, response.content.decode())
            body = response.json()
            self.assertEqual((body["pixel_id"], body["token_set"], body["test_event_code"]), (PIXEL, True, CODE))
            self.assertNotIn("access_token", body)
            self.assertNotIn("token_encrypted", body)

    def test_the_stored_token_is_ciphertext_that_decrypts_back(self):
        self.save()
        stored = MetaSettings.objects.get(site=self.site).token_encrypted
        self.assertNotIn(TOKEN, stored)
        self.assertEqual(crypto.decrypt(stored), TOKEN)

    def test_a_left_out_or_empty_token_keeps_the_saved_one(self):
        self.save()
        before = MetaSettings.objects.get(site=self.site).token_encrypted
        self.send("put", self.path, {"pixel_id": "999999999", "access_token": ""})
        self.send("put", self.path, {"test_event_code": "NEWCODE1"})
        row = MetaSettings.objects.get(site=self.site)
        self.assertEqual((row.token_encrypted, row.pixel_id, row.test_event_code), (before, "999999999", "NEWCODE1"))

    def test_a_token_can_be_replaced_and_removed(self):
        self.save()
        other = TOKEN.replace("abcdefghij", "klmnopqrst")
        self.save(access_token=other)
        self.assertEqual(crypto.decrypt(MetaSettings.objects.get(site=self.site).token_encrypted), other)
        removed = self.send("delete", self.path + "token/", None)
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(removed.json()["token_set"])
        self.assertEqual(MetaSettings.objects.get(site=self.site).token_encrypted, "")
        self.assertEqual(MetaSettings.objects.get(site=self.site).pixel_id, PIXEL)

    def test_values_are_validated(self):
        cases = [
            {"pixel_id": "12ab"}, {"pixel_id": "1" * 21}, {"access_token": "short"},
            {"access_token": "x" * 30 + " " + "y" * 30}, {"test_event_code": "bad code!"},
        ]
        for case in cases:
            with self.subTest(case=case):
                body = self.error(self.send("put", self.path, case), 422, "invalid")
                self.assertEqual(len(body["fields"]), 1)
        self.assertEqual(MetaSettings.objects.count(), 0)

    def test_a_missing_key_refuses_to_store_a_token(self):
        with override_settings(META_CREDENTIALS_KEY=""):
            self.error(self.save(), 503, "unavailable")
        self.assertEqual(MetaSettings.objects.filter(token_encrypted__gt="").count(), 0)

    def test_changing_the_pixel_id_clears_the_last_test(self):
        self.save()
        MetaSettings.objects.update(last_test_outcome="ok", last_test_at=self.site.created_at)
        self.send("put", self.path, {"pixel_id": "888888888"})
        row = MetaSettings.objects.get(site=self.site)
        self.assertEqual((row.last_test_outcome, row.last_test_at), ("", None))

    def test_writes_are_limited_per_account(self):
        for _ in range(20):
            self.assertEqual(self.send("put", self.path, {"pixel_id": PIXEL}).status_code, 200)
        self.error(self.send("put", self.path, {"pixel_id": PIXEL}), 429, "rate_limited")

    def test_the_last_events_list_shows_only_this_sites_rows(self):
        mine = MetaDelivery.objects.create(site=self.site, event_name="PageView", event_id="pv_" + "a" * 16)
        other = make_site(self.account, name="second-store", first=False)
        MetaDelivery.objects.create(site=other, event_name="PageView", event_id="pv_" + "b" * 16)
        rows = self.get(self.path).json()["events"]
        self.assertEqual([r["event_id"] for r in rows], [mine.event_id])
        self.assertEqual(set(rows[0]), {"event_name", "event_id", "created_at", "outcome", "is_test"})

    def test_another_site_of_mine_is_not_changed(self):
        other = make_site(self.account, name="second-store", first=False)
        self.save()
        self.assertFalse(MetaSettings.objects.filter(site=other).exists())


class ConnectionTestTests(MetaOwnerTestCase):
    def run_test(self, *answers):
        with FakeMeta(*answers) as meta:
            response = self.send("post", self.path + "test/", None, HTTP_USER_AGENT="Mozilla/5.0 Owner")
        return response, meta

    def test_it_needs_a_pixel_a_token_and_a_code(self):
        self.send("put", self.path, {"pixel_id": PIXEL, "access_token": TOKEN})
        self.error(self.send("post", self.path + "test/", None), 409, "incomplete")

    def test_ok_sends_one_flagged_page_view_and_records_it(self):
        self.save()
        response, meta = self.run_test(200)
        self.assertEqual(response.json(), {"outcome": "ok"})
        body = meta.bodies()[0]
        self.assertEqual(body["test_event_code"], CODE)
        self.assertEqual(body["access_token"], TOKEN)
        self.assertNotIn(TOKEN, meta.requests[0][0].full_url)
        event = body["data"][0]
        self.assertEqual(event["event_name"], "PageView")
        self.assertTrue(event["event_id"].startswith("tc_"))
        self.assertEqual(event["user_data"]["client_user_agent"], "Mozilla/5.0 Owner")
        row = MetaSettings.objects.get(site=self.site)
        self.assertEqual(row.last_test_outcome, "ok")
        delivery = MetaDelivery.objects.get()
        self.assertEqual((delivery.is_test, delivery.outcome), (True, "sent"))

    def test_outcomes_are_codes_never_metas_message(self):
        self.save()
        cases = [
            ((http_error(400, 190),), "bad_token"), ((http_error(400, 100),), "bad_pixel"),
            ((http_error(429, 4),), "rate_limited"), ((http_error(503, None),), "meta_down"),
            ((TimeoutError("x"),), "meta_down"),
        ]
        for answers, outcome in cases:
            with self.subTest(outcome=outcome):
                response, _ = self.run_test(*answers)
                self.assertEqual(response.json(), {"outcome": outcome})
                self.assertNotIn("SECRET MESSAGE", response.content.decode())
                self.assertEqual(MetaSettings.objects.get(site=self.site).last_test_outcome, outcome)

    def test_an_unreadable_token_is_reported_without_a_crash(self):
        self.save()
        with override_settings(META_CREDENTIALS_KEY="not-a-key"):
            response, meta = self.run_test(200)
        self.assertEqual(response.json(), {"outcome": "token_unreadable"})
        self.assertEqual(meta.requests, [])
        with override_settings(META_CREDENTIALS_KEY=crypto.Fernet.generate_key().decode()):  # another valid key
            response, meta = self.run_test(200)
        self.assertEqual(response.json(), {"outcome": "token_unreadable"})

    def test_tests_are_limited_per_account(self):
        self.save()
        for _ in range(10):
            self.run_test(200)
        response, meta = self.run_test(200)
        self.error(response, 429, "rate_limited")
        self.assertEqual(meta.requests, [])
