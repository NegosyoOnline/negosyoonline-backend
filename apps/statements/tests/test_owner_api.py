"""Owner routes for the billing profile and statements (Billing Build B5, PR 7)."""
import hashlib
from datetime import datetime, timezone
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing.models import Subscription
from apps.common import private_files
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.statements.models import BillingProfile, Statement, StatementDelivery, StatementLine

WHEN = datetime(2026, 11, 25, 2, 0, tzinfo=timezone.utc)
PDF_BYTES = b"%PDF-1.4 a stored statement"
ON = dict(BILLING_ENABLED=True, BILLING_STATEMENTS_ENABLED=True)
PROFILE = {
    "company_name": "Ana Trading", "billing_email": "billing@example.test",
    "address": "1 Rizal St, Cebu", "tin": "123-456-789-000",
}


def issue_for(site, n, **fields):
    """A statement of the site's subscription, made directly: the issuing service is not
    this PR's."""
    values = dict(
        number=f"NOBS-2026-{n:06d}", document_type="statement", kind="usage_only",
        subscription=Subscription.objects.get(site=site), source_key=f"payment:btp_t{n}",
        period_start=WHEN, period_end=WHEN, total_centavos=39900, is_test=True,
        issuer={"legal_name": "Issuer"}, bill_to={"name": "Ana"},
        payment_reference=f"btp_t{n}", pdf_key=f"statements/pdf/{n}.pdf",
        pdf_sha256=hashlib.sha256(PDF_BYTES).hexdigest(), pdf_size=len(PDF_BYTES),
        issued_at=WHEN,
    )
    values.update(fields)
    statement = Statement.objects.create(**values)
    StatementLine.objects.create(
        statement=statement, position=1, kind="base", description="Subscription",
        unit_centavos=39900, amount_centavos=39900,
    )
    return statement


@override_settings(**ON)
class OwnerStatementsCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.base = f"/v1/sites/{self.site_id}/billing/"
        # Ben, a second subscriber, with a site, a profile and a statement of his own.
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben Store")["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)
        self.ben_statement = issue_for(self.ben_site, 99)
        BillingProfile.objects.create(
            site=self.ben_site, company_name="Ben Co", billing_email="ben@example.test"
        )
        patcher = mock.patch.object(private_files, "read", return_value=PDF_BYTES)
        self.read = patcher.start()
        self.addCleanup(patcher.stop)

    def put_profile(self, data, client=None, site_id=None):
        path = f"/v1/sites/{site_id or self.site_id}/billing/profile/"
        return self.send("put", path, data, client=client)


class SwitchTests(OwnerStatementsCase):
    def test_every_route_is_a_404_when_the_switch_is_off_and_reads_no_table(self):
        statement = issue_for(self.site, 1)
        paths = [
            "profile/", "statements/", f"statements/{statement.number}/",
            f"statements/{statement.number}/pdf/",
        ]
        for settings in (
            dict(BILLING_STATEMENTS_ENABLED=False), dict(BILLING_ENABLED=False),
        ):
            with override_settings(**settings):
                for path in paths:
                    with CaptureQueriesContext(connection) as queries:
                        response = self.get(self.base + path)
                    self.assertEqual(response.status_code, 404, path)
                    self.only_the_session_read(queries)
                with CaptureQueriesContext(connection) as queries:
                    self.assertEqual(self.put_profile(PROFILE).status_code, 404)
                self.only_the_session_read(queries)
        self.assertFalse(BillingProfile.objects.filter(site=self.site).exists())

    def only_the_session_read(self, queries):
        # Signing in reads the session; no site, billing or statement table is touched.
        for query in queries.captured_queries:
            self.assertIn("accounts_session", query["sql"])
            for table in ("statements_", "sites_site", "billing_"):
                self.assertNotIn(table, query["sql"])

    def test_signed_out_is_refused(self):
        from django.test import Client
        response = Client().get(self.base + "statements/", secure=True)
        self.assertEqual(response.status_code, 401)


class ProfileTests(OwnerStatementsCase):
    def test_get_with_none_saved_answers_the_empty_profile(self):
        body = self.get(self.base + "profile/").json()
        self.assertEqual(body, {
            "company_name": "", "billing_email": "", "address": "", "tin": "",
            "updated_at": None,
        })

    def test_put_saves_and_get_reads_it_back(self):
        response = self.put_profile(PROFILE)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual({k: body[k] for k in PROFILE}, PROFILE)
        self.assertIsNotNone(body["updated_at"])
        self.assertEqual(self.get(self.base + "profile/").json(), body)
        changed = {**PROFILE, "tin": "", "address": ""}
        self.assertEqual(self.put_profile(changed).status_code, 200)
        self.assertEqual(BillingProfile.objects.filter(site=self.site).count(), 1)
        self.assertEqual(BillingProfile.objects.get(site=self.site).tin, "")

    def test_values_are_trimmed(self):
        self.put_profile({**PROFILE, "company_name": "  Ana Trading  "})
        self.assertEqual(BillingProfile.objects.get(site=self.site).company_name, "Ana Trading")

    def test_unknown_and_missing_fields_are_refused(self):
        for data in (
            {**PROFILE, "site": "x"}, {**PROFILE, "id": 1},
            {k: v for k, v in PROFILE.items() if k != "tin"},
        ):
            response = self.put_profile(data)
            self.assertEqual(response.status_code, 400, data)
            self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.assertFalse(BillingProfile.objects.filter(site=self.site).exists())

    def test_invalid_values_name_their_field(self):
        for field, value in (
            ("company_name", "  "), ("billing_email", "not-an-email"),
            ("tin", "1" * 18), ("address", "a" * 256),
        ):
            response = self.put_profile({**PROFILE, field: value})
            self.assertEqual(response.status_code, 400, field)
            self.assertIn(field, response.json()["error"]["fields"])
        self.assertFalse(BillingProfile.objects.filter(site=self.site).exists())

    def test_the_write_is_throttled(self):
        from apps.statements import api
        with mock.patch.object(api, "PROFILE_LIMIT", 2):
            self.assertEqual(self.put_profile(PROFILE).status_code, 200)
            self.assertEqual(self.put_profile(PROFILE).status_code, 200)
            response = self.put_profile(PROFILE)
        self.assertEqual(response.status_code, 429)

    def test_another_subscribers_profile_is_a_404_for_get_and_put(self):
        self.assertEqual(self.get(f"/v1/sites/{self.ben_site_id}/billing/profile/").status_code, 404)
        self.assertEqual(self.put_profile(PROFILE, site_id=self.ben_site_id).status_code, 404)
        self.assertEqual(BillingProfile.objects.get(site=self.ben_site).company_name, "Ben Co")
        self.assertEqual(
            self.get("/v1/sites/site_unknown/billing/profile/").status_code, 404
        )

    def test_a_removed_site_is_a_404(self):
        from django.utils import timezone
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.get(self.base + "profile/").status_code, 404)

    def test_a_profile_leaves_issued_statements_alone(self):
        statement = issue_for(self.site, 2)
        self.put_profile(PROFILE)
        statement.refresh_from_db()
        self.assertEqual(statement.bill_to, {"name": "Ana"})


class StatementListTests(OwnerStatementsCase):
    def test_empty(self):
        self.assertEqual(
            self.get(self.base + "statements/").json(), {"items": [], "next_cursor": None}
        )

    def test_newest_first_with_a_cursor_and_only_my_own(self):
        made = [issue_for(self.site, n) for n in (1, 2, 3)]
        first = self.get(self.base + "statements/?limit=2").json()
        self.assertEqual([i["number"] for i in first["items"]], [made[2].number, made[1].number])
        self.assertEqual(first["next_cursor"], made[1].public_id)
        second = self.get(self.base + f"statements/?limit=2&cursor={first['next_cursor']}").json()
        self.assertEqual([i["number"] for i in second["items"]], [made[0].number])
        self.assertIsNone(second["next_cursor"])
        self.assertNotIn(self.ben_statement.number, str(first) + str(second))

    def test_a_row_carries_only_the_listed_keys(self):
        issue_for(self.site, 1)
        row = self.get(self.base + "statements/").json()["items"][0]
        self.assertEqual(set(row), {
            "number", "kind", "document_type", "issued_at", "period_start", "period_end",
            "total_centavos", "currency", "is_test", "delivery_state",
        })
        self.assertEqual(row["delivery_state"], "none")
        self.assertEqual(row["total_centavos"], 39900)

    def test_delivery_state_is_the_latest_attempt(self):
        statement = issue_for(self.site, 1)
        for attempt, status in ((1, "failed"), (2, "sent")):
            StatementDelivery.objects.create(
                statement=statement, to_email="a@example.test", status=status, attempt=attempt,
                idempotency_key=f"deliver:{statement.pk}:{attempt}",
            )
        row = self.get(self.base + "statements/").json()["items"][0]
        self.assertEqual(row["delivery_state"], "sent")

    def test_bad_cursors_are_refused(self):
        for cursor in ("junk", self.ben_statement.public_id):
            response = self.get(self.base + f"statements/?cursor={cursor}")
            self.assertEqual(response.status_code, 400, cursor)
            self.assertEqual(response.json()["error"]["code"], "invalid_cursor")

    def test_another_subscribers_site_is_a_404(self):
        response = self.get(f"/v1/sites/{self.ben_site_id}/billing/statements/")
        self.assertEqual(response.status_code, 404)


class StatementDetailTests(OwnerStatementsCase):
    def test_detail_has_the_stored_totals_lines_and_original(self):
        original = issue_for(self.site, 1)
        adjustment = issue_for(
            self.site, 2, number="NOCN-2026-000001", document_type="adjustment",
            original=original, total_centavos=-1000, source_key="adjustment:x:1",
            adjustment_reason="goodwill",
        )
        body = self.get(self.base + f"statements/{adjustment.number}/").json()
        self.assertEqual(body["original_number"], original.number)
        self.assertEqual(body["total_centavos"], -1000)
        self.assertEqual(body["label"], adjustment.label)
        self.assertEqual(body["bill_to"], {"name": "Ana"})
        self.assertEqual(body["lines"][0]["amount_centavos"], 39900)
        plain = self.get(self.base + f"statements/{original.number}/").json()
        self.assertIsNone(plain["original_number"])
        for hidden in ("pdf_key", "pdf_sha256", "pdf_size", "source_key", "id", "public_id"):
            self.assertNotIn(hidden, plain)

    def test_unknown_and_malformed_numbers_are_a_404(self):
        for number in ("NOBS-2026-000777", "junk", "NOBS-2026-1"):
            self.assertEqual(
                self.get(self.base + f"statements/{number}/").status_code, 404, number
            )

    def test_another_subscribers_statement_is_a_404_on_my_site_and_on_his(self):
        number = self.ben_statement.number
        for path in (self.base, f"/v1/sites/{self.ben_site_id}/billing/"):
            for suffix in ("", "pdf/"):
                response = self.get(path + f"statements/{number}/{suffix}")
                self.assertEqual(response.status_code, 404, path + suffix)
        self.read.assert_not_called()
        # The owner himself can read it.
        ok = self.get(f"/v1/sites/{self.ben_site_id}/billing/statements/{number}/", client=self.ben)
        self.assertEqual(ok.status_code, 200)

    def test_no_route_edits_a_statement(self):
        statement = issue_for(self.site, 1)
        for method in ("put", "patch", "post", "delete"):
            response = self.send(method, self.base + f"statements/{statement.number}/", {})
            self.assertIn(response.status_code, (404, 405), method)
        statement.refresh_from_db()
        self.assertEqual(statement.total_centavos, 39900)


class StatementPdfTests(OwnerStatementsCase):
    def test_the_stored_file_is_served_with_private_headers(self):
        statement = issue_for(self.site, 1)
        response = self.get(self.base + f"statements/{statement.number}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF_BYTES)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Disposition"], 'inline; filename="statement.pdf"')
        self.read.assert_called_once_with(statement.pdf_key)

    def test_a_store_failure_answers_503_and_leaks_nothing(self):
        statement = issue_for(self.site, 1)
        self.read.side_effect = private_files.StoreError("vercel token secret")
        response = self.get(self.base + f"statements/{statement.number}/pdf/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "statement_store_unavailable")
        self.assertNotIn(b"secret", response.content)
        self.assertNotIn(statement.pdf_key.encode(), response.content)

    def test_a_file_that_does_not_match_its_hash_answers_503(self):
        statement = issue_for(self.site, 1)
        self.read.return_value = b"another file"
        response = self.get(self.base + f"statements/{statement.number}/pdf/")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"another file", response.content)
