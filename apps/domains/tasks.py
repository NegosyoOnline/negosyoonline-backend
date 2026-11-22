"""The domains tasks (beat entries `domains-sweep`, `domains-hourly-check`, `domains-daily-sweep`).

Each does nothing while DOMAINS_ENABLED is off. With the provider off, paused or refused, the
state machine takes no step and no provider call is made; removals still queue (a claim goes to
`removal_pending`), and take effect when the provider is back.
"""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.conf import settings

from apps.domains import lifecycle, machine, providers, purchase, services, sweeps
from apps.domains.models import Domain

logger = logging.getLogger(__name__)
User = get_user_model()


def run_sweep(*, bundle, now=None, probe_fn=None):
    results = {}
    ids = list(Domain.objects.filter(state__in=purchase.PURCHASE_STATES).order_by("pk").values_list("pk", flat=True))
    for claim_id in ids:  # the purchase states: a Check, the purchase steps, the polling of a sent row
        word = purchase.advance(claim_id, providers=bundle, now=now)
        results[word] = results.get(word, 0) + 1
    ids = list(Domain.objects.filter(state__in=machine.STEP_STATES).order_by("pk").values_list("pk", flat=True))
    for claim_id in ids:
        word = machine.advance(claim_id, providers=bundle, now=now, probe_fn=probe_fn)
        results[word] = results.get(word, 0) + 1
    return results


def run_hourly(*, bundle, now=None, probe_fn=None):
    results = {}
    ids = list(Domain.objects.filter(state__in=machine.CHECK_STATES).order_by("pk").values_list("pk", flat=True))
    for claim_id in ids:
        word = machine.hourly_check(claim_id, providers=bundle, now=now, probe_fn=probe_fn)
        results[word] = results.get(word, 0) + 1
    return results


def run_daily(*, bundle, now=None):
    out = {"removals_queued": lifecycle.sweep(now)}
    if bundle is None:
        return {**out, "skipped": "provider off or paused"}
    out["zone_audit"] = sweeps.zone_audit(bundle)
    out["vercel"] = sweeps.vercel_compare(bundle)
    out["registrations"] = sweeps.registration_read(bundle, now)
    out["prices"] = purchase.sentinel(bundle, now)
    return out


@shared_task(name="domains.sweep", ignore_result=True)
def sweep():
    if not settings.DOMAINS_ENABLED:
        return {"skipped": "switched off"}
    lifecycle.sweep()  # queues removals even while the provider is paused
    services.safety_net()  # a first payment whose hook was skipped still marks the site eligible
    for claim_id in purchase.pending_confirms():  # a "confirmed registered" request whose task was lost
        confirm_registered(claim_id)
    return run_sweep(bundle=providers.current())


@shared_task(name="domains.confirm_registered", ignore_result=True)
def confirm_registered(claim_id):
    """The worker half of HQ "confirmed registered" (the api holds no Registrar token)."""
    if not settings.DOMAINS_ENABLED:
        return "switched off"
    result = purchase.process_confirm(claim_id, providers.current())
    _audit_confirm(claim_id, result)
    return result


def _audit_confirm(claim_id, result):
    """The operator audit entry for the answer, named for the operator who asked."""
    from apps.domains.models import DomainEvent  # noqa: PLC0415
    from apps.operators import audit  # noqa: PLC0415

    if result == "none":
        return
    event = DomainEvent.objects.filter(claim_id=claim_id, kind__in=purchase.CONFIRM_KINDS).order_by("-pk").first()
    operator = User.objects.filter(pk=event.actor_id).first() if event else None
    claim = Domain.objects.select_related("site").get(pk=claim_id)
    target = dict(target_type="site", target_id=claim.site.public_id)
    if result == "done":
        audit.record(operator, "domains_confirmed_registered", detail={"claim": claim.public_id}, **target)
    else:
        audit.record(operator, "domains_confirm_refused", detail={"claim": claim.public_id, "reason": result}, **target)


@shared_task(name="domains.check_name", ignore_result=True)
def check_name(claim_id):
    """The owner's Check: read only. It never advances a claim past `quoted`."""
    if not settings.DOMAINS_ENABLED:
        return "switched off"
    return purchase.advance(claim_id, providers=providers.current(), only_check=True)


@shared_task(name="domains.hourly_check", ignore_result=True)
def hourly_check():
    if not settings.DOMAINS_ENABLED:
        return {"skipped": "switched off"}
    return run_hourly(bundle=providers.current())


@shared_task(name="domains.daily_sweep", ignore_result=True)
def daily_sweep():
    if not settings.DOMAINS_ENABLED:
        return {"skipped": "switched off"}
    return run_daily(bundle=providers.current())
