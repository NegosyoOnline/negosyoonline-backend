"""Querysets that refuse bulk changes to rows the books never change.

The database triggers (migration 0002) are the real wall on PostgreSQL; these stop the same
changes through Django on every database, with a clearer error.
"""
from django.db import models


class LedgerRefusal(Exception):
    """A change the books never allow. Every guard in this app raises it."""


class ProtectedQuerySet(models.QuerySet):
    """Rows that are never deleted: a book, an account, a period."""

    def delete(self):
        raise LedgerRefusal("ledger rows are never deleted")


class AppendOnlyQuerySet(models.QuerySet):
    """Rows that are only ever added: the change history, the recognition schedule."""

    def update(self, **kwargs):
        raise LedgerRefusal("ledger rows are append-only")

    def bulk_update(self, objs, fields, batch_size=None):
        raise LedgerRefusal("ledger rows are append-only")

    def delete(self):
        raise LedgerRefusal("ledger rows are append-only")


class JournalEntryQuerySet(models.QuerySet):
    def _refuse_posted(self):
        if self.filter(status="posted").exists():
            raise LedgerRefusal("a posted entry cannot be changed")

    def update(self, **kwargs):
        self._refuse_posted()
        return super().update(**kwargs)

    def delete(self):
        self._refuse_posted()
        return super().delete()

    def bulk_update(self, objs, fields, batch_size=None):
        pks = [o.pk for o in objs if o.pk is not None]
        if self.model._default_manager.filter(pk__in=pks, status="posted").exists():
            raise LedgerRefusal("a posted entry cannot be changed")
        return super().bulk_update(objs, fields, batch_size)

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        if any(o.status != "draft" for o in objs):
            raise LedgerRefusal("an entry is created as a draft")
        return super().bulk_create(objs, *args, **kwargs)


class JournalLineQuerySet(models.QuerySet):
    def _refuse_posted(self):
        if self.filter(entry__status="posted").exists():
            raise LedgerRefusal("the lines of a posted entry cannot be changed")

    def update(self, **kwargs):
        self._refuse_posted()
        return super().update(**kwargs)

    def delete(self):
        self._refuse_posted()
        return super().delete()

    def bulk_update(self, objs, fields, batch_size=None):
        pks = [o.pk for o in objs if o.pk is not None]
        if self.model._default_manager.filter(pk__in=pks, entry__status="posted").exists():
            raise LedgerRefusal("the lines of a posted entry cannot be changed")
        return super().bulk_update(objs, fields, batch_size)

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        entry_ids = {o.entry_id for o in objs}
        entry = self.model._meta.get_field("entry").related_model
        if entry._default_manager.filter(pk__in=entry_ids).exclude(status="draft").exists():
            raise LedgerRefusal("a line cannot be added to a posted entry")
        return super().bulk_create(objs, *args, **kwargs)
