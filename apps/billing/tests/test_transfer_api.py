"""The Subscriber's bank-transfer routes over HTTP (Billing Build B2a, pull request 2)."""
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import AccountAuditEvent
from apps.accounts.tests.base import ORIGIN
from apps.billing import api, proofs, services
from apps.billing.models import BankTransferPayment, Subscription
from apps.billing.tests.base import JPEG, MANILA, PDF, PNG, days, make_operator, transfers_on
from apps.billing.tests.test_transitions import snapshot
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

PAYMENT_KEYS = {
    "id", "status", "plan", "amount_centavos", "currency", "transferred_on",
    "transfer_reference", "submitted_at", "decided_at", "reject_reason", "reject_note", "proof",
    "cycle",
}
OPTION_KEYS = {
    "payable", "not_payable_reason", "offers", "transfer_accounts", "under_review", "latest",
    "proof_rules",
}
OFFER_KEYS = {
    "plan", "name", "amount_centavos", "currency", "months", "period_start_at", "period_end_at",
    "starts_at_confirmation", "then",
}


@override_settings(BILLING_ENABLED=True)
class TransferApiCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        on = transfers_on()
        self.store = on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.subscription = Subscription.objects.get(site=self.site)
        self.start = self.subscription.trial_start_at
        self.trial_end = self.subscription.trial_end_at
        self.base = f"/v1/sites/{self.site_id}/billing/"
        patcher = mock.patch("apps.billing.services.timezone")
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.at(self.start + days(1))
        self.operator = make_operator()

    def at(self, moment):
        self.clock.now.return_value = moment
        return moment

    def today(self):
        return self.clock.now.return_value.astimezone(MANILA).date().isoformat()

    def upload(self, content=JPEG, client=None, path=None, origin=ORIGIN, **fields):
        data = {"plan": "monthly", "transferred_on": self.today(), **fields}
        data = {name: value for name, value in data.items() if value is not None}
        if content is not None:
            data["proof"] = SimpleUploadedFile("my name and slip.exe", content, "text/plain")
        extra = {"HTTP_ORIGIN": origin} if origin else {}
        return (client or self.client).post(
            path or self.base + "payments/", data=data, secure=True, **extra
        )

    def confirm(self, payment_id, at):
        payment = BankTransferPayment.objects.get(public_id=payment_id)
        return services.confirm_transfer(
            payment, self.operator, amount_received_centavos=payment.amount_centavos, now=at
        )


class PaymentOptionsTests(TransferApiCase):
    def test_a_trial_sees_how_to_pay(self):
        body = self.get(self.base + "payment/").json()

        self.assertEqual(set(body), OPTION_KEYS)
        self.assertEqual((body["payable"], body["not_payable_reason"]), (True, None))
        self.assertEqual(len(body["offers"]), 1)
        offer = body["offers"][0]
        self.assertEqual(set(offer), OFFER_KEYS)
        self.assertEqual(
            (offer["plan"], offer["amount_centavos"], offer["currency"], offer["months"],
             offer["starts_at_confirmation"], offer["then"]),
            ("monthly", 249900, "PHP", 1, False, None),
        )
        self.assertIsNotNone(offer["period_start_at"])
        self.assertIsNotNone(offer["period_end_at"])
        self.assertEqual(body["transfer_accounts"], [{
            "kind": "bank", "provider_name": "Test Bank", "account_name": "Test Receiver",
            "account_number": "0000 0000 00",
        }])
        self.assertEqual((body["under_review"], body["latest"]), (None, None))
        self.assertEqual(body["proof_rules"], {
            "max_bytes": 5242880, "content_types": ["image/jpeg", "image/png", "application/pdf"],
        })

    def test_the_read_writes_nothing(self):
        self.at(self.trial_end + days(1))
        before = snapshot()
        body = self.get(self.base + "payment/").json()
        # Ended, never paid: payable, and the period starts when the payment is confirmed.
        offer = body["offers"][0]
        self.assertEqual(
            (body["payable"], offer["starts_at_confirmation"], offer["period_start_at"],
             offer["period_end_at"]),
            (True, True, None, None),
        )
        self.assertEqual(snapshot(), before)

    def test_it_shows_the_proof_under_review_and_then_the_decision(self):
        sent = self.upload().json()
        body = self.get(self.base + "payment/").json()
        self.assertEqual(body["under_review"]["id"], sent["id"])
        self.assertTrue(body["payable"])  # a proof under review can be replaced

        self.confirm(sent["id"], self.at(self.start + days(2)))

        body = self.get(self.base + "payment/").json()
        self.assertEqual(
            (body["payable"], body["not_payable_reason"], body["offers"], body["under_review"]),
            (False, "paid_ahead", [], None),
        )
        self.assertEqual((body["latest"]["id"], body["latest"]["status"]), (sent["id"], "confirmed"))

    def test_the_reasons_a_site_cannot_be_paid_for(self):
        sent = self.upload().json()
        self.confirm(sent["id"], self.start + days(2))
        self.at(self.trial_end + days(1))
        self.post(self.base + "cancel/")
        self.assertEqual(
            self.get(self.base + "payment/").json()["not_payable_reason"], "cancel_scheduled"
        )

    def test_an_exempt_site_cannot_pay(self):
        services.set_exempt(self.site, operator=self.operator, reason="approved test site")
        body = self.get(self.base + "payment/").json()
        self.assertEqual((body["payable"], body["not_payable_reason"]), (False, "billing_exempt"))
        self.error(self.upload(), 409, "billing_exempt")

    def test_the_billing_read_is_unchanged_and_shows_the_running_cycle_when_paid_ahead(self):
        path = self.base
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            off = self.get(path).content
        self.assertEqual(self.get(path).content, off)

        sent = self.upload().json()
        self.confirm(sent["id"], self.start + days(2))
        body = self.get(path).json()
        self.assertEqual(body["state"], "active")
        self.assertEqual(
            (body["current_cycle"]["sequence"], body["current_cycle"]["settlement"]),
            (0, "not_billable"),
        )
        self.at(self.trial_end)
        self.assertEqual(self.get(path).json()["current_cycle"]["sequence"], 1)


class SubmitApiTests(TransferApiCase):
    def test_a_proof_is_accepted_and_recorded(self):
        response = self.upload(transfer_reference=" REF-1 ")

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(set(body), PAYMENT_KEYS)
        self.assertTrue(body["id"].startswith("btp_"))
        self.assertEqual(
            (body["status"], body["plan"], body["amount_centavos"], body["currency"],
             body["transferred_on"], body["transfer_reference"], body["decided_at"],
             body["reject_reason"], body["reject_note"], body["cycle"]),
            ("submitted", "monthly", 249900, "PHP", self.today(), "REF-1", None, None, None, None),
        )
        self.assertEqual(body["proof"], {"content_type": "image/jpeg", "size_bytes": len(JPEG)})
        self.assertEqual(
            list(AccountAuditEvent.objects.filter(event="billing_proof_submitted")
                 .values_list("detail", flat=True)),
            [{"site": self.site_id, "payment": body["id"]}],
        )
        self.assertEqual(Subscription.objects.get(site=self.site).state, "trial_active")

    def test_the_same_file_again_answers_200_with_the_same_payment(self):
        first = self.upload().json()
        again = self.upload()
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json()["id"], first["id"])
        self.assertEqual(AccountAuditEvent.objects.filter(event="billing_proof_submitted").count(), 1)

    def test_every_bad_field_is_named(self):
        cases = {
            "proof": [dict(content=None), dict(content=b"<html></html>"),
                      dict(content=JPEG + b"0" * proofs.MAX_BYTES)],
            "plan": [dict(plan=None), dict(plan="annual")],
            "transferred_on": [dict(transferred_on=None), dict(transferred_on="11/12/2026"),
                               dict(transferred_on="2999-01-01")],
            "transfer_reference": [dict(transfer_reference="x" * 65)],
            "color": [dict(color="red")],
        }
        for field, each in cases.items():
            for values in each:
                with self.subTest(field=field, values=list(values)):
                    body = self.error(self.upload(**values), 400, "invalid_request")
                    self.assertEqual(list(body["fields"]), [field])
                    self.assertIsInstance(body["fields"][field], list)
        self.assertEqual(BankTransferPayment.objects.count(), 0)
        self.assertEqual(self.store.files, {})

    def test_the_conflicts(self):
        sent = self.upload().json()
        self.confirm(sent["id"], self.start + days(2))
        self.error(self.upload(content=PNG), 409, "paid_ahead")
        self.at(self.trial_end + days(1))
        self.post(self.base + "cancel/")
        self.error(self.upload(content=PNG), 409, "cancel_scheduled")

    def test_past_the_last_date_to_pay(self):
        self.at(self.trial_end + days(1))
        self.upload()  # the trial ended; this write starts nothing new
        deletion = Subscription.objects.get(site=self.site).deletion_due_at
        self.assertIsNotNone(deletion)
        self.at(deletion)
        self.error(self.upload(content=PNG), 409, "past_deletion_date")

    def test_a_write_without_an_allowed_origin_is_refused(self):
        for origin in (None, "https://anas-store.negosyotest.com"):
            response = self.upload(origin=origin)
            self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(BankTransferPayment.objects.count(), 0)

    def test_submissions_are_limited_per_account(self):
        for _ in range(api.PROOF_LIMIT):
            self.assertEqual(self.upload().status_code // 100, 2)
        self.error(self.upload(content=PNG), 429, "rate_limited")
        self.assertEqual(BankTransferPayment.objects.count(), 1)


class ReadApiTests(TransferApiCase):
    def test_the_list_is_newest_first_and_paged(self):
        first = self.upload().json()["id"]
        second = self.upload(content=PNG).json()["id"]
        third = self.upload(content=PDF).json()["id"]

        page = self.get(self.base + "payments/?limit=2").json()
        self.assertEqual(set(page), {"items", "next_cursor"})
        self.assertEqual([item["id"] for item in page["items"]], [third, second])
        self.assertEqual([item["status"] for item in page["items"]], ["submitted", "replaced"])
        self.assertEqual(page["next_cursor"], second)
        rest = self.get(self.base + f"payments/?limit=2&cursor={second}").json()
        self.assertEqual(([item["id"] for item in rest["items"]], rest["next_cursor"]), ([first], None))
        self.error(self.get(self.base + "payments/?cursor=btp_nope"), 400, "invalid_cursor")

    def test_the_detail_shows_a_confirmed_cycle_and_never_the_operator(self):
        sent = self.upload().json()
        self.confirm(sent["id"], self.start + days(2))

        body = self.get(self.base + f"payments/{sent['id']}/").json()

        self.assertEqual(set(body), PAYMENT_KEYS)
        self.assertEqual(body["status"], "confirmed")
        self.assertEqual(set(body["cycle"]), {"sequence", "start_at", "end_at"})
        self.assertEqual(body["cycle"]["sequence"], 1)
        text = self.get(self.base + f"payments/{sent['id']}/").content.decode()
        for hidden in ("proof_key", "decided_by", "amount_received", "operator"):
            self.assertNotIn(hidden, text)

    def test_a_rejection_shows_its_reason_and_note(self):
        sent = self.upload().json()
        payment = BankTransferPayment.objects.get(public_id=sent["id"])
        services.reject_transfer(
            payment, self.operator, reason="other", note="Another name.", now=self.start + days(2)
        )
        body = self.get(self.base + f"payments/{sent['id']}/").json()
        self.assertEqual(
            (body["status"], body["reject_reason"], body["reject_note"]),
            ("rejected", "other", "Another name."),
        )
        options = self.get(self.base + "payment/").json()
        self.assertEqual((options["latest"]["id"], options["payable"]), (sent["id"], True))

    def test_the_proof_is_sent_privately_with_a_neutral_name(self):
        sent = self.upload(content=PDF).json()

        response = self.get(self.base + f"payments/{sent['id']}/proof/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Disposition"], 'inline; filename="proof.pdf"')


class IsolationTests(TransferApiCase):
    def paths(self, payment_id):
        return [
            "payment/", "payments/", f"payments/{payment_id}/", f"payments/{payment_id}/proof/",
        ]

    def test_another_subscriber_gets_404_everywhere_and_no_file_is_read(self):
        mine = self.upload().json()["id"]
        ben = self.other_client()
        bens_site = self.create_site(client=ben, subdomain="bens-store")["id"]
        bens_base = f"/v1/sites/{bens_site}/billing/"
        self.store.get = mock.Mock(side_effect=AssertionError("a file was read"))

        for path in self.paths(mine):
            with self.subTest(path=path):
                self.error(self.get(self.base + path, client=ben), 404, "not_found")
        # Ana's payment id under Ben's own site, and a guessed id, are not found either.
        for payment_id in (mine, "btp_" + "0" * 26, "nonsense"):
            for suffix in ("", "proof/"):
                self.error(
                    self.get(bens_base + f"payments/{payment_id}/{suffix}", client=ben),
                    404, "not_found",
                )
        self.error(self.upload(client=ben, content=PNG), 404, "not_found")
        self.assertEqual(self.get(bens_base + "payments/", client=ben).json()["items"], [])
        self.assertEqual(BankTransferPayment.objects.count(), 1)

    def test_signed_out_is_401_on_every_route(self):
        mine = self.upload().json()["id"]
        stranger = Client()
        for path in self.paths(mine):
            self.assertEqual(self.get(self.base + path, client=stranger).status_code, 401, path)
        self.assertEqual(self.upload(client=stranger, content=PNG).status_code, 401)

    def test_switched_off_every_route_is_404_and_no_new_table_is_read(self):
        mine = self.upload().json()["id"]
        before = snapshot()
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            with CaptureQueriesContext(connection) as queries:
                for path in self.paths(mine):
                    self.error(self.get(self.base + path), 404, "not_found")
                self.error(self.upload(content=PNG), 404, "not_found")
                self.assertEqual(self.get(self.base).status_code, 200)
        touched = [q["sql"] for q in queries if "banktransfer" in q["sql"].lower()]
        self.assertEqual(touched, [])
        self.assertEqual(snapshot(), before)
        self.assertEqual(BankTransferPayment.objects.count(), 1)
        with override_settings(BILLING_ENABLED=False):
            for path in self.paths(mine):
                self.error(self.get(self.base + path), 404, "not_found")

    def test_the_paths_are_in_the_openapi_description(self):
        paths = self.get("/v1/openapi.json").json()["paths"]
        prefix = "/v1/sites/{site_id}/billing/"
        for suffix, method in (
            ("payment/", "get"), ("payments/", "get"), ("payments/", "post"),
            ("payments/{payment_id}/", "get"), ("payments/{payment_id}/proof/", "get"),
        ):
            self.assertIn(method, paths[prefix + suffix])
