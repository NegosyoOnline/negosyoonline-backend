"""Helpers shared by the billing tests. Time is always passed in; no test reads the clock."""
from datetime import datetime, timedelta
from unittest import mock

from apps.accounts.models import Account
from apps.billing import clock, services
from apps.billing.models import Subscription
from apps.sites.tests.base import make_account, make_site

MANILA = clock.MANILA
T0 = datetime(2026, 11, 11, 10, 0, tzinfo=MANILA)  # the account is created; the trial starts
TRIAL_END = datetime(2026, 11, 25, 10, 0, tzinfo=MANILA)
CYCLE_1_END = datetime(2026, 12, 25, 15, 0, tzinfo=MANILA)
CYCLE_2_END = datetime(2027, 1, 25, 15, 0, tzinfo=MANILA)
SECOND = timedelta(seconds=1)


def days(n):
    return timedelta(days=n)


def manila(*parts):
    return datetime(*parts, tzinfo=MANILA)


def method_on_file():
    """As if the site had an active payment method at every instant (B2 makes this real)."""
    return mock.patch("apps.billing.methods.active_at", return_value=True)


def collection_is_live():
    return mock.patch("apps.billing.plan.collection_live", return_value=True)


def make_subscription(email="ana@example.test", name="anas-store", start=T0, now=None):
    """A site whose account was created at `start`, with its subscription as of `now`."""
    account = make_account(email)
    Account.objects.filter(pk=account.pk).update(created_at=start)
    account.refresh_from_db()
    site = make_site(account, name=name)
    return services.start_subscription(site, account, now=now or start)


def fresh(subscription):
    return Subscription.objects.get(pk=subscription.pk)


def make_active(**kwargs):
    """A subscription whose first payment succeeded: cycle 1 runs to CYCLE_1_END."""
    subscription = make_subscription(**kwargs)
    with method_on_file():
        services.first_payment_succeeded(
            subscription, paid_at=TRIAL_END, reference=f"first-{subscription.pk}"
        )
    return fresh(subscription)


def make_attention(**kwargs):
    """Paid for cycle 1; the renewal at CYCLE_1_END failed."""
    subscription = make_active(**kwargs)
    services.renewal_failed(subscription, at=CYCLE_1_END)
    return fresh(subscription)


def make_suspended(**kwargs):
    """Paid for cycle 1, the renewal failed, and 7 days passed."""
    subscription = make_attention(**kwargs)
    services.sweep(now=CYCLE_1_END + days(7))
    return fresh(subscription)
