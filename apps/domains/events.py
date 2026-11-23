"""Writing `DomainEvent` rows (append-only) with an actor snapshot. `detail` is whitelisted by the
caller: names, codes, cents and counts, never a provider body or a token."""
from django.utils import timezone

from apps.domains.models import ActorType, DomainEvent


def log(site, kind, *, claim=None, registration=None, actor_type=ActorType.SYSTEM, actor_id=None,
           from_state="", to_state="", detail=None, now=None):
    return DomainEvent.objects.create(
        site=site, claim=claim, registration=registration, kind=kind, actor_type=actor_type,
        actor_id=actor_id, from_state=from_state, to_state=to_state, detail=detail or {},
        created_at=now or timezone.now(),
    )


# The only keys an email-track event may carry, each a short string, a number or a flag. A provider
# body, a record value, a key or an id never reaches `detail` through `clean`.
DETAIL_KEYS = frozenset({"reason", "code", "status", "region", "count", "types", "capacity", "occupied"})


def clean(detail):
    """`detail` reduced to the whitelisted keys and plain short values (design R4.14.7 (g))."""
    out = {}
    for key, value in (detail or {}).items():
        if key not in DETAIL_KEYS:
            continue
        if isinstance(value, bool) or isinstance(value, int):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:80]
        elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            out[key] = [v[:16] for v in value[:10]]
    return out
