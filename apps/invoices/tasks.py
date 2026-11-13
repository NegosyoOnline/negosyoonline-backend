"""The register's safety net, every 5 minutes beside the billing sweep. It is the same function
`manage.py invoices_backfill_items --apply` runs. With the register off it reads and writes
nothing. The daily alert goes to the staff at 08:00 Manila time (apps/invoices/alert.py)."""
from celery import shared_task

from apps.invoices import alert, services


@shared_task(name="invoices.ensure_items", ignore_result=True)
def ensure_items():
    return services.ensure_items()


@shared_task(name="invoices.daily_alert", ignore_result=True)
def daily_alert():
    """One email to the active staff while an invoice waits to be written. Sends nothing while
    the register is off, when nothing is pending, or a second time on the same Manila day."""
    return alert.run()
