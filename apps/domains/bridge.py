"""The bridge import (ClickUp z8uq9mhpbb, plan FREE-DOMAIN-Y1 decision FD3): an HQ staff action that
brings a domain Coach Ed bought by hand into the system for ONE site, on the "confirmed registered"
pattern (purchase.process_confirm).

It never calls `register` and spends nothing. It has two halves, like "confirmed registered":

* the api half (`request_import`, a staff route): validates, then writes the name onto the site's
  registration row (state `intent`, unsent, so nothing can buy it: no claim exists) and an
  `import_requested` event. No provider call (the api holds no Registrar token).
* the worker half (`process_import`): under the purchase lock, re-checks every rule, then reads the
  registrar. The registrar's get-registration must show the name in OUR account and its status must
  not be in progress, failed, action required or blocked, and the answer must carry `expires_at`.
  Only then is the registration recorded `succeeded` (with `expires_at`, `auto_renew`) and a claim
  started at `registered`: the normal machine does DNS, Vercel and Resend from there, behind the
  provider switches as they are. Any refused or ambiguous read gives the name back (the row returns
  to an empty `intent`) and records the reason.

The registration row of an import carries `register_sent_at` = the import time and `send_count` = 1
only because the table's own consistency rule requires them on a `succeeded` row; no `register_sent`
event is written, so no purchase cap counts it, and send_count 1 means the row can never send.
Counts as the site's one domain for life (the registration row is one per site and never deleted).
Refused for a billing-exempt (or deleted, non-Santé) site, a site with a claim or a registration
name, and a name another site holds.
"""
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import Subscription
from apps.common.errors import ApiError
from apps.domains import events, machine, names, purchase
from apps.domains.models import (
    ActorType, ClaimState, Domain, DomainEvent, DomainRegistration, RegistrationState,
)
from apps.domains.providers.base import Paused, ProviderError
from apps.sites import templates

IMPORT_KINDS = ("import_requested", "bridge_imported", "import_refused")


def _refused(reason, message, status=409):
    return ApiError("import_refused", message, status=status, fields={"reason": [reason]})


def last_import(site):
    return DomainEvent.objects.filter(site=site, kind__in=IMPORT_KINDS).order_by("-pk").first()


def import_pending(site):
    last = last_import(site)
    return last is not None and last.kind == "import_requested"


def site_refusal(site):
    """A short code when the site can never take an imported domain, else "". Reads only."""
    if site.deleted_at is not None or site.template != templates.SANTE:
        return "site_off"
    subscription = Subscription.objects.filter(site=site).first()
    if subscription is None:
        return "no_subscription"
    if subscription.billing_exempt:
        return "exempt"
    return ""


_MESSAGES = {
    "site_off": "This site cannot take a domain.",
    "no_subscription": "This site has no subscription.",
    "exempt": "This site is billing-exempt and has no free domain.",
}


def request_import(operator, site, raw_name, *, now=None):
    """The staff route's half. Returns "requested" or "pending". Writes rows, calls no provider."""
    now = now or timezone.now()
    try:
        name = names.normalise(raw_name)
    except names.NameRefused as error:
        raise ApiError("invalid_request", "Some values are missing or invalid.", fields={"name": [str(error)]}) from None
    reason = names.refusal(name)
    if reason:
        raise _refused("name_refused", reason)
    code = site_refusal(site)
    if code:
        raise _refused(code, _MESSAGES[code])
    purchase.ledger()
    with transaction.atomic():
        reg, _ = lock_site_rows(site)
        if reg.name is not None:
            if reg.state == RegistrationState.INTENT and reg.name == name and import_pending(site):
                return "pending"
            raise _refused("has_registration", "This site already has a domain for life.")
        if Domain.objects.filter(site=site).exclude(state=ClaimState.ENDED).exists():
            raise _refused("has_claim", "This site already has a domain claim.")
        if DomainRegistration.objects.filter(name=name).exclude(site=site).exists():
            raise _refused("name_held", "Another site holds that name.")
        reg.name = name
        purchase.clear_check(reg)
        try:
            with transaction.atomic():
                reg.save()
        except IntegrityError:
            raise _refused("name_held", "Another site holds that name.") from None
        events.log(site, "import_requested", registration=reg, actor_type=ActorType.OPERATOR,
                   actor_id=operator.pk, detail={"name": name}, now=now)
    return "requested"


def lock_site_rows(site):
    """Inside a transaction: the ledger, then the site's registration row (created when missing)."""
    from apps.domains.models import DomainPurchaseLedger  # noqa: PLC0415

    DomainPurchaseLedger.objects.select_for_update().get(pk=1)
    reg, _ = DomainRegistration.objects.get_or_create(site=site)
    return DomainRegistration.objects.select_for_update().get(pk=reg.pk), site


def _refuse(site, reg_id, operator_id, name, reason, now):
    """Give the name back (only an unsent `intent` row of this very name) and record why."""
    purchase.ledger()
    with transaction.atomic():
        reg, _ = purchase.lock_rows(reg_id)
        if (
            reg.state == RegistrationState.INTENT and reg.register_sent_at is None and reg.send_count == 0
            and reg.name == name
        ):
            reg.name = None
            purchase.clear_check(reg)
            reg.save()
        events.log(site, "import_refused", registration=reg, actor_type=ActorType.OPERATOR,
                   actor_id=operator_id, detail={"name": name, "reason": reason}, now=now)
    return reason


def process_import(site_id, providers, *, now=None):
    """The worker's half. Returns "done", "none" (nothing pending) or the refusal reason."""
    from apps.sites.models import Site  # noqa: PLC0415

    now = now or timezone.now()
    site = Site.objects.filter(pk=site_id).first()
    if site is None:
        return "none"
    last = last_import(site)
    if last is None or last.kind != "import_requested":
        return "none"
    operator_id, name = last.actor_id, (last.detail or {}).get("name")
    reg = DomainRegistration.objects.filter(site=site).first()
    if reg is None:
        return "none"
    if (
        not name or reg.name != name or reg.state != RegistrationState.INTENT
        or reg.register_sent_at is not None or reg.send_count != 0
    ):
        return _refuse(site, reg.pk, operator_id, name, "not_pending", now)
    code = site_refusal(site)
    if code:
        return _refuse(site, reg.pk, operator_id, name, code, now)
    if Domain.objects.filter(site=site).exclude(state=ClaimState.ENDED).exists():
        return _refuse(site, reg.pk, operator_id, name, "has_claim", now)
    if settings.DOMAINS_PROVIDER_PAUSED:
        return _refuse(site, reg.pk, operator_id, name, "paused", now)
    if providers is None or getattr(providers, "registrar", None) is None:
        return _refuse(site, reg.pk, operator_id, name, "provider_unavailable", now)
    try:
        found = providers.registrar.get(name)
    except Paused:
        return _refuse(site, reg.pk, operator_id, name, "paused", now)
    except ProviderError as error:
        return _refuse(site, reg.pk, operator_id, name, "read_failed_" + error.code, now)
    if not found or found["name"] != name:
        return _refuse(site, reg.pk, operator_id, name, "not_in_our_account", now)
    if found.get("expires_at") is None:
        return _refuse(site, reg.pk, operator_id, name, "no_expiry", now)
    try:
        status = providers.registrar.status(name)
    except Paused:
        return _refuse(site, reg.pk, operator_id, name, "paused", now)
    except ProviderError as error:
        if error.code != "not_found":
            return _refuse(site, reg.pk, operator_id, name, "status_failed_" + error.code, now)
        status = {"state": "unknown"}
    if status["state"] in ("in_progress",) + purchase.BAD_STATUSES:
        return _refuse(site, reg.pk, operator_id, name, "status_" + status["state"], now)
    purchase.ledger()
    with transaction.atomic():
        locked, _ = lock_site_rows(site)
        if (
            locked.state != RegistrationState.INTENT or locked.name != name or locked.send_count != 0
            or locked.register_sent_at is not None or not import_pending(site)
        ):
            return "none"
        if site_refusal(site) or Domain.objects.filter(site=site).exclude(state=ClaimState.ENDED).exists():
            return _refuse(site, locked.pk, operator_id, name, "not_allowed", now)
        locked.state, locked.completed_at = RegistrationState.SUCCEEDED, now
        locked.register_sent_at, locked.send_count = now, 1  # the table's rule for `succeeded`; no send happened
        locked.provider_status, locked.provider_code = "succeeded", ""
        locked.expires_at, locked.auto_renew, locked.auto_renew_checked_at = (
            found["expires_at"], found["auto_renew"], now,
        )
        locked.save()
        claim = Domain.objects.create(site=site, name=name, state=ClaimState.REGISTERED)
        machine.record_event(claim, "state", to_state=ClaimState.REGISTERED, detail={"reason": "bridge import"}, now=now)
        events.log(
            site, "bridge_imported", claim=claim, registration=locked, actor_type=ActorType.OPERATOR,
            actor_id=operator_id, detail={"name": name}, now=now,
        )
    return "done"


def pending_imports():
    """Site ids whose latest import event is a request nobody has answered (the sweep's backstop)."""
    seen, out = set(), []
    for event in DomainEvent.objects.filter(kind__in=IMPORT_KINDS).order_by("-pk"):
        if event.site_id in seen:
            continue
        seen.add(event.site_id)
        if event.kind == "import_requested":
            out.append(event.site_id)
    return out


def import_rows(limit=100):
    """The latest import event of each site, newest first, for the HQ list."""
    seen, rows = set(), []
    qs = DomainEvent.objects.filter(kind__in=IMPORT_KINDS).select_related("site").order_by("-pk")
    for event in qs:
        if event.site_id in seen:
            continue
        seen.add(event.site_id)
        state = {"import_requested": "pending", "bridge_imported": "done"}.get(event.kind, "refused")
        rows.append({
            "site": event.site.public_id, "site_name": event.site.display_name,
            "name": (event.detail or {}).get("name", ""), "state": state,
            "reason": (event.detail or {}).get("reason", ""), "at": event.created_at.isoformat(),
        })
        if len(rows) >= limit:
            break
    return rows
