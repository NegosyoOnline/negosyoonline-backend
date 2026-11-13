"""The receiving accounts: where Subscribers are told to send a bank transfer.

Staff set them in HQ Finance (`apps.billing.staff`). Every write locks every row, in pk order
(the table holds a handful of rows), and writes its audit entry in the same transaction, so an
account cannot change without a record of the values before and after. Nothing here deletes.
Nothing outside staff reads or writes these rows except `plan.transfer_accounts()`.
"""
import re

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.billing import proofs
from apps.billing.models import ReceivingAccount
from apps.common.errors import ApiError
from apps.operators import audit

LIMITS = {"provider_name": 60, "account_name": 120}
NUMBER = re.compile(r"^[0-9 -]{6,40}$")
_TEXT = ("provider_name", "account_name", "account_number")


def _invalid(field, message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={field: [message]}
    )


def _kind(value):
    if value not in ReceivingAccount.Kind.values:
        raise _invalid("kind", "Choose bank or ewallet.")
    return value


def _text(value, field):
    if not isinstance(value, str):
        raise _invalid(field, "Send text.")
    value = value.strip()
    if not value:
        raise _invalid(field, "This is required.")
    if field == "account_number":
        if not NUMBER.match(value):
            raise _invalid(field, "Use 6 to 40 digits, spaces or dashes.")
    elif len(value) > LIMITS[field]:
        raise _invalid(field, f"At most {LIMITS[field]} characters.")
    return value


def _sort_order(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999:
        raise _invalid("sort_order", "Send a whole number from 0 to 999.")
    return value


def _active(value):
    if not isinstance(value, bool):
        raise _invalid("active", "Send true or false.")
    return value


def _snapshot(account):
    return {
        "kind": account.kind, "provider_name": account.provider_name,
        "account_name": account.account_name, "account_number": account.account_number,
        "sort_order": account.sort_order, "active": account.active,
    }


def as_dict(account):
    """An account as staff see it. The full number: staff only."""
    return {
        "id": account.public_id,
        **_snapshot(account),
        "created_at": account.created_at.isoformat(),
        "updated_at": account.updated_at.isoformat(),
        "updated_by": account.updated_by.username,
    }


def _lock_all():
    return list(ReceivingAccount.objects.select_for_update().order_by("pk"))


def _duplicate():
    return ApiError(
        "account_exists",
        "That account is already listed. Edit or reactivate that account instead.",
        status=409,
    )


def _named(body, rows):
    public_id = body.get("account")
    if not isinstance(public_id, str) or not public_id:
        raise _invalid("account", "Name the account.")
    for row in rows:
        if row.public_id == public_id:
            return row
    raise ApiError("not_found", "Not found.", status=404)


def status():
    """Whether Subscribers can pay by bank transfer now, and if not, why."""
    switch_on = bool(settings.BILLING_BANK_TRANSFER_ENABLED)
    store = bool(proofs.available())
    active = ReceivingAccount.objects.filter(active=True).count()
    return {
        "switch_on": switch_on, "store_available": store, "active_accounts": active,
        "live": bool(settings.BILLING_ENABLED and switch_on and store and active),
    }


def listing():
    rows = ReceivingAccount.objects.select_related("updated_by").order_by("sort_order", "pk")
    return {"items": [as_dict(row) for row in rows], "bank_transfer": status()}


def add(operator, body):
    values = {
        "kind": _kind(body.get("kind")),
        **{name: _text(body.get(name), name) for name in _TEXT},
        "sort_order": _sort_order(body.get("sort_order", 0)),
        "active": _active(body.get("active", True)),
    }
    with transaction.atomic():
        rows = _lock_all()
        if any(
            row.provider_name == values["provider_name"]
            and row.account_number == values["account_number"] for row in rows
        ):
            raise _duplicate()
        try:
            with transaction.atomic():
                account = ReceivingAccount.objects.create(
                    created_by=operator, updated_by=operator, **values
                )
        except IntegrityError:
            raise _duplicate() from None
        audit.record(
            operator, "billing_receiving_account_added", target_type="receiving_account",
            target_id=account.public_id, detail={"after": _snapshot(account)},
        )
    return account


def edit(operator, body):
    changes = {}
    if "kind" in body:
        changes["kind"] = _kind(body["kind"])
    for name in _TEXT:
        if name in body:
            changes[name] = _text(body[name], name)
    if "sort_order" in body:
        changes["sort_order"] = _sort_order(body["sort_order"])
    with transaction.atomic():
        rows = _lock_all()
        account = _named(body, rows)
        before = _snapshot(account)
        changes = {name: value for name, value in changes.items() if before[name] != value}
        if not changes:
            return account
        after = {**before, **changes}
        if any(
            row.pk != account.pk and row.provider_name == after["provider_name"]
            and row.account_number == after["account_number"] for row in rows
        ):
            raise _duplicate()
        for name, value in changes.items():
            setattr(account, name, value)
        account.updated_by = operator
        try:
            with transaction.atomic():
                account.save()
        except IntegrityError:
            raise _duplicate() from None
        audit.record(
            operator, "billing_receiving_account_edited", target_type="receiving_account",
            target_id=account.public_id, detail={"before": before, "after": _snapshot(account)},
        )
    return account


def set_active(operator, body):
    active = _active(body.get("active"))
    with transaction.atomic():
        rows = _lock_all()
        account = _named(body, rows)
        if account.active == active:
            return account
        if (
            not active and settings.BILLING_BANK_TRANSFER_ENABLED
            and not any(row.active for row in rows if row.pk != account.pk)
        ):
            # With no active account every Finance payment route answers 404, so a proof
            # waiting for review could be neither confirmed nor rejected.
            raise ApiError(
                "last_active_account",
                "Add the new account first, then switch this one off.",
                status=409,
            )
        before = _snapshot(account)
        account.active = active
        account.updated_by = operator
        account.save()
        detail = {"before": before, "after": _snapshot(account)}
        if active:
            audit.record(
                operator, "billing_receiving_account_activated",
                target_type="receiving_account", target_id=account.public_id, detail=detail,
            )
        else:
            audit.record(
                operator, "billing_receiving_account_deactivated",
                target_type="receiving_account", target_id=account.public_id, detail=detail,
            )
    return account
