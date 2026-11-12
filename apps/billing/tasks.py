"""The scheduled jobs of Billing: the lifecycle sweep (B1) and the billing emails (B2a-E),
each every 5 minutes.

`beat` queues them and `worker` runs them. The sweep is the same function `manage.py
billing_sweep` runs. With `BILLING_ENABLED` off the sweep reads and writes nothing, and the
emails are sent only while bank transfer can take a payment.
"""
from celery import shared_task

from apps.billing import notices, services


@shared_task(name="billing.sweep", ignore_result=True)
def sweep():
    """Catch every due subscription up with the time. Returns a count per transition code."""
    return services.sweep()


@shared_task(name="billing.notices", ignore_result=True)
def send_notices():
    """Send the billing emails that are due, each once. Returns a count per email code."""
    return notices.run()
