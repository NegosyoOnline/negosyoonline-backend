"""Cursor pagination. The cursor is the public id of the last item already returned."""
from apps.common.errors import ApiError
from apps.common.ids import is_public_id

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def page(queryset, *, limit, cursor, prefix):
    """Return (items, next_cursor). `queryset` must already be filtered to the caller's rows."""
    limit = max(1, min(limit if limit is not None else DEFAULT_LIMIT, MAX_LIMIT))
    rows = queryset.order_by("pk")
    if cursor:
        last = rows.filter(public_id=cursor).first() if is_public_id(cursor, prefix) else None
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.")
        rows = rows.filter(pk__gt=last.pk)
    items = list(rows[: limit + 1])
    more = len(items) > limit
    items = items[:limit]
    return items, (items[-1].public_id if more else None)
