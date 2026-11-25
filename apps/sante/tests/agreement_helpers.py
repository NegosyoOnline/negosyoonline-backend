"""Shared set-up for the Membership Agreement tests."""
from datetime import date

from django.contrib.auth import get_user_model

from apps.billing.tests.base import make_live_subscription
from apps.sante import agreement, services

PDF = b"%PDF-1.4\n" + b"sample agreement\n" * 20 + b"%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40
DAY = date(2026, 11, 15)
LETTER_DAY = date(2026, 10, 31)
REASON = "Authorized by Coach Ed, filed in ClickUp z8uq9me7x7"


def make_operator(username="op", **fields):
    return get_user_model().objects.create_user(
        username, f"{username}@example.test", "pw", is_staff=True, **fields
    )


def make_master(operator, email="master@example.test", name="master-store"):
    """A Santé site named the catalog owner; returns it."""
    site = make_live_subscription(email, name).site
    services.set_catalog_owner(site, operator=operator)
    return site


def draft(operator, label="v1", title="Santé Membership Agreement", content=PDF, effective=DAY):
    return agreement.create_draft(
        operator, title=title, version_label=label, effective_date=effective, content=content
    )


def published(operator, label="v1", **changes):
    version = draft(operator, label, **changes)
    agreement.publish(operator, label, reason=REASON)
    version.refresh_from_db()
    return version
