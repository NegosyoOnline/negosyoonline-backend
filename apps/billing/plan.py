"""The plans and the periods of the subscription lifecycle. No table holds these."""
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings

from apps.billing import proofs

PLAN_NAME = "NegosyoOnline Monthly"
PRICE_CENTAVOS = 249_900  # PHP 2,499
CURRENCY = "PHP"
TRIAL_DAYS = 14

TRIAL = timedelta(days=TRIAL_DAYS)
GRACE = timedelta(days=7)  # L11: no payment outcome by the due date plus 7 days: suspended
# A multi-month term can be paid for again only this close to its end (Coach Ed, 2026-11-11).
RENEWAL_WINDOW = timedelta(days=30)
DOWNLOAD_WINDOW = timedelta(days=30)
DELETION_AFTER_PAID = timedelta(days=90)
DELETION_NEVER_PAID = timedelta(days=30)  # L75
# The id of the wording a buyer of the Launch Promo accepts. The wording is the frontend's;
# the backend records only which version was shown (Billing Build B3, question 4).
CONSENT_VERSION = "promo-consent-1"


@dataclass(frozen=True)
class Plan:
    """What one payment buys: one term of `months` months for `price_centavos`.

    A plan is a record of one version. A price change is a new version; nothing edits an old
    one, and a cycle keeps the version it was opened under (Billing Build B3).
    """

    code: str
    version: int
    name: str
    price_centavos: int
    months: int  # the length of the term one payment buys
    allowance_periods: int  # monthly allowance periods inside the term
    renews_to: str  # the plan code that follows the term
    needs_consent: bool = False  # the buyer must accept what follows (the Launch Promo)


MONTHLY = Plan("monthly", 1, PLAN_NAME, PRICE_CENTAVOS, 1, 1, "monthly")
# PHP 24,990.00 paid in advance: ten monthly payments, two months free (saving PHP 4,998.00).
ANNUAL = Plan("annual", 1, "NegosyoOnline Annual", 2_499_000, 12, 12, "annual")
# The Launch Promo: PHP 4,999.00 once for six months. It buys one term and has no renewal of
# its own: what follows is Monthly. One allowance period for each of its six months.
LAUNCH_PROMO = Plan(
    "launch_promo", 1, "NegosyoOnline Launch Promo (6 Months)", 499_900, 6, 6, "monthly", True
)
# Every version ever sold, which is what a stored row is read against. Add a new version
# here and in PLANS; never change a row of this table.
VERSIONS = {(p.code, p.version): p for p in (MONTHLY, ANNUAL, LAUNCH_PROMO)}
# The version of each plan that is sold now.
PLANS = {p.code: p for p in (MONTHLY, ANNUAL, LAUNCH_PROMO)}


def version_of(code, version):
    """The plan a stored row was bought under. An unknown code or version is an error: it is
    never read as the current plan."""
    return VERSIONS[(code, version)]


def following(cycle):
    """The plan whose payment is due after `cycle`: the current version of what its plan
    renews to. A cycle with no snapshot (the trial) is followed by Monthly."""
    if not cycle.plan_code:
        return MONTHLY
    return PLANS[version_of(cycle.plan_code, cycle.plan_version).renews_to]


def snapshot(bought):
    """The fields of a cycle opened under `bought`."""
    return {
        "plan_code": bought.code,
        "plan_version": bought.version,
        "price_centavos": bought.price_centavos,
        "term_months": bought.months,
    }



def offers(subscription, now):
    """The plans this subscription can buy at `now`, in the order the page shows them.

    Monthly is always offered, Annual after it while its switch works. The Launch Promo is offered while its switch is on, and only to
    a subscription that has never had a paid cycle, so it is bought at most once. Its end
    date is not named yet; none is in code.
    """
    offered = [MONTHLY]
    if promo_open(subscription):
        offered.insert(0, LAUNCH_PROMO)
    if annual_open():
        offered.append(ANNUAL)
    return offered


def annual_open():
    """Whether Annual is offered: its switch is on and a payment can be taken."""
    return bool(settings.BILLING_ANNUAL_ENABLED) and collection_live()


def promo_open(subscription):
    return bool(settings.BILLING_LAUNCH_PROMO_ENABLED) and never_paid(subscription)


def never_paid(subscription):
    return not subscription.cycles.filter(settlement="paid").exists()


def transfer_accounts():
    """The active receiving accounts, in the order the page shows them, as the four fields a
    Subscriber is told. Staff set them in HQ Finance; none is in code or in a variable."""
    from apps.billing.models import ReceivingAccount

    rows = ReceivingAccount.objects.filter(active=True).order_by("sort_order", "pk")
    return [
        {
            "kind": row.kind, "provider_name": row.provider_name,
            "account_name": row.account_name, "account_number": row.account_number,
        }
        for row in rows
    ]


def has_receiving_account():
    from apps.billing.models import ReceivingAccount

    return ReceivingAccount.objects.filter(active=True).exists()


def collection_live():
    """Whether NegosyoOnline can take a payment: bank transfer is switched on, there is a store
    for the proof and an active account to pay to.

    The table is read last: a switch that is off, or no store, reads nothing. Never cached, so
    an account staff just switched off stops being offered at once.

    R-4a: the dates of a subscription that never paid start only once it can be paid for.
    """
    return bool(
        settings.BILLING_ENABLED
        and settings.BILLING_BANK_TRANSFER_ENABLED
        and proofs.available()
        and has_receiving_account()
    )


# Storage usage (Billing Build B4). Orders are counted for display against the 500 included in
# the plan (the Plan usage meters, `meters.py`), and nothing is charged for them: a Subscriber's
# sales are their own business (Coach Ed, 2026-11-11).
STORAGE_INCLUDED_GB = 5  # per monthly allowance period
STORAGE_OVERAGE_CENTAVOS_PER_GB = 3_000  # PHP 30 for each started GB over the 5 GB
GB_BYTES = 1_000_000_000  # a decimal gigabyte (Coach Ed, 2026-11-13)
STORAGE_LIMIT_BYTES = STORAGE_INCLUDED_GB * GB_BYTES  # the storage meter's limit: 5,000,000,000
LATE_ADMISSION = timedelta(hours=72)  # L2: a snapshot is accepted up to 72 hours after cutoff
# L13: platform renditions and the 5 GB allowance are decided with the media upload task
# before storage is metered. Until that task changes this, no snapshot is charged.
STORAGE_METERED = False
USAGE_ONLY = "usage_only"  # a payment of overage alone: no cycle, no base fee


def usage_switch_on():
    """The operator's switch alone: BILLING_ENABLED and BILLING_USAGE_ENABLED."""
    return bool(settings.BILLING_ENABLED and settings.BILLING_USAGE_ENABLED)


def usage_enabled():
    """Usage is billed or shown only while its switch is on AND storage is metered, so the
    switch alone can never put a usage amount in front of a Subscriber (Coach Ed, 2026-11-13:
    no extra charge at launch; usage is turned on later)."""
    return usage_switch_on() and bool(STORAGE_METERED)
