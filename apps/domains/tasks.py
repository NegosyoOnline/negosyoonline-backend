"""The domains tasks (beat entries `domains-sweep`, `domains-hourly-check`, `domains-daily-sweep`).

Each does nothing while DOMAINS_ENABLED is off. With the provider off, paused or refused, the
state machine takes no step and no provider call is made; removals still queue (a claim goes to
`removal_pending`), and take effect when the provider is back.
"""
import logging

from celery import shared_task
from django.conf import settings

from apps.domains import lifecycle, machine, providers, sweeps
from apps.domains.models import Domain

logger = logging.getLogger(__name__)


def run_sweep(*, bundle, now=None, probe_fn=None):
    results = {}
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
    return out


@shared_task(name="domains.sweep", ignore_result=True)
def sweep():
    if not settings.DOMAINS_ENABLED:
        return {"skipped": "switched off"}
    lifecycle.sweep()  # queues removals even while the provider is paused
    return run_sweep(bundle=providers.current())


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
