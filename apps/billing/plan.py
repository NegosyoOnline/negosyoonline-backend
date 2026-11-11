"""The plan and the periods of the subscription lifecycle. No table holds these in B1."""
from datetime import timedelta

PLAN_NAME = "NegosyoOnline Monthly"
PRICE_CENTAVOS = 249_900  # PHP 2,499
CURRENCY = "PHP"
TRIAL_DAYS = 14

TRIAL = timedelta(days=TRIAL_DAYS)
GRACE = timedelta(days=7)  # L11: no payment outcome by the due date plus 7 days: suspended
DOWNLOAD_WINDOW = timedelta(days=30)
DELETION_AFTER_PAID = timedelta(days=90)
DELETION_NEVER_PAID = timedelta(days=30)  # L75


def collection_live():
    """Whether NegosyoOnline can take a payment. Billing Build B2 changes this answer.

    R-4a: the dates of a subscription that never paid start only once it can be paid for.
    """
    return False
