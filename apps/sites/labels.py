"""Labels worked out from stored Site rows on each read. Nothing here is stored or written.

A label is for support, reporting and communications. It never authorizes anything: access to
a Santé feature is decided by `services.site_for(..., template=templates.SANTE)`.
"""
from django.conf import settings
from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q
from django.utils import timezone

from apps.accounts.models import Account
from apps.billing import services as billing
from apps.sites import templates
from apps.sites.models import Site

SANTE_SITE_OWNER = "Santé Site Owner"
# With billing on, the site's subscription must be in one of these states at this moment.
SUBSCRIPTION_STATES = ("trial_active", "active", "payment_requires_attention")


def _eligible_site():
    """The one place the rule lives: a Santé site that is not removed and not on operator hold
    and, where billing is on, whose subscription is on trial, active or needing payment
    attention at this moment."""
    rule = Q(template=templates.SANTE, deleted_at__isnull=True, operator_hold=False)
    if settings.BILLING_ENABLED:
        rule &= billing.state_filter(timezone.now(), SUBSCRIPTION_STATES)
    return rule


def eligible_sante_sites():
    """Santé sites that meet the rule above."""
    return Site.objects.filter(_eligible_site())


def with_sante_site_owner(accounts):
    """`accounts` with a true/false `sante_site_owner` column, worked out in the same query.

    True when the account is not closed and owns at least one eligible Santé site.
    """
    owns_one = Exists(eligible_sante_sites().filter(account=OuterRef("pk")))
    return accounts.annotate(
        sante_site_owner=ExpressionWrapper(
            Q(closed_at__isnull=True) & Q(owns_one), output_field=BooleanField()
        )
    )


def with_counts_for_sante_site_owner(sites):
    """`sites` with a true/false `counts_for_sante_site_owner` column, in the same query.

    True when the site is eligible and its account is not closed.
    """
    return sites.annotate(
        counts_for_sante_site_owner=ExpressionWrapper(
            _eligible_site() & Q(account__closed_at__isnull=True), output_field=BooleanField()
        )
    )


def labels_for(account):
    """The labels of one account, read fresh: `["Santé Site Owner"]` or `[]`."""
    owner = with_sante_site_owner(Account.objects.filter(pk=account.pk)).values_list(
        "sante_site_owner", flat=True
    ).first()
    return [SANTE_SITE_OWNER] if owner else []
