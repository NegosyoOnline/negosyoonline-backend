"""The Finance routes under /staff/v1/finance/ on the hq host: an operator reviews bank
transfer proofs. They are reached only through the staff routes' own origin and session
checks, and they are closed while bank transfer is off."""
import ast
import json
from datetime import date, datetime
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing import services, staff
from apps.billing.models import BankTransferEvent, BankTransferPayment, BillingCycle, Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, JPEG, MANILA, PDF, PNG, T0, TRIAL_END, days, fresh, make_operator,
    make_subscription, method_on_file, submit, transfers_on,
)
from apps.billing.tests.test_transitions import snapshot
from apps.common.errors import ApiError
from apps.inbox import contacts_staff
from apps.inbox import staff as inbox_staff
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.base import HQ, HqAdminMixin
from apps.operators.tests.test_staff_routes import STAFF, StaffTestCase, on_staff
from apps.sites.tests.test_concurrency import run_together

BASE = "/staff/v1/finance/payments/"
PATHS = ("list/", "detail/", "proof/", "confirm/", "reject/")
SENT = T0 + days(1)
AT = T0 + days(2)  # when the operator acts
PAYMENT_KEYS = [
    "amount_centavos", "amount_received_centavos", "currency", "cycle", "decided_at",
    "decided_by", "id", "plan", "proof", "reject_note", "reject_reason", "site", "status",
    "submitted_at", "transfer_reference", "transferred_on",
]
Status = BankTransferPayment.Status


@on_staff
@override_settings(BILLING_ENABLED=True, BILLING_LAUNCH_PROMO_ENABLED=True)
class FinanceTestCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        switched = transfers_on()
        self.store = switched.__enter__()
        self.addCleanup(switched.__exit__, None, None, None)
        clock = mock.patch.object(staff, "_now", return_value=AT)
        clock.start()
        self.addCleanup(clock.stop)
        self.identity = self.enrolled()
        self.operator = self.identity.user

    def finance(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def entries(self, action):
        return list(OperatorAuditEntry.objects.filter(action=action).order_by("pk"))


class FinanceGateTests(FinanceTestCase):
    def test_the_routes_are_registered_beside_the_sign_in_routes_and_nothing_else_changed(self):
        self.assertEqual(sorted(staff.ROUTES), sorted(f"finance/payments/{p}" for p in PATHS))
        for path in staff.ROUTES:
            self.assertEqual(staff_views.ROUTES[path][0], "POST")
        self.assertEqual(
            sorted(set(staff_views.ROUTES) - set(staff.ROUTES) - set(inbox_staff.ROUTES) - set(contacts_staff.ROUTES)),
            ["console/", "me/", "sign-in/", "sign-in/code/", "sign-in/verify/", "sign-out/"],
        )

    def test_no_finance_route_takes_the_place_of_another_staff_route(self):
        # ROUTES.update overwrites silently, so the names are held here.
        sign_in_routes = {"sign-in/", "sign-in/code/", "sign-in/verify/", "sign-out/", "me/", "console/"}
        for path in staff.ROUTES:
            self.assertTrue(path.startswith("finance/"), path)
            self.assertNotIn(path, sign_in_routes)
        self.assertIs(staff_views.ROUTES["me/"][1], staff_views.me)
        self.assertIs(staff_views.ROUTES["console/"][1], staff_views.console)
        self.assertEqual(len(staff_views.ROUTES), len(sign_in_routes) + len(staff.ROUTES) + len(inbox_staff.ROUTES) + len(contacts_staff.ROUTES))

    def test_the_finance_module_imports_nothing_from_the_staff_routes(self):
        with open(staff.__file__) as source:
            tree = ast.parse(source.read())
        imported = [
            f"{node.module}.{name.name}" if isinstance(node, ast.ImportFrom) else name.name
            for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in node.names
        ]
        self.assertEqual([name for name in imported if name.startswith("apps.operators")], [])

    def test_another_origin_is_refused_before_anything_else(self):
        self.staff_sign_in()
        for path in PATHS:
            for origin in ("https://evil.example", None):
                response = self.finance(path, origin=origin)
                self.assertError(response, 403, "origin_not_allowed")
                self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_no_session_and_a_session_before_the_second_step_are_refused(self):
        payment = submit(make_subscription(), SENT)
        for path in PATHS:
            self.assertError(self.finance(path, {"payment": payment.public_id}), 401, "unauthenticated")
        self.staff_email_step()
        for path in PATHS:
            self.assertError(
                self.finance(path, {"payment": payment.public_id}), 401, "second_step_required"
            )
        self.assertEqual(fresh(payment.subscription).state, Subscription.State.TRIAL_ACTIVE)
        self.assertEqual(BankTransferPayment.objects.get().status, Status.SUBMITTED)
        self.assertEqual(self.entries("billing_proof_viewed"), [])

    def test_only_post_and_its_preflight_are_answered(self):
        self.staff_sign_in()
        for path in PATHS:
            self.assertError(self.call("GET", BASE + path), 405, "method_not_allowed")
            response = self.call("OPTIONS", BASE + path, "POST")
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response["Access-Control-Allow-Methods"], "POST, OPTIONS")
        self.assertError(self.finance("other/"), 404, "not_found")

    def test_every_answer_carries_the_staff_headers(self):
        self.staff_sign_in()
        payment = submit(make_subscription(), SENT)
        for path in PATHS:
            response = self.finance(path, {"payment": payment.public_id, "status": "submitted"})
            self.assertEqual(response["Access-Control-Allow-Origin"], STAFF)
            self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["X-Robots-Tag"], "noindex")
            self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_with_the_switch_off_every_route_is_not_found_and_reads_no_billing_table(self):
        payment = submit(make_subscription(), SENT)
        self.staff_sign_in()
        before = snapshot()
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            with CaptureQueriesContext(connection) as queries:
                for path in PATHS:
                    body = {
                        "payment": payment.public_id, "status": "submitted",
                        "amount_received_centavos": payment.amount_centavos, "reason": "other",
                        "note": "x",
                    }
                    self.assertError(self.finance(path, body), 404, "not_found")
        self.assertEqual([q["sql"] for q in queries if "billing_" in q["sql"].lower()], [])
        self.assertEqual(snapshot(), before)
        self.assertEqual(BankTransferPayment.objects.get().status, Status.SUBMITTED)
        self.assertEqual(OperatorAuditEntry.objects.filter(action__startswith="billing_").count(), 0)

    @override_settings(BILLING_ENABLED=False)
    def test_with_billing_off_every_route_is_not_found(self):
        self.staff_sign_in()
        for path in PATHS:
            self.assertError(self.finance(path, {"status": "submitted"}), 404, "not_found")

    @override_settings(HQ_STAFF_ORIGIN="")
    def test_without_a_staff_origin_the_routes_do_not_exist(self):
        for path in PATHS:
            response = self.finance(path)
            self.assertEqual(response.status_code, 404)
            self.assertContains(response, "Page not found.", status_code=404)


class FinanceQueueTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def ids(self, status, **extra):
        response = self.finance("list/", {"status": status, **extra})
        self.assertEqual(response.status_code, 200)
        return [item["id"] for item in response.json()["items"]], response.json()

    def test_the_queue_is_one_status_oldest_first_with_the_count_under_review(self):
        first = submit(make_subscription(), SENT)
        second = submit(make_subscription("ben@example.test", "bens-store"), SENT + days(1))
        rejected = submit(make_subscription("cy@example.test", "cys-store"), SENT)
        services.reject_transfer(rejected, self.operator, reason="unreadable", now=AT)

        ids, body = self.ids("submitted")
        self.assertEqual(ids, [first.public_id, second.public_id])
        self.assertEqual(sorted(body), ["items", "next_cursor", "under_review_count"])
        self.assertEqual((body["next_cursor"], body["under_review_count"]), (None, 2))
        self.assertEqual(sorted(body["items"][0]), PAYMENT_KEYS)
        self.assertEqual(self.ids("rejected")[0], [rejected.public_id])
        self.assertEqual(self.ids("rejected")[1]["under_review_count"], 2)
        self.assertEqual(self.ids("confirmed")[0], [])
        self.assertEqual(self.ids("replaced")[0], [])

    def test_the_queue_is_paged(self):
        made = [
            submit(make_subscription(f"u{i}@example.test", f"store-{i}"), SENT).public_id
            for i in range(3)
        ]
        ids, body = self.ids("submitted", limit=2)
        self.assertEqual((ids, body["next_cursor"]), (made[:2], made[1]))
        ids, body = self.ids("submitted", limit=2, cursor=made[1])
        self.assertEqual((ids, body["next_cursor"]), (made[2:], None))

    def test_a_bad_status_limit_or_cursor_is_refused(self):
        for body in ({}, {"status": "paid"}, {"status": ["submitted"]}, {"status": None}):
            response = self.finance("list/", body)
            self.assertError(response, 400, "invalid_request")
            self.assertEqual(list(response.json()["error"]["fields"]), ["status"])
        for limit in ("2", 1.5, True):
            response = self.finance("list/", {"status": "submitted", "limit": limit})
            self.assertError(response, 400, "invalid_request")
            self.assertEqual(list(response.json()["error"]["fields"]), ["limit"])
        for cursor in ("nope", "btp_" + "0" * 22, 7):
            response = self.finance("list/", {"status": "submitted", "cursor": cursor})
            self.assertError(response, 400, "invalid_cursor")

    def test_the_queue_holds_no_email_key_or_hash(self):
        payment = submit(make_subscription(), SENT)
        text = self.finance("list/", {"status": "submitted"}).content.decode()
        for secret in ("ana@example.test", payment.proof_key, payment.proof_sha256):
            self.assertNotIn(secret, text)


class FinanceDetailTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def test_one_payment_as_finance_sees_it(self):
        subscription = make_subscription()
        payment = submit(
            subscription, SENT, plan_code="launch_promo", transfer_reference="REF-1",
        )
        payment, subscription = BankTransferPayment.objects.get(), fresh(subscription)
        response = self.finance("detail/", {"payment": payment.public_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "id": payment.public_id,
            "status": "submitted",
            "site": {"id": subscription.site.public_id, "name": subscription.site.display_name},
            "plan": {"code": "launch_promo", "name": "NegosyoOnline Launch Promo (6 Months)", "months": 6},
            "amount_centavos": 499900,
            "currency": "PHP",
            "transferred_on": SENT.astimezone(MANILA).date().isoformat(),
            "transfer_reference": "REF-1",
            "submitted_at": payment.submitted_at.isoformat(),
            "decided_at": None,
            "decided_by": None,
            "amount_received_centavos": None,
            "reject_reason": None,
            "reject_note": None,
            "proof": {"content_type": "image/jpeg", "size_bytes": len(JPEG)},
            "cycle": None,
            "subscriber_email": "ana@example.test",
            "subscription": {
                "state": "trial_active",
                "trial_end_at": subscription.trial_end_at.isoformat(),
                "service_ended_at": None,
            },
            "history": [
                {"code": "submitted", "actor": "owner",
                 "at": BankTransferEvent.objects.get().recorded_at.isoformat()},
            ],
        })
        text = response.content.decode()
        for secret in (payment.proof_key, payment.proof_sha256):
            self.assertNotIn(secret, text)
        self.assertEqual(OperatorAuditEntry.objects.filter(action__startswith="billing_").count(), 0)

    def test_a_decided_payment_shows_who_decided_and_its_history(self):
        payment = submit(make_subscription(), SENT)
        services.reject_transfer(payment, self.operator, reason="other", note="Blurred.", now=AT)
        body = self.finance("detail/", {"payment": payment.public_id}).json()
        self.assertEqual(
            (body["status"], body["decided_by"], body["reject_reason"], body["reject_note"]),
            ("rejected", self.operator.username, "other", "Blurred."),
        )
        self.assertEqual(datetime.fromisoformat(body["decided_at"]), AT)
        self.assertEqual(
            [(e["code"], e["actor"]) for e in body["history"]],
            [("submitted", "owner"), ("rejected", "operator")],
        )

    def test_an_unknown_or_missing_payment(self):
        submit(make_subscription(), SENT)
        for path in ("detail/", "proof/", "confirm/", "reject/"):
            for value in ("btp_" + "0" * 22, "site_x", "1"):
                body = {"payment": value, "amount_received_centavos": 249900, "reason": "other"}
                self.assertError(self.finance(path, body), 404, "not_found")
            for body in ({}, {"payment": 5}, {"payment": None}):
                response = self.finance(path, body)
                self.assertError(response, 400, "invalid_request")
                self.assertEqual(list(response.json()["error"]["fields"]), ["payment"])
        self.assertEqual(BankTransferPayment.objects.get().status, Status.SUBMITTED)
        self.assertEqual(OperatorAuditEntry.objects.filter(action__startswith="billing_").count(), 0)

    def test_a_body_that_is_too_large_or_not_json_is_treated_as_empty(self):
        payment = submit(make_subscription(), SENT)
        response = self.finance("detail/", {"payment": payment.public_id, "pad": "x" * 3000})
        self.assertError(response, 400, "invalid_request")
        response = self.client.post(
            BASE + "detail/", data="not json", content_type="application/json", secure=True,
            HTTP_HOST=HQ, HTTP_ORIGIN=STAFF,
        )
        self.assertError(response, 400, "invalid_request")


class FinanceProofTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def test_the_file_is_sent_whole_with_its_headers_and_each_read_is_audited(self):
        subscription = make_subscription()
        for content, content_type, name in (
            (JPEG, "image/jpeg", "proof.jpg"), (PNG, "image/png", "proof.png"),
            (PDF, "application/pdf", "proof.pdf"),
        ):
            payment = submit(subscription, SENT, content=content)
            response = self.finance("proof/", {"payment": payment.public_id})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.streaming)
            self.assertEqual(response.content, content)
            self.assertEqual(response["Content-Type"], content_type)
            self.assertEqual(response["Content-Disposition"], f'inline; filename="{name}"')
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["Access-Control-Allow-Origin"], STAFF)
        entries = self.entries("billing_proof_viewed")
        self.assertEqual(len(entries), 3)
        self.assertEqual({entry.operator for entry in entries}, {self.operator})
        self.assertEqual(entries[-1].target_id, subscription.site.public_id)
        self.assertEqual(entries[-1].detail, {"payment": payment.public_id})

    def test_the_proof_of_a_replaced_payment_can_still_be_read(self):
        subscription = make_subscription()
        old = submit(subscription, SENT)
        submit(subscription, SENT + days(1), content=PNG)
        response = self.finance("proof/", {"payment": old.public_id})
        self.assertEqual((response.status_code, response.content), (200, JPEG))
        self.assertEqual(len(self.entries("billing_proof_viewed")), 1)


class FinanceConfirmTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def confirm(self, payment, amount=None):
        amount = payment.amount_centavos if amount is None else amount
        return self.finance(
            "confirm/", {"payment": payment.public_id, "amount_received_centavos": amount}
        )

    def test_a_confirmation_opens_exactly_one_paid_cycle_and_names_the_operator(self):
        subscription = make_subscription()
        payment = submit(subscription, SENT)
        response = self.confirm(payment)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(sorted(body), PAYMENT_KEYS)
        self.assertEqual(
            (body["status"], body["decided_by"], body["amount_received_centavos"]),
            ("confirmed", self.operator.username, 249900),
        )
        self.assertEqual(datetime.fromisoformat(body["decided_at"]), AT)
        cycle = BillingCycle.objects.get(subscription=subscription, settlement="paid")
        self.assertEqual((cycle.start_at, cycle.end_at), (TRIAL_END, CYCLE_1_END))
        self.assertEqual(body["cycle"], {
            "sequence": cycle.sequence, "start_at": cycle.start_at.isoformat(),
            "end_at": cycle.end_at.isoformat(),
        })
        row = BankTransferPayment.objects.get()
        self.assertEqual((row.status, row.decided_by, row.decided_at), (Status.CONFIRMED, self.operator, AT))
        entry, = self.entries("billing_payment_confirmed")
        self.assertEqual((entry.operator, entry.target_id), (self.operator, subscription.site.public_id))
        self.assertEqual(entry.detail["payment"], payment.public_id)

    def test_the_launch_promo_is_one_cycle_of_six_months(self):
        subscription = make_subscription()
        payment = submit(subscription, SENT, plan_code="launch_promo")
        body = self.confirm(payment).json()
        self.assertEqual((body["status"], body["amount_centavos"]), ("confirmed", 499900))
        cycle = BillingCycle.objects.get(subscription=subscription, settlement="paid")
        self.assertEqual(cycle.start_at, TRIAL_END)
        self.assertEqual(cycle.end_at.astimezone(MANILA).date(), date(2027, 5, 25))
        self.assertEqual(subscription.cycles.filter(settlement="paid").count(), 1)

    def test_confirming_twice_writes_nothing_more(self):
        subscription = make_subscription()
        payment = submit(subscription, SENT)
        first = self.confirm(payment).json()
        before = snapshot()
        again = self.confirm(payment)
        self.assertEqual((again.status_code, again.json()), (200, first))
        self.assertEqual(snapshot(), before)
        self.assertEqual(subscription.cycles.filter(settlement="paid").count(), 1)
        self.assertEqual(payment.events.filter(code="confirmed").count(), 1)
        self.assertEqual(len(self.entries("billing_payment_confirmed")), 1)

    def test_a_replaced_proof_cannot_be_confirmed_and_settles_nothing(self):
        subscription = make_subscription()
        old = submit(subscription, SENT)
        new = submit(subscription, SENT + (AT - SENT) / 2, content=PNG)
        before = snapshot()
        self.assertError(self.confirm(old), 409, "not_under_review")
        self.assertEqual(snapshot(), before)
        self.assertEqual(subscription.cycles.filter(settlement="paid").count(), 0)
        self.assertEqual(self.entries("billing_payment_confirmed"), [])
        self.assertEqual(self.confirm(new).json()["status"], "confirmed")
        self.assertEqual(BankTransferPayment.objects.get(pk=old.pk).status, Status.REPLACED)

    def test_a_rejected_proof_cannot_be_confirmed(self):
        payment = submit(make_subscription(), SENT)
        services.reject_transfer(payment, self.operator, reason="unreadable", now=AT)
        self.assertError(self.confirm(payment), 409, "not_under_review")

    def test_the_amount_must_be_the_amount_due(self):
        subscription = make_subscription()
        payment = submit(subscription, SENT)
        before = snapshot()
        for amount in (249800, 0, 499900):
            self.assertError(self.confirm(payment, amount), 409, "amount_mismatch")
        for amount in ("249900", 249900.0, True, None, [249900]):
            response = self.confirm(payment, amount) if amount is not None else self.finance(
                "confirm/", {"payment": payment.public_id}
            )
            self.assertError(response, 400, "invalid_request")
            self.assertEqual(list(response.json()["error"]["fields"]), ["amount_received_centavos"])
        self.assertEqual(snapshot(), before)
        self.assertEqual(BankTransferPayment.objects.get().status, Status.SUBMITTED)

    def test_a_promo_proof_for_a_site_that_has_since_paid_is_refused(self):
        subscription = make_subscription()
        payment = submit(subscription, SENT, plan_code="launch_promo")
        with method_on_file():
            services.first_payment_succeeded(
                subscription, paid_at=SENT + (AT - SENT) / 2, reference="other", early=True
            )
        self.assertError(self.confirm(payment), 409, "plan_not_offered")
        self.assertEqual(BankTransferPayment.objects.get().status, Status.SUBMITTED)
        self.assertEqual(subscription.cycles.filter(settlement="paid").count(), 1)

    def test_every_refusal_of_the_lifecycle_is_answered_with_its_code(self):
        payment = submit(make_subscription(), SENT)
        for code in (
            "cancel_scheduled", "paid_ahead", "past_deletion_date", "billing_exempt",
            "invalid_transition",
        ):
            refusal = ApiError(code, "Refused.", status=409)
            with mock.patch.object(services, "confirm_transfer", side_effect=refusal):
                response = self.confirm(payment)
            self.assertError(response, 409, code)
            self.assertEqual(response.json()["error"]["message"], "Refused.")


class FinanceRejectTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def reject(self, payment, **body):
        return self.finance("reject/", {"payment": payment.public_id, **body})

    def test_a_rejection_changes_nothing_about_the_subscription_and_can_be_replaced(self):
        subscription = make_subscription()
        payment = submit(subscription, SENT)
        before = snapshot()
        response = self.reject(payment, reason="amount_mismatch", note="  Short by 100.  ")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(sorted(body), PAYMENT_KEYS)
        self.assertEqual(
            (body["status"], body["reject_reason"], body["reject_note"], body["decided_by"]),
            ("rejected", "amount_mismatch", "Short by 100.", self.operator.username),
        )
        self.assertEqual(snapshot(), before)
        entry, = self.entries("billing_payment_rejected")
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual(entry.detail, {"payment": payment.public_id, "reason": "amount_mismatch"})
        again = submit(subscription, AT + days(1), content=PNG)
        self.assertEqual(again.status, Status.SUBMITTED)

    def test_rejecting_twice_keeps_the_first_reason(self):
        payment = submit(make_subscription(), SENT)
        first = self.reject(payment, reason="unreadable").json()
        again = self.reject(payment, reason="other", note="Changed my mind.")
        self.assertEqual((again.status_code, again.json()), (200, first))
        self.assertEqual(len(self.entries("billing_payment_rejected")), 1)

    def test_the_reason_and_the_note_are_checked(self):
        payment = submit(make_subscription(), SENT)
        for body, field in (
            ({}, "reason"), ({"reason": "because"}, "reason"), ({"reason": 3}, "reason"),
            ({"reason": "other"}, "note"), ({"reason": "other", "note": "  "}, "note"),
            ({"reason": "unreadable", "note": "x" * 301}, "note"),
            ({"reason": "unreadable", "note": 5}, "note"),
        ):
            response = self.reject(payment, **body)
            self.assertError(response, 400, "invalid_request")
            self.assertEqual(list(response.json()["error"]["fields"]), [field], body)
        self.assertEqual(BankTransferPayment.objects.get().status, Status.SUBMITTED)

    def test_a_confirmed_or_replaced_payment_cannot_be_rejected(self):
        subscription = make_subscription()
        old = submit(subscription, SENT)
        new = submit(subscription, SENT + (AT - SENT) / 2, content=PNG)
        self.assertError(self.reject(old, reason="unreadable"), 409, "not_under_review")
        services.confirm_transfer(new, self.operator, amount_received_centavos=249900, now=AT)
        self.assertError(self.reject(new, reason="unreadable"), 409, "not_under_review")
        self.assertEqual(self.entries("billing_payment_rejected"), [])


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(
    HQ_HOST=HQ, ALLOWED_HOSTS=[HQ, "testserver"], HQ_STAFF_ORIGIN=STAFF, BILLING_ENABLED=True
)
class FinanceConcurrencyTests(HqAdminMixin, TransactionTestCase):
    """Two operators at once, through the routes. The rows are committed, so each thread's
    own connection sees the session and the payment."""

    def setUp(self):
        switched = transfers_on()
        switched.__enter__()
        self.addCleanup(switched.__exit__, None, None, None)
        clock = mock.patch.object(staff, "_now", return_value=AT)
        clock.start()
        self.addCleanup(clock.stop)
        self.open_hq_session(make_operator())

    def operator_call(self, path, body):
        """One call in its own client, with the signed-in operator's cookie."""
        def run():
            client = Client()
            client.cookies = self.client.cookies
            return client.post(
                BASE + path, data=json.dumps(body), content_type="application/json",
                secure=True, HTTP_HOST=HQ, HTTP_ORIGIN=STAFF,
            )
        return run

    def test_two_confirmations_at_once_settle_one_cycle(self):
        subscription = make_subscription()
        payment = submit(subscription, SENT)
        body = {"payment": payment.public_id, "amount_received_centavos": 249900}
        results = run_together(self.operator_call("confirm/", body), self.operator_call("confirm/", body))
        self.assertEqual([r.status_code for r in results], [200, 200], results)
        self.assertEqual(subscription.cycles.filter(settlement="paid").count(), 1)
        self.assertEqual(payment.events.filter(code="confirmed").count(), 1)
        self.assertEqual(
            OperatorAuditEntry.objects.filter(action="billing_payment_confirmed").count(), 1
        )

    def test_a_confirmation_and_a_rejection_at_once_decide_once(self):
        subscription = make_subscription()
        payment = submit(subscription, SENT)
        results = run_together(
            self.operator_call(
                "confirm/", {"payment": payment.public_id, "amount_received_centavos": 249900}
            ),
            self.operator_call("reject/", {"payment": payment.public_id, "reason": "unreadable"}),
        )
        self.assertEqual(sorted(r.status_code for r in results), [200, 409], results)
        row = BankTransferPayment.objects.get()
        paid = subscription.cycles.filter(settlement="paid").count()
        self.assertEqual(paid, 1 if row.status == Status.CONFIRMED else 0)
        self.assertEqual(
            OperatorAuditEntry.objects.filter(action__startswith="billing_payment_").count(), 1
        )

    def test_a_confirmation_racing_a_replacing_proof_matches_one_of_the_two_orders(self):
        for attempt in range(5):
            subscription = make_subscription(f"u{attempt}@example.test", f"store-{attempt}")
            payment = submit(subscription, SENT)
            confirmed_before = OperatorAuditEntry.objects.filter(
                action="billing_payment_confirmed"
            ).count()

            response, sent = run_together(
                self.operator_call(
                    "confirm/", {"payment": payment.public_id, "amount_received_centavos": 249900}
                ),
                lambda: submit(subscription, AT, content=PNG),
            )

            payment.refresh_from_db()
            paid = subscription.cycles.filter(settlement="paid").count()
            confirmed = OperatorAuditEntry.objects.filter(
                action="billing_payment_confirmed"
            ).count() - confirmed_before
            if response.status_code == 200:
                # confirmed first, so the next period is paid and a new proof is refused
                self.assertEqual((payment.status, paid, confirmed), ("confirmed", 1, 1))
                self.assertEqual(sent.code, "paid_ahead")
                self.assertEqual(subscription.transfers.count(), 1)
            else:
                # replaced first, so the old proof has nothing to confirm
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()["error"]["code"], "not_under_review")
                self.assertEqual((payment.status, paid, confirmed), ("replaced", 0, 0))
                self.assertEqual(payment.events.filter(code="confirmed").count(), 0)
                self.assertEqual(sent.status, "submitted")
                self.assertEqual(subscription.transfers.count(), 2)
            self.assertLessEqual(subscription.transfers.filter(status="submitted").count(), 1)
