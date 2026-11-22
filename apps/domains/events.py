"""Writing `DomainEvent` rows (append-only) with an actor snapshot. `detail` is whitelisted by the
caller: names, codes, cents and counts, never a provider body or a token."""
from django.utils import timezone

from apps.domains.models import ActorType, DomainEvent


def record(site, kind, *, claim=None, registration=None, actor_type=ActorType.SYSTEM, actor_id=None,
           from_state="", to_state="", detail=None, now=None):
    return DomainEvent.objects.create(
        site=site, claim=claim, registration=registration, kind=kind, actor_type=actor_type,
        actor_id=actor_id, from_state=from_state, to_state=to_state, detail=detail or {},
        created_at=now or timezone.now(),
    )
