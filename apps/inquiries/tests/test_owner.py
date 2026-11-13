"""The Subscriber's connection routes: access, isolation between sites, validation, the
write-only token, and the connection test."""
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from apps.inquiries import crypto
from apps.inquiries.models import InquiryConnection
from apps.inquiries.tests.base import (
    BAD_TOKEN,
    LIST_OK,
    PAGE,
    REF,
    SECRET_TEXT,
    TOKEN,
    FakePancake,
    http_error,
    no_limiter,
    switched_on,
)
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_site


@switched_on
class InquiryOwnerTestCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        no_limiter(self)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.base = f"/v1/sites/{self.site_id}/inquiries/"
        self.path = self.base + "connection/"

    def save(self, **body):
        return self.send("put", self.path, {"page_id": PAGE, "access_token": TOKEN, "label": "Shop page", **body})


class AccessTests(InquiryOwnerTestCase):
    def walk(self, client=None, base=None):
        base = base or self.base
        return [
            self.get(base + "connection/", client=client),
            self.send("put", base + "connection/", {"page_id": PAGE}, client=client),
            self.send("delete", base + "connection/", None, client=client),
            self.send("post", base + "connection/test/", None, client=client),
            self.get(base + "conversations/", client=client),
            self.get(base + f"conversations/{REF}/", client=client),
            self.send("post", base + f"conversations/{REF}/reply/", {"text": "hi", "idempotency_key": "key-12345678"}, client=client),
        ]

    def test_switch_off_every_route_answers_404_and_touches_no_table(self):
        with override_settings(INQUIRIES_ENABLED=False), FakePancake() as pancake:
            for response in self.walk():
                self.error(response, 404, "not_found")
        self.assertEqual(pancake.requests, [])
        self.assertEqual(InquiryConnection.objects.count(), 0)

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
        with FakePancake() as pancake:
            foreign = self.walk(client=ben)
        self.assertEqual(pancake.requests, [])
        for a, b in zip(foreign, missing):
            self.error(a, 404, "not_found")
            self.assertEqual(a.json(), b.json())
        self.assertEqual(InquiryConnection.objects.count(), 0)

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
        response = self.send("put", self.path, {"page_id": PAGE, "owner_id": 7})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(InquiryConnection.objects.count(), 0)


class ConnectionTests(InquiryOwnerTestCase):
    def test_a_fresh_site_reads_not_connected(self):
        self.assertEqual(
            self.get(self.path).json(),
            {"provider": "pancake", "status": "unset", "page_id": "", "label": "", "token_set": False, "last_test": None},
        )

    def test_saving_and_reading_never_returns_the_token(self):
        saved = self.save()
        self.assertEqual(saved.status_code, 200, saved.content)
        for response in (saved, self.get(self.path)):
            self.assertNotIn(TOKEN, response.content.decode())
            body = response.json()
            self.assertEqual((body["page_id"], body["token_set"], body["status"], body["label"]), (PAGE, True, "active", "Shop page"))
            self.assertEqual(set(body), {"provider", "status", "page_id", "label", "token_set", "last_test"})

    def test_the_stored_token_is_ciphertext_that_decrypts_back(self):
        self.save()
        stored = InquiryConnection.objects.get(site=self.site).token_encrypted
        self.assertNotIn(TOKEN, stored)
        self.assertEqual(crypto.decrypt(stored), TOKEN)

    def test_a_left_out_or_empty_token_keeps_the_saved_one(self):
        self.save()
        before = InquiryConnection.objects.get(site=self.site).token_encrypted
        self.send("put", self.path, {"label": "Renamed", "access_token": ""})
        row = InquiryConnection.objects.get(site=self.site)
        self.assertEqual((row.token_encrypted, row.label), (before, "Renamed"))

    def test_a_token_can_be_replaced_and_disconnected(self):
        self.save()
        other = TOKEN.replace("abcdefghij", "klmnopqrst")
        self.save(access_token=other)
        self.assertEqual(crypto.decrypt(InquiryConnection.objects.get(site=self.site).token_encrypted), other)
        removed = self.send("delete", self.path, None)
        self.assertEqual(removed.status_code, 200)
        body = removed.json()
        self.assertEqual((body["token_set"], body["status"]), (False, "unset"))
        self.assertEqual(InquiryConnection.objects.get(site=self.site).token_encrypted, "")

    def test_values_are_validated(self):
        cases = [
            {"page_id": "no/slash"}, {"page_id": "x"}, {"page_id": "1" * 65}, {"access_token": "short"},
            {"access_token": "x" * 30 + " " + "y" * 30}, {"label": "x" * 61}, {"label": "bad\x07label"},
        ]
        for case in cases:
            with self.subTest(case=case):
                body = self.error(self.send("put", self.path, case), 422, "invalid")
                self.assertEqual(len(body["fields"]), 1)
        self.assertEqual(InquiryConnection.objects.count(), 0)

    def test_a_missing_key_refuses_to_store_a_token(self):
        with override_settings(INQUIRIES_CREDENTIALS_KEY=""):
            self.error(self.save(), 503, "unavailable")
        self.assertEqual(InquiryConnection.objects.exclude(token_encrypted="").count(), 0)

    def test_changing_the_page_clears_the_last_test(self):
        self.save()
        InquiryConnection.objects.update(last_test_outcome="ok", last_test_at=self.site.created_at)
        self.send("put", self.path, {"page_id": "2000000000002"})
        row = InquiryConnection.objects.get(site=self.site)
        self.assertEqual((row.last_test_outcome, row.last_test_at, row.status), ("", None, "active"))

    def test_saving_a_new_token_after_it_went_invalid_makes_the_connection_usable_again(self):
        self.save()
        InquiryConnection.objects.update(status="invalid")
        self.save(access_token=TOKEN.replace("abcdefghij", "klmnopqrst"))
        self.assertEqual(InquiryConnection.objects.get().status, "active")

    def test_writes_are_limited_per_account(self):
        for _ in range(20):
            self.assertEqual(self.send("put", self.path, {"label": "x"}).status_code, 200)
        self.error(self.send("put", self.path, {"label": "x"}), 429, "rate_limited")

    def test_another_site_of_mine_is_not_changed(self):
        other = make_site(self.account, name="second-store", first=False)
        self.save()
        self.assertFalse(InquiryConnection.objects.filter(site=other).exists())


class ConnectionTestTests(InquiryOwnerTestCase):
    def run_test(self, *answers):
        with FakePancake(listing=list(answers)) as pancake:
            response = self.send("post", self.path + "test/", None)
        return response, pancake

    def test_it_needs_a_page_and_a_token(self):
        self.send("put", self.path, {"page_id": PAGE})
        self.error(self.send("post", self.path + "test/", None), 409, "incomplete")

    def test_ok_records_the_outcome_and_activates(self):
        self.save()
        response, pancake = self.run_test(LIST_OK)
        self.assertEqual(response.json(), {"outcome": "ok"})
        self.assertEqual(pancake.kinds(), ["list"])
        row = InquiryConnection.objects.get(site=self.site)
        self.assertEqual((row.last_test_outcome, row.status), ("ok", "active"))
        self.assertIsNotNone(row.last_test_at)
        self.assertEqual(self.get(self.path).json()["last_test"]["outcome"], "ok")

    def test_outcomes_are_codes_never_pancakes_message(self):
        self.save()
        cases = [
            (BAD_TOKEN, "invalid_credentials"), ((403, {}), "forbidden_page"),
            ((429, {}), "rate_limited"), ((503, {}), "provider_down"), (TimeoutError("x"), "provider_down"),
            (http_error(500), "provider_down"), (b"<html>", "bad_response"),
        ]
        for answer, outcome in cases:
            with self.subTest(outcome=outcome):
                InquiryConnection.objects.update(status="active")
                response, _ = self.run_test(answer)
                self.assertEqual(response.json(), {"outcome": outcome})
                for leak in (SECRET_TEXT, TOKEN):
                    self.assertNotIn(leak, response.content.decode())
                self.assertEqual(InquiryConnection.objects.get(site=self.site).last_test_outcome, outcome)

    def test_a_credential_failure_marks_the_connection_invalid_and_later_calls_skip_pancake(self):
        self.save()
        response, _ = self.run_test(BAD_TOKEN)
        self.assertEqual(response.json(), {"outcome": "invalid_credentials"})
        self.assertEqual(InquiryConnection.objects.get().status, "invalid")
        with FakePancake() as pancake:
            for path in ("conversations/", f"conversations/{REF}/"):
                self.error(self.get(self.base + path), 409, "reconnect")
        self.assertEqual(pancake.requests, [])

    def test_a_test_after_invalid_tries_again_and_recovers(self):
        self.save()
        self.run_test(BAD_TOKEN)
        response, _ = self.run_test(LIST_OK)
        self.assertEqual(response.json(), {"outcome": "ok"})
        self.assertEqual(InquiryConnection.objects.get().status, "active")

    def test_a_failed_retest_of_an_invalid_connection_stays_invalid(self):
        self.save()
        self.run_test(BAD_TOKEN)
        self.run_test((503, {}))
        self.assertEqual(InquiryConnection.objects.get().status, "invalid")

    def test_an_unreadable_token_is_reported_without_a_crash(self):
        self.save()
        with override_settings(INQUIRIES_CREDENTIALS_KEY=crypto.Fernet.generate_key().decode()):
            response, pancake = self.run_test(LIST_OK)
        self.assertEqual(response.json(), {"outcome": "not_configured"})
        self.assertEqual(pancake.requests, [])

    def test_tests_are_limited_per_account(self):
        self.save()
        for _ in range(10):
            self.run_test(LIST_OK)
        response, pancake = self.run_test(LIST_OK)
        self.error(response, 429, "rate_limited")
        self.assertEqual(pancake.requests, [])
