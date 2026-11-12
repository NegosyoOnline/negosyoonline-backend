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
    "id", "status", "plan", "amount_centavos", "usage_centavos", "currency", "transferred_on",
    "transfer_reference", "submitted_at", "decided_at", "reject_reason", "reject_note", "proof",
    "cycle", "transferred_at", "sender_bank", "sender_first_name", "sender_last_name",
    "sender_account_number", "amount_sent_centavos",
}
OPTION_KEYS = {
    "payable", "not_payable_reason", "payable_from", "offers", "usage", "transfer_accounts",
    "under_review", "latest", "proof_rules",
}
OFFER_KEYS = {
    "plan", "name", "amount_centavos", "usage_centavos", "currency", "months", "version", "term_months",
    "period_start_at", "period_end_at",
    "starts_at_confirmation", "then", "consent",
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

    def local_time(self):
        return self.clock.now.return_value.astimezone(MANILA).strftime("%Y-%m-%dT%H:%M")

    def upload(self, content=JPEG, client=None, path=None, origin=ORIGIN, **fields):
        data = {
            "plan": "monthly", "transferred_at": self.local_time(),
            "transfer_reference": "BANK-REF-1", "sender_bank": "Test Bank",
            "sender_first_name": "Juanito", "sender_last_name": "Bautista",
            "sender_account_number": "1234-5678-90", "amount_sent": "999.00", **fields,
        }
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

    def test_the_senders_details_are_stored_and_shown_to_the_owner(self):
        at = self.clock.now.return_value
        response = self.upload(
            sender_bank="  Test Bank ", amount_sent="4,999.00",
            transferred_at=at.astimezone(MANILA).strftime("%Y-%m-%dT%H:%M"),
            transferred_on=self.today(),
        )

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(
            (body["sender_bank"], body["sender_first_name"], body["sender_last_name"],
             body["sender_account_number"], body["amount_sent_centavos"]),
            ("Test Bank", "Juanito", "Bautista", "1234-5678-90", 499900),
        )
        row = BankTransferPayment.objects.get()
        self.assertEqual(row.transferred_on.isoformat(), self.today())
        self.assertEqual(row.transferred_at.astimezone(MANILA).strftime("%Y-%m-%dT%H:%M"),
                         self.local_time())
        self.assertEqual(self.get(self.base + f"payments/{body['id']}/").json(), body)

    def test_the_manila_date_of_the_time_is_the_date_kept(self):
        self.at(self.start + days(1))
        stamp = self.clock.now.return_value.astimezone(MANILA).replace(hour=0, minute=30)
        # 00:30 in Manila is the evening before in UTC; the Manila date is what is kept.
        self.at(stamp + days(1))
        response = self.upload(transferred_at=stamp.strftime("%Y-%m-%dT%H:%M"))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["transferred_on"], stamp.date().isoformat())

    def test_a_typed_amount_that_differs_from_the_amount_due_is_accepted_and_flagged(self):
        response = self.upload(amount_sent="1.50")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["amount_sent_centavos"], 150)
        row = BankTransferPayment.objects.get()
        self.assertEqual((row.amount_sent_centavos, row.amount_centavos), (150, 249900))
        # confirming still needs the amount received to be the amount due
        with self.assertRaises(Exception):
            services.confirm_transfer(
                row, self.operator, amount_received_centavos=150, now=self.start + days(2)
            )

    def test_an_old_row_without_the_details_still_reads(self):
        sent = self.upload().json()
        BankTransferPayment.objects.update(
            sender_bank="", sender_first_name="", sender_last_name="", sender_account_number="",
            amount_sent_centavos=None, transferred_at=None,
        )
        body = self.get(self.base + f"payments/{sent['id']}/").json()
        self.assertEqual(set(body), PAYMENT_KEYS)
        self.assertIsNone(body["transferred_at"])
        self.assertIsNone(body["amount_sent_centavos"])
        self.assertEqual(self.get(self.base + "payments/").status_code, 200)

    def test_the_same_file_again_leaves_the_first_rows_details_unchanged(self):
        first = self.upload().json()
        again = self.upload(sender_bank="Another Bank", amount_sent="1.00")
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json(), first)
        self.assertEqual(BankTransferPayment.objects.get().sender_bank, "Test Bank")

    @mock.patch.object(api, "PROOF_LIMIT", 1000)
    def test_every_bad_field_is_named(self):
        cases = {
            "proof": [dict(content=None), dict(content=b"<html></html>"),
                      dict(content=JPEG + b"0" * proofs.MAX_BYTES)],
            "plan": [dict(plan=None), dict(plan="annual")],
            "transferred_on": [dict(transferred_on="11/12/2026"),
                               dict(transferred_on="2999-01-01"),
                               dict(transferred_on="2020-01-01")],  # not the date of transferred_at
            "transferred_at": [dict(transferred_at=None), dict(transferred_at="11/12/2026 10:00"),
                               dict(transferred_at="2999-01-01T10:00"),
                               dict(transferred_at="2026-11-12T10:00:00")],
            "transfer_reference": [dict(transfer_reference=None), dict(transfer_reference="  "),
                                   dict(transfer_reference="x" * 65)],
            "sender_bank": [dict(sender_bank=None), dict(sender_bank=" "),
                            dict(sender_bank="x" * 81)],
            "sender_first_name": [dict(sender_first_name=None), dict(sender_first_name="x" * 81)],
            "sender_last_name": [dict(sender_last_name=None), dict(sender_last_name="x" * 81)],
            "sender_account_number": [
                dict(sender_account_number=None), dict(sender_account_number="12345"),
                dict(sender_account_number="1234567890A"), dict(sender_account_number="1" * 35),
            ],
            "amount_sent": [
                dict(amount_sent=None), dict(amount_sent="0"), dict(amount_sent="0.00"),
                dict(amount_sent="-5"), dict(amount_sent="10.001"), dict(amount_sent="1e3"),
                dict(amount_sent="12,34"), dict(amount_sent="abc"), dict(amount_sent="99999999999"),
            ],
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
