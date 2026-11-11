"""The plans and the periods of the subscription lifecycle. No table holds these."""
import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings

from apps.billing import proofs

logger = logging.getLogger(__name__)

PLAN_NAME = "NegosyoOnline Monthly"
PRICE_CENTAVOS = 249_900  # PHP 2,499
CURRENCY = "PHP"
TRIAL_DAYS = 14

TRIAL = timedelta(days=TRIAL_DAYS)
GRACE = timedelta(days=7)  # L11: no payment outcome by the due date plus 7 days: suspended
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


ACCOUNT_KINDS = ("bank", "ewallet")
ACCOUNT_FIELDS = ("kind", "provider_name", "account_name", "account_number")


def offers(subscription, now):
    """The plans this subscription can buy at `now`, in the order the page shows them.

    Monthly is always offered. The Launch Promo is offered while its switch is on, and only to
    a subscription that has never had a paid cycle, so it is bought at most once. Its end
    date is not named yet; none is in code.
    """
    offered = [MONTHLY]
    if promo_open(subscription):
        offered.insert(0, LAUNCH_PROMO)
    return offered


def promo_open(subscription):
    return bool(settings.BILLING_LAUNCH_PROMO_ENABLED) and never_paid(subscription)


def never_paid(subscription):
    return not subscription.cycles.filter(settlement="paid").exists()


def transfer_accounts():
    """The receiving accounts in BILLING_TRANSFER_ACCOUNTS. A value that is not a list of
    complete accounts counts as none, so a half-set variable never shows a broken account."""
    raw = settings.BILLING_TRANSFER_ACCOUNTS
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except ValueError:
        rows = None
    if not isinstance(rows, list) or not rows:
        return _bad_accounts()
    accounts = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(ACCOUNT_FIELDS):
            return _bad_accounts()
        if not all(isinstance(row[name], str) and row[name].strip() for name in ACCOUNT_FIELDS):
            return _bad_accounts()
        if row["kind"] not in ACCOUNT_KINDS:
            return _bad_accounts()
        accounts.append({name: row[name].strip() for name in ACCOUNT_FIELDS})
    return accounts


def _bad_accounts():
    logger.error("BILLING_TRANSFER_ACCOUNTS is not a list of complete accounts; treated as empty.")
    return []


def collection_live():
    """Whether NegosyoOnline can take a payment: bank transfer is switched on, there is an
    account to pay to and a store for the proof.

    R-4a: the dates of a subscription that never paid start only once it can be paid for.
    """
    return bool(
        settings.BILLING_ENABLED
        and settings.BILLING_BANK_TRANSFER_ENABLED
        and transfer_accounts()
        and proofs.available()
    )
