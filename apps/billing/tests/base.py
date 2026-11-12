"""Helpers shared by the billing tests. Time is always passed in; no test reads the clock."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

from django.test import override_settings

from apps.accounts.models import Account
from apps.billing import clock, plan, proofs, services
from apps.billing.models import Subscription
from apps.common import private_files
from apps.operators.models import User
from apps.sites.models import Site
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
    # The site's own creation time is part of the dates of a never-paid site (B2a, question 9).
    Site.objects.filter(pk=site.pk).update(created_at=now or start)
    site.refresh_from_db()
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


# Bank transfer (Billing Build B2a)

JPEG = b"\xff\xd8\xff\xe0" + b"proof-one" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"proof-two" * 20
PDF = b"%PDF-1.7\n" + b"proof-three" * 20
TEST_ACCOUNTS = json.dumps([
    {"kind": "bank", "provider_name": "Test Bank", "account_name": "Test Receiver",
     "account_number": "0000 0000 00"},
])


class MemoryProofStore:
    def __init__(self):
        self.files = {}

    def save(self, content, content_type):
        key = private_files.new_key(proofs.FOLDER, content_type)
        self.files[key] = bytes(content)
        return key

    def read(self, key):
        return self.files[key]

    def discard(self, key):
        self.files.pop(key, None)


@contextmanager
def transfers_on():
    """Bank transfer switched on, with a made-up receiving account and a store in memory."""
    store = MemoryProofStore()
    with override_settings(
        BILLING_BANK_TRANSFER_ENABLED=True, BILLING_TRANSFER_ACCOUNTS=TEST_ACCOUNTS
    ), mock.patch("apps.billing.proofs.save", side_effect=store.save), mock.patch(
        "apps.billing.proofs.read", side_effect=store.read
    ), mock.patch("apps.billing.proofs.discard", side_effect=store.discard), mock.patch(
        "apps.billing.proofs.available", return_value=True
    ):
        yield store


def make_operator(username="op"):
    return User.objects.create(username=username, is_staff=True)


# The sender's details every test proof carries unless a test names its own.
SENDER = dict(
    transfer_reference="BANK-REF-1", sender_bank="Test Bank", sender_first_name="Juanito",
    sender_last_name="Bautista", sender_account_number="1234 5678 90", amount_sent="999.00",
)


def submit(subscription, at, content=JPEG, plan_code="monthly", **fields):
    """The owner sends a proof at `at`. Returns the payment."""
    if plan_code == "launch_promo":  # the buyer accepts what follows, as the page asks
        fields.setdefault("renewal_consent", True)
        fields.setdefault("consent_version", plan.CONSENT_VERSION)
    fields.setdefault("transferred_at", at)
    for name, value in SENDER.items():
        fields.setdefault(name, value)
    payment, _ = services.submit_transfer(
        subscription, subscription.site.account, plan_code=plan_code, content=content,
        now=at, **fields,
    )
    return payment
