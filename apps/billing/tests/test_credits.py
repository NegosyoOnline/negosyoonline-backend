"""Subscription Credit (Billing B6, piece BE-2): the append-only ledger, earning on a referred
business's first confirmed payment (R-1), paying with credit, a bill the credit covers in full
(R-2), reversal by a credit note, the HQ correction and the switch."""
from unittest import mock, skipUnless

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from apps.accounts.models import Account
from apps.billing import credits, plan, services
from apps.billing.models import BankTransferEvent, BankTransferPayment, CreditEntry, Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, JPEG, PNG, T0, TRIAL_END, days, fresh, make_active, make_operator,
    make_subscription, method_on_file, submit, transfers_on,
)
from apps.common.errors import ApiError
from apps.operators.models import OperatorAuditEntry
from apps.sites.tests.base import make_site
from apps.statements.managers import Immutable

MONTHLY = 249_900
AT = T0 + days(2)  # when a referred business pays, inside its trial
ON = dict(BILLING_ENABLED=True, BILLING_REFERRALS_ENABLED=True)
Kind = CreditEntry.Kind
Status = BankTransferPayment.Status


def referred_pair(referred_email="new@example.test", referrer_email="ref@example.test"):
    """An active referrer and a trial business it referred."""
    referrer = make_active(email=referrer_email, name=referrer_email.split("@")[0])
    referred = make_subscription(referred_email, referred_email.split("@")[0])
    account = referred.site.account
    account.referred_by = referrer.site.account
    account.save()
    return referrer, referred


def kinds(account):
    return list(
        CreditEntry.objects.filter(account=account).order_by("pk").values_list("kind", "amount_centavos")
    )


class CreditCase(TestCase):
    switches = ON

    def setUp(self):
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        settings = override_settings(**self.switches)
        settings.enable()
        self.addCleanup(settings.disable)
        self.operator = make_operator()

    def confirm(self, payment, at=AT, amount=None):
        received = payment.amount_centavos - payment.credit_centavos if amount is None else amount
        return services.confirm_transfer(
            payment, self.operator, amount_received_centavos=received, now=at
        )

    def grant(self, account, centavos, reason="test grant"):
        return credits.correct(self.operator, account, centavos, reason)

    def refused(self, code, call, *args, **kwargs):
        with self.assertRaises(ApiError) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception


class LedgerTests(CreditCase):
    def setUp(self):
        super().setUp()
        self.account = make_subscription().site.account
        self.entry = self.grant(self.account, 1000)

    def test_a_row_is_never_changed_or_deleted(self):
        self.entry.amount_centavos = 5
        with self.assertRaises(Immutable):
            self.entry.save()
        with self.assertRaises(Immutable):
            self.entry.delete()
        with self.assertRaises(Immutable):
            CreditEntry.objects.all().delete()
        with self.assertRaises(Immutable):
            CreditEntry.objects.update(amount_centavos=1)

    def test_the_key_is_unique(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CreditEntry.objects.create(
                account=self.account, kind=Kind.EARN, amount_centavos=1,
                idempotency_key=self.entry.idempotency_key,
                payment=BankTransferPayment.objects.create(
                    subscription=make_subscription("x@example.test", "x-store"), plan_code="monthly",
                    amount_centavos=1, transferred_on=T0.date(), proof_key="", proof_content_type="",
                    proof_size=0, proof_sha256="", submitted_by=self.account, submitted_at=T0,
                ),
            )

    def test_each_kind_has_its_sign_and_a_correction_has_a_reason(self):
        bad = [
            dict(kind=Kind.CORRECTION, amount_centavos=0, reason="r", operator=self.operator),
            dict(kind=Kind.CORRECTION, amount_centavos=5, reason="", operator=self.operator),
            dict(kind=Kind.CORRECTION, amount_centavos=5, reason="r"),
            dict(kind=Kind.REVERSE, amount_centavos=5),
        ]
        for number, values in enumerate(bad):
            with self.assertRaises(IntegrityError), transaction.atomic():
                CreditEntry.objects.create(
                    account=self.account, idempotency_key=f"bad:{number}", **values
                )

    def test_a_correction_needs_a_reason_and_never_takes_the_balance_below_zero(self):
        self.refused("invalid_request", credits.correct, self.operator, self.account, 5, " ")
        self.refused("invalid_request", credits.correct, self.operator, self.account, 0, "r")
        self.refused("exceeds_balance", credits.correct, self.operator, self.account, -1001, "r")
        credits.correct(self.operator, self.account, -400, "Coach Ed: case 7")
        self.assertEqual(credits.balance(self.account), 600)
        audit = OperatorAuditEntry.objects.get(
            action="billing_credit_correction", reason="Coach Ed: case 7"
        )
        self.assertEqual((audit.reason, audit.operator), ("Coach Ed: case 7", self.operator))

    def test_the_same_request_id_writes_once(self):
        first = credits.correct(self.operator, self.account, 50, "r", request_id="abc")
        again = credits.correct(self.operator, self.account, 50, "r", request_id="abc")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(credits.balance(self.account), 1050)

    @skipUnless(connection.vendor == "postgresql", "the trigger is PostgreSQL's")
    def test_the_trigger_refuses_raw_sql(self):
        for sql in ("UPDATE billing_creditentry SET amount_centavos = 1", "DELETE FROM billing_creditentry"):
            with self.assertRaises(Exception) as raised, transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql)
            self.assertIn("final and cannot be changed or deleted", str(raised.exception))
        self.assertEqual(credits.balance(self.account), 1000)


class EarnTests(CreditCase):
    def test_the_first_confirmed_payment_earns_twenty_percent_to_the_peso(self):
        referrer, referred = referred_pair()
        payment = submit(referred, AT)
        self.confirm(payment)
        self.assertEqual(kinds(referrer.site.account), [("earn", 50_000)])  # PHP 499.80 -> 500
        entry = CreditEntry.objects.get()
        self.assertEqual((entry.payment, entry.idempotency_key), (payment, f"earn:{payment.pk}"))
        self.assertEqual(credits.balance(referrer.site.account), 50_000)
        self.assertEqual(credits.earned_count(referrer.site.account), 1)
        self.assertEqual(kinds(referred.site.account), [])

    def test_earn_amounts_of_the_three_plans(self):
        self.assertEqual(credits.earn_amount(499_900), 100_000)  # PHP 999.80 -> 1,000
        self.assertEqual(credits.earn_amount(2_499_000), 499_800)  # 4,998
        self.assertEqual(credits.earn_amount(249_900), 50_000)
        self.assertEqual(credits.earn_amount(0), 0)

    @override_settings(BILLING_LAUNCH_PROMO_ENABLED=True, BILLING_ANNUAL_ENABLED=True)
    def test_the_promo_and_the_annual_plan_earn_their_amounts(self):
        for plan_code, expected in (("launch_promo", 100_000), ("annual", 499_800)):
            referrer, referred = referred_pair(
                f"{plan_code}-new@example.test", f"{plan_code}-ref@example.test"
            )
            payment = submit(referred, AT, plan_code=plan_code)
            self.confirm(payment)
            self.assertIn(("earn", expected), kinds(referrer.site.account))

    def test_a_replayed_earn_writes_nothing_more(self):
        referrer, referred = referred_pair()
        payment = self.confirm(submit(referred, AT))
        credits.earn(payment, AT)
        credits.earn(payment, AT)
        self.assertEqual(CreditEntry.objects.filter(kind=Kind.EARN).count(), 1)

    def test_only_the_first_confirmed_payment_earns(self):
        referrer, referred = referred_pair()
        self.confirm(submit(referred, AT))
        later = CYCLE_1_END - days(5)
        second = self.confirm(submit(fresh(referred), later, content=PNG), at=later)
        self.assertEqual(CreditEntry.objects.filter(kind=Kind.EARN).count(), 1)
        self.assertIsNone(credits.earn(second, later))

    def test_a_first_payment_that_earned_nothing_is_still_the_first(self):
        referrer = make_subscription("trial-ref@example.test", "trial-ref")  # only a trial
        referred = make_subscription("new@example.test", "new-store")
        referred.site.account.referred_by = referrer.site.account
        referred.site.account.save()
        self.confirm(submit(referred, AT))
        self.assertFalse(CreditEntry.objects.exists())  # rule 6: the referrer was not active
        with method_on_file():
            services.first_payment_succeeded(referrer, paid_at=TRIAL_END, reference="ref-first")
        later = CYCLE_1_END - days(5)
        second = self.confirm(submit(fresh(referred), later, content=PNG), at=later)
        self.assertEqual(second.status, Status.CONFIRMED)
        self.assertFalse(CreditEntry.objects.exists())  # R-1: the first payment only, ever

    def test_a_referrer_with_no_active_site_does_not_earn(self):
        referrer, referred = referred_pair()
        services.cancel(referrer, now=T0 + days(20))  # cancelled: not `active` (rule 6)
        Subscription.objects.filter(pk=referrer.pk).update(state=Subscription.State.SUSPENDED)
        self.confirm(submit(referred, AT))
        self.assertFalse(CreditEntry.objects.exists())

    def test_no_referrer_no_credit(self):
        self.confirm(submit(make_subscription(), AT))
        self.assertFalse(CreditEntry.objects.exists())

    def test_a_payment_of_overage_alone_earns_nothing(self):
        referrer, referred = referred_pair()
        payment = BankTransferPayment(
            plan_code=plan.USAGE_ONLY, amount_centavos=5000, usage_centavos=5000, credit_centavos=0
        )
        self.assertEqual(credits.base_paid(payment), 0)

    def test_a_failure_while_earning_never_blocks_the_confirmation(self):
        referrer, referred = referred_pair()
        payment = submit(referred, AT)
        with mock.patch.object(credits, "earn", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.billing.credits", level="ERROR"):
                confirmed = self.confirm(payment)
        self.assertEqual(confirmed.status, Status.CONFIRMED)
        self.assertFalse(CreditEntry.objects.exists())

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_switch_off_earns_nothing(self):
        referrer, referred = referred_pair()
        self.confirm(submit(referred, AT))
        self.assertFalse(CreditEntry.objects.exists())

    def test_the_base_paid_is_after_credit_and_excludes_usage(self):
        payment = BankTransferPayment(
            plan_code="monthly", amount_centavos=MONTHLY + 3000, usage_centavos=3000,
            credit_centavos=100_000,
        )
        self.assertEqual(credits.base_paid(payment), MONTHLY - 100_000)
        payment.credit_centavos = 400_000  # more than the base: the rest pays usage
        self.assertEqual(credits.base_paid(payment), 0)


class SpendTests(CreditCase):
    def setUp(self):
        super().setUp()
        self.subscription = make_subscription("owner@example.test", "owner-store")
        self.account = self.subscription.site.account
        self.grant(self.account, 100_000)

    def test_the_credit_is_taken_off_what_is_transferred_and_reserved(self):
        payment = submit(self.subscription, AT)
        self.assertEqual((payment.amount_centavos, payment.credit_centavos), (MONTHLY, 100_000))
        self.assertEqual(kinds(self.account)[-1], ("reserve", -100_000))
        self.assertEqual(credits.balance(self.account), 0)  # reserved credit is not spendable
        self.assertEqual(credits.reserved(self.account), 100_000)

    def test_the_amount_the_operator_confirms_is_the_amount_less_the_credit(self):
        payment = submit(self.subscription, AT)
        self.refused("amount_mismatch", self.confirm, payment, amount=MONTHLY)
        self.assertEqual(fresh(self.subscription).state, Subscription.State.TRIAL_ACTIVE)
        confirmed = self.confirm(payment, amount=MONTHLY - 100_000)
        self.assertEqual(confirmed.status, Status.CONFIRMED)
        self.assertEqual(confirmed.amount_received_centavos, MONTHLY - 100_000)
        self.assertEqual([k for k, _ in kinds(self.account)], ["correction", "reserve", "apply"])
        self.assertEqual(credits.balance(self.account), 0)
        self.assertEqual(credits.reserved(self.account), 0)
        self.assertEqual(fresh(self.subscription).state, Subscription.State.ACTIVE)

    def test_a_rejection_releases_the_credit(self):
        payment = submit(self.subscription, AT)
        services.reject_transfer(payment, self.operator, reason="unreadable", now=AT)
        self.assertEqual([k for k, _ in kinds(self.account)], ["correction", "reserve", "release"])
        self.assertEqual(credits.balance(self.account), 100_000)
        services.reject_transfer(payment, self.operator, reason="unreadable", now=AT)  # replay
        self.assertEqual(CreditEntry.objects.filter(kind=Kind.RELEASE).count(), 1)

    def test_a_replaced_proof_releases_then_takes_the_credit_again(self):
        first = submit(self.subscription, AT)
        second = submit(self.subscription, AT + days(1), content=PNG)
        self.assertEqual(fresh_payment(first).status, Status.REPLACED)
        self.assertEqual(second.credit_centavos, 100_000)
        self.assertEqual([k for k, _ in kinds(self.account)],
                         ["correction", "reserve", "release", "reserve"])
        self.assertEqual(credits.balance(self.account), 0)

    def test_the_same_proof_again_returns_the_payment_and_reserves_nothing_more(self):
        first = submit(self.subscription, AT)
        again = submit(self.subscription, AT)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(CreditEntry.objects.filter(kind=Kind.RESERVE).count(), 1)

    def test_credit_pays_any_charge_not_only_the_plan(self):
        # Credit against an overage-only payment is also taken (rule 3).
        owed = mock.Mock(centavos=150_000, max_id=0, lines=0)
        with mock.patch("apps.billing.services._usage_only_due", return_value=owed), mock.patch(
            "apps.billing.plan.any_usage_enabled", return_value=True
        ), mock.patch("apps.billing.services.usage.frozen_total", return_value=150_000):
            payment = submit(self.subscription, AT, plan_code=plan.USAGE_ONLY)
        self.assertEqual((payment.amount_centavos, payment.credit_centavos), (150_000, 100_000))
        self.assertEqual(credits.balance(self.account), 0)

    def test_a_credit_that_covers_the_whole_bill_is_not_a_transfer(self):
        self.grant(self.account, MONTHLY)
        self.refused("covered_by_credit", submit, self.subscription, AT)
        self.assertFalse(BankTransferPayment.objects.exists())
        self.assertEqual(credits.balance(self.account), 100_000 + MONTHLY)

    def test_the_earliest_due_bill_gets_the_credit(self):
        later_site = make_site(self.account, name="second-store")
        second = services.start_subscription(later_site, self.account, now=T0 + days(3))
        # The first site's trial ends first, so the second site is not first in line.
        self.assertEqual(submit(second, AT + days(5), content=PNG).credit_centavos, 0)
        self.assertEqual(submit(self.subscription, AT + days(5)).credit_centavos, 100_000)

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_switch_off_takes_no_credit(self):
        payment = submit(self.subscription, AT)
        self.assertEqual(payment.credit_centavos, 0)
        self.assertFalse(CreditEntry.objects.filter(kind=Kind.RESERVE).exists())
        self.assertEqual(self.confirm(payment).amount_received_centavos, MONTHLY)

    @override_settings(BILLING_REFERRALS_ENABLED=False)
    def test_a_reserve_that_exists_when_the_switch_goes_off_is_still_resolved(self):
        with override_settings(BILLING_REFERRALS_ENABLED=True):
            payment = submit(self.subscription, AT)
        self.confirm(payment, amount=MONTHLY - 100_000)
        self.assertEqual(CreditEntry.objects.filter(kind=Kind.APPLY).count(), 1)

    def test_a_payment_row_is_kept_and_its_credit_is_inside_the_amount(self):
        payment = submit(self.subscription, AT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BankTransferPayment.objects.filter(pk=payment.pk).update(credit_centavos=MONTHLY + 1)


def fresh_payment(payment):
    return BankTransferPayment.objects.get(pk=payment.pk)


class PayWithCreditTests(CreditCase):
    def setUp(self):
        super().setUp()
        self.subscription = make_subscription("owner@example.test", "owner-store")
        self.account = self.subscription.site.account

    def pay(self, plan_code="monthly", at=AT, **kwargs):
        return services.pay_with_credit(
            self.subscription, self.account, plan_code=plan_code, now=at, **kwargs
        )

    def test_a_fully_covered_bill_settles_at_once_with_no_proof_and_no_staff_click(self):
        self.grant(self.account, MONTHLY + 5000)
        payment = self.pay()
        self.assertEqual(payment.status, Status.CONFIRMED)
        self.assertEqual(
            (payment.amount_centavos, payment.credit_centavos, payment.amount_received_centavos),
            (MONTHLY, MONTHLY, 0),
        )
        self.assertIsNone(payment.decided_by)
        self.assertEqual((payment.proof_key, payment.proof_size), ("", 0))
        self.assertIsNotNone(payment.cycle)
        self.assertEqual(fresh(self.subscription).state, Subscription.State.ACTIVE)
        self.assertEqual([k for k, _ in kinds(self.account)], ["correction", "reserve", "apply"])
        self.assertEqual(credits.balance(self.account), 5000)
        self.assertEqual(
            list(payment.events.order_by("pk").values_list("code", "actor")),
            [("submitted", "owner"), ("confirmed", "owner")],
        )
        audit = OperatorAuditEntry.objects.get(action="billing_payment_confirmed_by_credit")
        self.assertIsNone(audit.operator)
        self.assertEqual(audit.detail["payment"], payment.public_id)

    def test_it_goes_through_the_same_settlement_as_a_confirmed_transfer(self):
        self.grant(self.account, MONTHLY)
        via_credit = self.pay()
        other = make_subscription("other@example.test", "other-store")
        transferred = self.confirm(submit(other, AT))
        self.assertEqual(
            (via_credit.cycle.sequence, via_credit.cycle.settlement, via_credit.cycle.plan_code),
            (transferred.cycle.sequence, transferred.cycle.settlement, transferred.cycle.plan_code),
        )
        self.assertEqual(
            list(fresh(self.subscription).transitions.values_list("code", flat=True)),
            list(fresh(other).transitions.values_list("code", flat=True)),
        )

    def test_a_credit_that_does_not_cover_the_bill_is_refused_and_changes_nothing(self):
        self.grant(self.account, MONTHLY - 1)
        self.refused("credit_insufficient", self.pay)
        self.assertEqual(credits.balance(self.account), MONTHLY - 1)
        self.assertFalse(BankTransferPayment.objects.exists())

    def test_it_is_closed_while_the_switch_is_off(self):
        self.grant(self.account, MONTHLY)
        with override_settings(BILLING_REFERRALS_ENABLED=False):
            error = self.refused("not_found", self.pay)
        self.assertEqual(error.status, 404)

    def test_a_proof_waiting_is_replaced_and_gives_its_credit_back_first(self):
        self.grant(self.account, 100_000)
        waiting = submit(self.subscription, AT)
        self.assertEqual(waiting.credit_centavos, 100_000)
        self.grant(self.account, MONTHLY)
        payment = self.pay(at=AT + days(1))
        self.assertEqual(fresh_payment(waiting).status, Status.REPLACED)
        self.assertEqual(payment.status, Status.CONFIRMED)
        self.assertEqual([k for k, _ in kinds(self.account)],
                         ["correction", "reserve", "correction", "release", "reserve", "apply"])
        self.assertEqual(credits.balance(self.account), 100_000)

    def test_the_launch_promo_still_needs_the_buyers_consent(self):
        self.grant(self.account, 499_900)
        with override_settings(BILLING_LAUNCH_PROMO_ENABLED=True):
            self.refused("invalid_request", self.pay, "launch_promo")
            payment = self.pay(
                "launch_promo", renewal_consent=True, consent_version=plan.CONSENT_VERSION
            )
        self.assertEqual(payment.status, Status.CONFIRMED)
        self.assertEqual(payment.consent.plan_code, "launch_promo")

    def test_a_replay_of_the_confirmed_state_cannot_double_spend(self):
        self.grant(self.account, MONTHLY)
        self.pay()
        self.refused("paid_ahead", self.pay, "monthly", at=AT + days(1))
        self.assertEqual(credits.balance(self.account), 0)

    def test_it_earns_for_the_referrer_on_the_first_payment_of_the_referred(self):
        referrer, referred = referred_pair()
        self.grant(referred.site.account, MONTHLY)
        services.pay_with_credit(
            referred, referred.site.account, plan_code="monthly", now=AT
        )
        # The base paid after credit is zero, so there is nothing to earn (a PHP 0 base earns none).
        self.assertEqual(credits.balance(referrer.site.account), 0)


class StatementTests(CreditCase):
    def test_the_statement_shows_the_credit_and_the_total_is_what_was_paid(self):
        from apps.statements import documents, issue
        from apps.statements.tests.base import make_issuer

        make_issuer(verified=True)
        subscription = make_subscription("owner@example.test", "owner-store")
        account = subscription.site.account
        self.grant(account, 100_000)
        payment = self.confirm(submit(subscription, AT))
        stored = documents.Stored(key="pdf-x", sha256="a" * 64, size=100)
        with override_settings(BILLING_STATEMENTS_ENABLED=True), mock.patch.object(
            documents, "store_document", return_value=stored
        ):
            statement = issue.issue_for_payment(payment, now=AT)
        lines = [(l.kind, l.description, l.amount_centavos) for l in statement.lines.order_by("position")]
        self.assertEqual(lines[0][0:3:2], ("base", MONTHLY))
        self.assertEqual(lines[1], ("credit", "Subscription Credit applied −₱1,000", -100_000))
        self.assertEqual(statement.total_centavos, MONTHLY - 100_000)

    def test_a_credit_note_for_a_billing_error_takes_back_the_earned_share(self):
        from apps.statements import adjust, documents, issue
        from apps.statements.tests.base import make_issuer

        make_issuer(verified=True)
        referrer, referred = referred_pair()
        payment = self.confirm(submit(referred, AT))
        stored = documents.Stored(key="pdf-x", sha256="a" * 64, size=100)
        with override_settings(BILLING_STATEMENTS_ENABLED=True), mock.patch.object(
            documents, "store_document", return_value=stored
        ):
            statement = issue.issue_for_payment(payment, now=AT)
            adjust.adjust(
                self.operator, statement.public_id, "billing_error",
                [{"description": "Half back", "amount_centavos": -(MONTHLY // 2 + 1)}],
            )
        account = referrer.site.account
        # 50,000 earned; the share of 124,951 of 249,900 is 25,000 (to the centavo, half up)
        self.assertEqual(credits.balance(account), 25_000)
        self.assertEqual(kinds(account)[-1], ("reverse", -25_000))

    def test_a_reversal_never_takes_the_balance_below_zero_and_writes_off_the_spent_part(self):
        from apps.statements import adjust, documents, issue
        from apps.statements.tests.base import make_issuer

        make_issuer(verified=True)
        referrer, referred = referred_pair()
        payment = self.confirm(submit(referred, AT))
        account = referrer.site.account
        credits.correct(self.operator, account, -30_000, "spent elsewhere (test)")  # 20,000 left
        stored = documents.Stored(key="pdf-x", sha256="a" * 64, size=100)
        with override_settings(BILLING_STATEMENTS_ENABLED=True), mock.patch.object(
            documents, "store_document", return_value=stored
        ):
            statement = issue.issue_for_payment(payment, now=AT)
            adjust.adjust(
                self.operator, statement.public_id, "duplicate_payment",
                [{"description": "All back", "amount_centavos": -MONTHLY}],
            )
        self.assertEqual(credits.balance(account), 0)
        off = OperatorAuditEntry.objects.get(action="billing_credit_written_off")
        self.assertEqual(
            (off.detail["written_off_centavos"], off.detail["reversed_centavos"]), (30_000, 20_000)
        )

    def test_other_reasons_do_not_reverse(self):
        from apps.statements import adjust, documents, issue
        from apps.statements.tests.base import make_issuer

        make_issuer(verified=True)
        referrer, referred = referred_pair()
        payment = self.confirm(submit(referred, AT))
        stored = documents.Stored(key="pdf-x", sha256="a" * 64, size=100)
        with override_settings(BILLING_STATEMENTS_ENABLED=True), mock.patch.object(
            documents, "store_document", return_value=stored
        ):
            statement = issue.issue_for_payment(payment, now=AT)
            adjust.adjust(
                self.operator, statement.public_id, "goodwill",
                [{"description": "Thanks", "amount_centavos": -1000}],
            )
        self.assertEqual(credits.balance(referrer.site.account), 50_000)


class SwitchOffTests(CreditCase):
    switches = dict(BILLING_ENABLED=True, BILLING_REFERRALS_ENABLED=False)

    def test_payments_behave_as_they_did_before(self):
        referrer, referred = referred_pair()
        payment = submit(referred, AT)
        self.assertEqual(payment.credit_centavos, 0)
        confirmed = self.confirm(payment)
        self.assertEqual(confirmed.amount_received_centavos, MONTHLY)
        self.assertFalse(CreditEntry.objects.exists())

    def test_the_account_figures_report_not_started(self):
        account = make_subscription().site.account
        view = credits.account_overview(account)
        self.assertFalse(view["started"])
        self.assertEqual((view["balance"], view["earned_count"]), (0, 0))
        self.assertEqual(view["code"], account.referral_code)


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(**ON)
class ReserveConcurrencyTests(TransactionTestCase):
    def test_two_bills_of_one_account_never_reserve_more_than_the_balance(self):
        from apps.sites.tests.test_concurrency import run_together

        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        operator = make_operator()
        first = make_subscription("owner@example.test", "first-store")
        account = first.site.account
        second = services.start_subscription(make_site(account, name="second-store"), account, now=T0)
        credits.correct(operator, account, 100_000, "test grant")

        def pay(subscription, content):
            return lambda: submit(subscription, AT, content=content)

        with mock.patch.object(credits, "is_first_in_line", return_value=True):
            results = run_together(pay(first, JPEG), pay(second, PNG))

        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        taken = sorted(BankTransferPayment.objects.values_list("credit_centavos", flat=True))
        self.assertEqual(taken, [0, 100_000])
        self.assertEqual(credits.balance(account), 0)
        self.assertEqual(sum(a for _, a in kinds(account)), 0)
