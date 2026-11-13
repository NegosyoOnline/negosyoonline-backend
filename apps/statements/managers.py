"""Querysets that refuse bulk changes to billing records that are never changed or deleted
(Billing Build B5, plan section 5.4, layer 2).

The PostgreSQL triggers of migration statements.0002 are the wall behind these; these stop the
same changes through Django on every database, with a clearer error.
"""
import contextvars
import logging
from contextlib import contextmanager

from django.db import models, transaction

logger = logging.getLogger(__name__)

# The staff user whose request is running, set by the Finance routes (see `acting_operator`).
_ACTOR = contextvars.ContextVar("billing_refusal_actor", default=None)

MAX_AUDITED_IDS = 50


class Immutable(RuntimeError):
    """A change a billing record never allows. Every guard of the statements and invoices
    apps raises it."""


@contextmanager
def acting_operator(operator):
    """Names the staff user for any refusal below, so the audit row of a refused edit or delete
    started from a staff route carries who tried it. Outside a route there is none."""
    token = _ACTOR.set(operator)
    try:
        yield
    finally:
        _ACTOR.reset(token)


def _target_ids(model, instances=None, queryset=None):
    path = model.REFUSAL_ID_PATH
    if queryset is not None:
        return [str(v) for v in queryset.values_list(path, flat=True)[:MAX_AUDITED_IDS]]
    ids = []
    for instance in instances:
        value = instance
        for part in path.split("__"):
            value = getattr(value, part)
        ids.append(str(value))
    return ids[:MAX_AUDITED_IDS]


def audit_refusal(kind, model, *, instances=None, queryset=None, detail=None):
    """Writes one operator audit row for a refused edit (`kind="edit"`) or delete of a billing
    record that opts in with `REFUSAL_TARGET_TYPE` and `REFUSAL_ID_PATH`. It holds the actor
    when a staff route set one, the target's public id (or the first few ids of a bulk change
    and their count), the action and the time (the row's own), plus the names of fields: never
    a value. It can never weaken the guard: the caller raises `Immutable` whatever happens
    here, and a failure is logged without detail.

    The row is written in the caller's transaction. If the caller then lets the refusal roll
    that transaction back, the row goes with it; a refusal the caller catches keeps it."""
    target_type = getattr(model, "REFUSAL_TARGET_TYPE", None)
    if not target_type:
        return
    from apps.operators.audit import record as _record

    try:
        with transaction.atomic():
            ids = _target_ids(model, instances, queryset)
            detail = {**(detail or {}), "count": len(ids)}
            if len(ids) > 1:
                detail["ids"] = ids
            target_id = ids[0] if len(ids) == 1 else ""
            if kind == "delete":
                _record(
                    _ACTOR.get(), "billing_record_delete_refused", target_type=target_type,
                    target_id=target_id, detail=detail,
                )
            else:
                _record(
                    _ACTOR.get(), "billing_record_edit_refused", target_type=target_type,
                    target_id=target_id, detail=detail,
                )
    except Exception:  # noqa: BLE001 - the refusal must stand whatever the audit does
        logger.warning("refusal of a %s on %s could not be audited", kind, target_type)


class KeptQuerySet(models.QuerySet):
    """Rows that are never deleted (L76). Their allowed changes go through the model."""

    def delete(self):
        audit_refusal("delete", self.model, queryset=self)
        raise Immutable("A billing record is kept and cannot be deleted.")


class AppendOnlyQuerySet(KeptQuerySet):
    """Rows that are only ever added: a statement, its lines, an invoice event."""

    def update(self, **kwargs):
        audit_refusal("edit", self.model, queryset=self)
        raise Immutable("A billing record is final and cannot be changed.")

    def bulk_update(self, objs, fields, batch_size=None):
        audit_refusal("edit", self.model, instances=list(objs))
        raise Immutable("A billing record is final and cannot be changed.")

    def bulk_create(self, objs, *args, **kwargs):
        if kwargs.get("update_conflicts"):
            audit_refusal("edit", self.model, instances=list(objs))
            raise Immutable("A billing record is final and cannot be changed.")
        return super().bulk_create(objs, *args, **kwargs)


class GuardedQuerySet(KeptQuerySet):
    """Rows with a few fields that move along a workflow. A bulk update would skip the model's
    check of which, so it is refused: change them one by one with `save()`."""

    def update(self, **kwargs):
        audit_refusal("edit", self.model, queryset=self)
        raise Immutable("Change this record with save(), which checks what may change.")

    def bulk_update(self, objs, fields, batch_size=None):
        audit_refusal("edit", self.model, instances=list(objs))
        raise Immutable("Change this record with save(), which checks what may change.")

    def bulk_create(self, objs, *args, **kwargs):
        if kwargs.get("update_conflicts"):
            audit_refusal("edit", self.model, instances=list(objs))
            raise Immutable("Change this record with save(), which checks what may change.")
        return super().bulk_create(objs, *args, **kwargs)


class FrozenFieldsMixin:
    """`save()` of an existing row may change only the fields in `MOVES`, and only along the
    rule `allowed(old, new)` gives. Everything else is frozen at the first save."""

    MOVES = ()

    def allowed(self, old):  # pragma: no cover - overridden where a workflow exists
        return True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self)._base_manager.get(pk=self.pk)
            for field in self._meta.concrete_fields:
                if field.name in self.MOVES:
                    continue
                if getattr(old, field.attname) != getattr(self, field.attname):
                    audit_refusal("edit", type(self), instances=[self], detail={"field": field.name})
                    raise Immutable(f"A billing record cannot change its {field.name}.")
            if not self.allowed(old):
                audit_refusal("edit", type(self), instances=[self], detail={"field": "workflow"})
                raise Immutable("That change is not allowed on a billing record.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        audit_refusal("delete", type(self), instances=[self])
        raise Immutable("A billing record is kept and cannot be deleted.")


class AppendOnlyMixin:
    """A row that is inserted once and never changed or deleted."""

    def save(self, *args, **kwargs):
        if not self._state.adding:
            audit_refusal("edit", type(self), instances=[self])
            raise Immutable("A billing record is final and cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        audit_refusal("delete", type(self), instances=[self])
        raise Immutable("A billing record is kept and cannot be deleted.")
