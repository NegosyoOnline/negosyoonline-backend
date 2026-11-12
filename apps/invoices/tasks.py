"""The register's safety net, every 5 minutes beside the billing sweep. It is the same function
`manage.py invoices_backfill_items --apply` runs. With the register off it reads and writes
nothing."""
from celery import shared_task

from apps.invoices import services


@shared_task(name="invoices.ensure_items", ignore_result=True)
def ensure_items():
    return services.ensure_items()
