"""Helpers shared by the lifecycle tests. Time is always passed in; no test reads the clock."""
from datetime import datetime, timedelta

from django.test import TestCase, override_settings

from apps.billing.clock import MANILA
from apps.billing.models import Subscription
from apps.billing.tests.base import make_subscription
from apps.sites.models import Site

State = Subscription.State
NOW = datetime(2026, 12, 30, 10, 0, tzinfo=MANILA)
ENDED = datetime(2026, 11, 25, 10, 0, tzinfo=MANILA)
DUE = datetime(2026, 12, 25, 10, 0, tzinfo=MANILA)


def days(n):
    return timedelta(days=n)


def ended_site(email="ana@example.test", name="anas-store", state=State.CANCELLED_NO_PAYMENT_METHOD,
               ended=ENDED, due=DUE, **extra):
    """A subscription whose service ended at `ended` and whose site is due for deletion at
    `due`, written directly (the dates are what the billing sweep stores)."""
    subscription = make_subscription(email=email, name=name)
    Subscription.objects.filter(pk=subscription.pk).update(
        state=state, service_ended_at=ended, download_until=ended + days(30), deletion_due_at=due,
        **extra,
    )
    subscription.refresh_from_db()
    return subscription


def submitted_proof(subscription, status="submitted"):
    """A bank transfer proof row, written directly."""
    from apps.billing.models import BankTransferPayment

    return BankTransferPayment.objects.create(
        subscription=subscription, reference=f"ref-{subscription.pk}-{status}", plan_code="monthly",
        amount_centavos=100, status=status, transferred_on=ENDED.date(), proof_key="k",
        proof_content_type="image/png", proof_size=1, proof_sha256="0" * 64,
        submitted_by=subscription.site.account, submitted_at=ENDED,
    )


def open_charge(subscription, status="open"):
    """A charge line (with the cycle and usage entry it needs), written directly."""
    from apps.billing.models import BillingCycle, ChargeLine, UsageEntry

    cycle = BillingCycle.objects.create(
        subscription=subscription, sequence=1, kind="paid", start_at=ENDED - days(30), end_at=ENDED,
        settlement="paid", plan_code="monthly", plan_version=1, price_centavos=100, term_months=1,
    )
    entry = UsageEntry.objects.create(
        subscription=subscription, cycle=cycle, period_index=1, source_reference=f"u-{subscription.pk}",
        quantity=1, occurred_at=ENDED, received_at=ENDED, status="counted",
    )
    line = ChargeLine.objects.create(
        subscription=subscription, cycle=cycle, period_index=1, entry=entry, quantity=1,
        amount_centavos=100,
    )
    if status != "open":
        ChargeLine.objects.filter(pk=line.pk).update(status=status, closed_at=ENDED, closed_reason="x")
    return line


def second_site(subscription, name="second-store"):
    """Another site of the same account, with no subscription of its own."""
    from apps.sites.tests.base import make_site

    return make_site(subscription.site.account, name=name, display_name="Second", first=False)


@override_settings(BILLING_ENABLED=True)
class LifecycleTestCase(TestCase):
    def site_ids(self, entries):
        return [entry["site"] for entry in entries]

    def refreshed(self, subscription):
        return Subscription.objects.get(pk=subscription.pk)

    def site_of(self, subscription):
        return Site.objects.get(pk=subscription.site_id)
