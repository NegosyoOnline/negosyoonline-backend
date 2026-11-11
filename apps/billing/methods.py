"""Payment methods, as the lifecycle needs to ask about them.

At launch the only way to pay is a bank transfer with proof (Billing Build B2a), so "a
payment method was active" means: a proof was waiting for review at that instant.
"""
from django.db.models import Q

from apps.billing import plan
from apps.billing.models import BankTransferPayment


def under_review_at(instant):
    """A condition on payments: sent by `instant` and not yet decided at it."""
    return Q(submitted_at__lte=instant) & (Q(decided_at__isnull=True) | Q(decided_at__gt=instant))


def active_at(site, instant):
    """Whether the site had a proof under review at that instant. Reads nothing while
    payment collection is off."""
    if not plan.collection_live():
        return False
    return BankTransferPayment.objects.filter(
        under_review_at(instant), subscription__site=site
    ).exists()


def reusable(site):
    """Whether the site has a payment method that could be charged again without the owner.

    Always false until a gateway exists (Billing Build B2): a bank transfer is a payment the
    owner sends each time, and nothing here authorises a debit.
    """
    return False
