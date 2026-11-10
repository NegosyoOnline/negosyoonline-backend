"""What the dispose_account and export_account commands both check."""
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError

from apps.accounts import retention


def add_arguments(parser):
    parser.add_argument("account", help="the account's public id (acc_...) or its email")
    parser.add_argument(
        "--actor", required=True, help="the staff user running this command, for the audit record"
    )
    parser.add_argument(
        "--request-ref", required=True,
        help="the reference of the person's request; a number, never a name or an address",
    )


def staff_user(username):
    found = get_user_model().objects.filter(
        username=username, is_active=True, is_staff=True
    ).first()
    if found is None:
        raise CommandError("--actor must be an active staff user.")
    return found


def account_for(reference):
    account = retention.find_account(reference)
    if account is None:
        raise CommandError("No such account.")
    return account
