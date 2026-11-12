"""Querysets that refuse bulk changes to billing records that are never changed or deleted
(Billing Build B5, plan section 5.4, layer 2).

The PostgreSQL triggers of migration statements.0002 are the wall behind these; these stop the
same changes through Django on every database, with a clearer error.
"""
from django.db import models


class Immutable(RuntimeError):
    """A change a billing record never allows. Every guard of the statements and invoices
    apps raises it."""


class KeptQuerySet(models.QuerySet):
    """Rows that are never deleted (L76). Their allowed changes go through the model."""

    def delete(self):
        raise Immutable("A billing record is kept and cannot be deleted.")


class AppendOnlyQuerySet(KeptQuerySet):
    """Rows that are only ever added: a statement, its lines, an invoice event."""

    def update(self, **kwargs):
        raise Immutable("A billing record is final and cannot be changed.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise Immutable("A billing record is final and cannot be changed.")

    def bulk_create(self, objs, *args, **kwargs):
        if kwargs.get("update_conflicts"):
            raise Immutable("A billing record is final and cannot be changed.")
        return super().bulk_create(objs, *args, **kwargs)


class GuardedQuerySet(KeptQuerySet):
    """Rows with a few fields that move along a workflow. A bulk update would skip the model's
    check of which, so it is refused: change them one by one with `save()`."""

    def update(self, **kwargs):
        raise Immutable("Change this record with save(), which checks what may change.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise Immutable("Change this record with save(), which checks what may change.")

    def bulk_create(self, objs, *args, **kwargs):
        if kwargs.get("update_conflicts"):
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
                    raise Immutable(f"A billing record cannot change its {field.name}.")
            if not self.allowed(old):
                raise Immutable("That change is not allowed on a billing record.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise Immutable("A billing record is kept and cannot be deleted.")


class AppendOnlyMixin:
    """A row that is inserted once and never changed or deleted."""

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise Immutable("A billing record is final and cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise Immutable("A billing record is kept and cannot be deleted.")
