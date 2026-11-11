"""The one scheduled job of Billing Build B1: the lifecycle sweep, every 5 minutes.

`beat` queues it and `worker` runs it. It is the same function `manage.py billing_sweep` runs.
With `BILLING_ENABLED` off it reads and writes nothing.
"""
from celery import shared_task

from apps.billing import services


@shared_task(name="billing.sweep", ignore_result=True)
def sweep():
    """Catch every due subscription up with the time. Returns a count per transition code."""
    return services.sweep()
