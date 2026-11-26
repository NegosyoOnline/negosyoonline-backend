"""The Subscription Credit routes (Billing B6, BE-2): the owner's account figures, the offers and
payments with their credit, pay with credit, and the HQ Referrals view and correction."""
from django.test import override_settings

from apps.billing import credits
from apps.billing.models import BankTransferPayment, CreditEntry, Subscription
from apps.billing.tests.base import days, make_operator, make_subscription, submit, transfers_on
from apps.billing.tests.test_staff_finance import FinanceTestCase
from apps.billing.tests.test_transfer_api import TransferApiCase
from apps.operators.models import OperatorAuditEntry

MONTHLY = 249_900
ON = dict(BILLING_REFERRALS_ENABLED=True, SITE_URL="https://www.negosyotest.com")


@override_settings(**ON)
class OwnerRoutesTests(TransferApiCase):
    def grant(self, centavos):
        return credits.correct(self.operator, self.account, centavos, "test grant")

    def test_the_account_route_gives_code_link_balance_and_count(self):
        self.grant(70_000)
        body = self.get("/v1/account/referrals/").json()
        self.assertEqual(body, {
            "started": True, "code": self.account.referral_code,
            "link": f"https://www.negosyotest.com/?ref={self.account.referral_code}",
            "balance": 70_000, "reserved": 0, "earned_count": 0,
        })

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_the_account_route_reports_not_started_while_off(self):
        body = self.get("/v1/account/referrals/").json()
        self.assertEqual((body["started"], body["balance"], body["earned_count"]), (False, 0, 0))
        self.assertEqual(body["code"], self.account.referral_code)

    def test_the_account_route_needs_a_sign_in_and_shows_only_ones_own_figures(self):
        self.grant(70_000)
        other = self.other_client()
        mine = self.get("/v1/account/referrals/").json()
        theirs = self.get("/v1/account/referrals/", client=other).json()
        self.assertEqual((mine["balance"], theirs["balance"]), (70_000, 0))
        self.assertNotEqual(mine["code"], theirs["code"])
        from django.test import Client

        self.assertEqual(Client().get("/v1/account/referrals/", secure=True).status_code, 401)

    def test_the_offers_and_the_payment_show_the_credit(self):
        self.grant(100_000)
        offer = self.get(self.base + "payment/").json()["offers"][0]
        self.assertEqual(
            (offer["credit_centavos"], offer["transfer_centavos"], offer["covered_by_credit"]),
            (100_000, MONTHLY - 100_000, False),
        )
        sent = self.upload().json()
        self.assertEqual(
            (sent["amount_centavos"], sent["credit_centavos"], sent["transfer_centavos"]),
            (MONTHLY, 100_000, MONTHLY - 100_000),
        )
        body = self.get(self.base + "payment/").json()
        self.assertEqual(body["credit"]["reserved_centavos"], 100_000)
        self.assertEqual(self.get("/v1/account/referrals/").json()["reserved"], 100_000)

    def test_a_bill_the_credit_covers_is_offered_as_covered_and_a_proof_is_refused(self):
        self.grant(MONTHLY)
        offer = self.get(self.base + "payment/").json()["offers"][0]
        self.assertTrue(offer["covered_by_credit"])
        self.assertEqual(offer["transfer_centavos"], 0)
        response = self.upload()
        self.error(response, 409, "covered_by_credit")

    def test_pay_with_credit_settles_the_bill_and_answers_201(self):
        self.grant(MONTHLY)
        response = self.post(self.base + "pay-with-credit/", {"plan": "monthly"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(
            (body["status"], body["credit_centavos"], body["transfer_centavos"], body["cycle"]["sequence"]),
            ("confirmed", MONTHLY, 0, 1),
        )
        self.assertEqual(Subscription.objects.get(pk=self.subscription.pk).state, "active")
        self.assertEqual(self.get("/v1/account/referrals/").json()["balance"], 0)
        # The proof of a bill paid with credit does not exist.
        self.error(self.get(self.base + f"payments/{body['id']}/proof/"), 404, "not_found")

    def test_pay_with_credit_refuses_an_uncovered_bill_and_another_accounts_site(self):
        self.grant(MONTHLY - 1)
        self.error(self.post(self.base + "pay-with-credit/", {"plan": "monthly"}), 409, "credit_insufficient")
        stranger = self.other_client()
        self.error(
            self.post(self.base + "pay-with-credit/", {"plan": "monthly"}, client=stranger), 404,
            "not_found",
        )

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_pay_with_credit_is_closed_while_off(self):
        self.error(self.post(self.base + "pay-with-credit/", {"plan": "monthly"}), 404, "not_found")


@override_settings(BILLING_ENABLED=True, **ON)
class StaffRoutesTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.subscription = make_subscription("owner@example.test", "owner-store")
        self.account = self.subscription.site.account
        self.staff_sign_in()

    def referrals(self, action, data):
        return self.call("POST", f"/staff/v1/finance/referrals/{action}/", data)

    def test_a_correction_needs_a_reason_and_is_audited(self):
        bad = self.referrals("correct", {
            "account": self.account.public_id, "amount_centavos": 5_000, "reason": "",
        })
        self.assertEqual(bad.status_code, 400)
        ok = self.referrals("correct", {
            "account": self.account.public_id, "amount_centavos": 5_000,
            "reason": "Coach Ed, case 1", "request_id": "r1",
        })
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["balance_centavos"], 5_000)
        entry = CreditEntry.objects.get()
        self.assertEqual((entry.kind, entry.operator, entry.reason), ("correction", self.operator, "Coach Ed, case 1"))
        self.assertEqual(len(self.entries("billing_credit_correction")), 1)

    def test_the_view_lists_the_referrer_the_referred_and_the_rows(self):
        referred = make_subscription("new@example.test", "new-store").site.account
        referred.referred_by = self.account
        referred.save()
        credits.correct(self.operator, self.account, 7_000, "test grant")
        body = self.referrals("view", {"account": self.account.public_id}).json()
        self.assertEqual(body["referral_code"], self.account.referral_code)
        self.assertEqual([r["account"] for r in body["referred"]], [referred.public_id])
        self.assertEqual((body["balance_centavos"], len(body["entries"])), (7_000, 1))
        self.assertEqual(body["entries"][0]["kind"], "correction")
        self.assertNotIn("new@example.test", str(body))
        self.assertEqual(len(self.entries("hq_referrals_viewed")), 1)
        down = self.referrals("view", {"account": referred.public_id}).json()
        self.assertEqual(down["referred_by"], self.account.public_id)

    def test_an_unknown_account_is_not_found(self):
        self.assertEqual(self.referrals("view", {"account": "acc_nope"}).status_code, 404)
        self.assertEqual(self.referrals("view", {}).status_code, 404)

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_both_routes_are_closed_while_off(self):
        for action in ("view", "correct"):
            self.assertEqual(
                self.referrals(action, {"account": self.account.public_id}).status_code, 404
            )

    def test_finance_sees_the_credit_and_the_amount_to_confirm(self):
        credits.correct(self.operator, self.account, 100_000, "test grant")
        payment = submit(self.subscription, self.subscription.trial_start_at + days(1))
        body = self.finance("detail/", {"payment": payment.public_id}).json()
        self.assertEqual(
            (body["amount_centavos"], body["credit_centavos"], body["transfer_centavos"]),
            (MONTHLY, 100_000, MONTHLY - 100_000),
        )
        wrong = self.finance("confirm/", {"payment": payment.public_id, "amount_received_centavos": MONTHLY})
        self.assertEqual(wrong.status_code, 409)
        right = self.finance(
            "confirm/", {"payment": payment.public_id, "amount_received_centavos": MONTHLY - 100_000}
        )
        self.assertEqual(right.status_code, 200, right.content)
        self.assertEqual(BankTransferPayment.objects.get().status, "confirmed")
