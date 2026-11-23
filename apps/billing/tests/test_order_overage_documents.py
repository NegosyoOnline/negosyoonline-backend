"""Order overage on the statement, the BIR invoice item, the owner statement API, and the rule
that a sale is never blocked (OV-3, OV-5)."""
from unittest import mock

from django.test import TestCase, override_settings

from apps.billing import plan, services, usage
from apps.billing.models import ChargeLine
from apps.billing.tests.base import days, fresh, make_active, make_operator, submit, transfers_on
from apps.billing.tests.test_order_overage import AFTER, LABEL_20, MONTHLY, ON, counts
from apps.invoices.models import ManualInvoiceItem
from apps.common.ids import new_public_id
from apps.shop.models import ShopOrder
from apps.shop.tests.base import ShopTestCase
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.statements import documents, issue
from apps.statements.tests.base import make_issuer
from apps.billing.tests.test_order_overage import produce

DOCS = dict(ON, BILLING_STATEMENTS_ENABLED=True, BILLING_INVOICE_REGISTER_ENABLED=True)


def fake_store(document):
    return documents.Stored(key=f"pdf-{document['number']}", sha256="a" * 64, size=100)


def pay_with_order_line(case, subscription, number=520):
    """A renewal that carries the order line, confirmed. Returns the payment."""
    with counts(number):
        usage.produce_order_entries(AFTER)
    at = AFTER + days(1)
    payment = submit(fresh(subscription), at)
    return services.confirm_transfer(
        payment, case.operator, amount_received_centavos=payment.amount_centavos, now=at
    )


@override_settings(**DOCS)
class DocumentLineTests(TestCase):
    def setUp(self):
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        patcher = mock.patch.object(documents, "store_document", side_effect=fake_store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.operator = make_operator()
        make_issuer(verified=True)

    def test_a_statement_and_an_invoice_item_each_get_their_own_order_line(self):
        payment = pay_with_order_line(self, make_active())
        statement = issue.issue_for_payment(payment, now=AFTER + days(1))
        lines = list(statement.lines.all())
        self.assertEqual([line.kind for line in lines], ["base", "usage"])
        order = lines[1]
        self.assertEqual(
            (order.description, order.quantity, order.unit_centavos, order.amount_centavos),
            (LABEL_20, 20, 500, 10_000),
        )
        self.assertEqual(lines[0].amount_centavos, MONTHLY)
        self.assertEqual(statement.total_centavos, MONTHLY + 10_000)
        item = ManualInvoiceItem.objects.get(source_ref=payment.public_id)
        self.assertEqual(len(item.lines), 2)
        invoice_line = item.lines[1]
        self.assertTrue(invoice_line["description"].startswith(LABEL_20 + ", "))
        self.assertEqual(
            (invoice_line["quantity"], invoice_line["unit_centavos"], invoice_line["amount_centavos"]),
            (20, 500, 10_000),
        )
        self.assertEqual(sum(l["amount_centavos"] for l in item.lines), payment.amount_received_centavos)

    def test_a_usage_only_payment_of_orders_has_that_one_line(self):
        subscription = make_active()
        with counts(540):
            produce()
        self.assertEqual(ChargeLine.objects.count(), 1)
        # A cancelled account pays what it owes alone.
        services.cancel(subscription, now=AFTER - days(5))
        at = AFTER + days(1)
        payment = submit(fresh(subscription), at, plan_code=plan.USAGE_ONLY)
        confirmed = services.confirm_transfer(
            payment, self.operator, amount_received_centavos=payment.amount_centavos, now=at
        )
        statement = issue.issue_for_payment(confirmed, now=at)
        self.assertEqual(statement.kind, "usage_only")
        self.assertEqual(
            [(l.kind, l.description, l.amount_centavos) for l in statement.lines.all()],
            [("usage", "Orders over 500: 40 × PHP 5", 20_000)],
        )
        self.assertEqual(usage.overage_name(confirmed), "orders")


@override_settings(**DOCS)
class StatementApiTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        patcher = mock.patch.object(documents, "store_document", side_effect=fake_store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.operator = make_operator()
        make_issuer(verified=True)

    def test_the_owner_sees_the_order_line_as_kind_orders(self):
        site_id = self.create_site()["id"]
        subscription = Site.objects.get(public_id=site_id).subscription
        with mock.patch("apps.billing.methods.active_at", return_value=True):
            services.first_payment_succeeded(
                subscription, paid_at=subscription.trial_end_at, reference="first"
            )
        subscription = fresh(subscription)
        cycle = subscription.cycles.get(sequence=1)
        with counts(520):
            usage.produce_order_entries(cycle.end_at + plan.LATE_ADMISSION)
        at = cycle.end_at + plan.LATE_ADMISSION + days(1)
        payment = submit(subscription, at)
        confirmed = services.confirm_transfer(
            payment, self.operator, amount_received_centavos=payment.amount_centavos, now=at
        )
        statement = issue.issue_for_payment(confirmed, now=at)
        body = self.get(f"/v1/sites/{site_id}/billing/statements/{statement.number}/").json()
        self.assertEqual([l["kind"] for l in body["lines"]], ["base", "orders"])
        order = body["lines"][1]
        self.assertEqual(
            order,
            {"position": 2, "kind": "orders", "description": LABEL_20, "quantity": 20,
             "unit_centavos": 500, "amount_centavos": 10_000},
        )
        self.assertEqual(body["lines"][0]["kind"], "base")


@override_settings(BILLING_ENABLED=True, BILLING_ORDER_OVERAGE_ENABLED=True,
                   BILLING_ORDER_OVERAGE_FROM="2026-01-01")
class SaleIsNeverBlockedTests(ShopTestCase):
    def test_an_order_is_accepted_at_1000_orders_and_beyond_the_charge(self):
        template = self.placed()  # a real order first, to copy its shape
        base = ShopOrder.objects.get(order_number=template["order_number"])
        rows = []
        for n in range(1000):
            row = ShopOrder.objects.get(pk=base.pk)
            row.pk = None
            row.id = None
            row.public_id = new_public_id("sho")
            row.order_number = f"SOX{n:05d}"
            row.idempotency_key = f"bulk-{n}"
            rows.append(row)
        ShopOrder.objects.bulk_create(rows)
        self.assertEqual(ShopOrder.objects.filter(site=self.site).count(), 1001)
        body = self.placed()
        self.assertFalse(body["replay"])
        self.assertEqual(ShopOrder.objects.filter(site=self.site).count(), 1002)


@override_settings(**ON)
class UsageRouteTests(SitesApiTestCase):
    def test_the_usage_route_opens_for_the_order_switch_alone_and_lists_the_line(self):
        site_id = self.create_site()["id"]
        path = f"/v1/sites/{site_id}/billing/usage/"
        subscription = Site.objects.get(public_id=site_id).subscription
        with mock.patch("apps.billing.methods.active_at", return_value=True):
            services.first_payment_succeeded(
                subscription, paid_at=subscription.trial_end_at, reference="first"
            )
        cycle = fresh(subscription).cycles.get(sequence=1)
        with counts(520):
            usage.produce_order_entries(cycle.end_at + plan.LATE_ADMISSION)
        now = cycle.end_at + plan.LATE_ADMISSION + days(1)
        with mock.patch("apps.billing.api.timezone") as clock_patch:
            clock_patch.now.return_value = now
            body = self.get(path).json()
        self.assertEqual(body["due_centavos"], 10_000)
        self.assertEqual(
            [(i["kind"], i["label"], i["amount_centavos"]) for i in body["items"]],
            [("overage_orders", LABEL_20, 10_000)],
        )
        with override_settings(BILLING_ORDER_OVERAGE_ENABLED=False):
            self.error(self.get(path), 404, "not_found")
