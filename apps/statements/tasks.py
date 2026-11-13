"""The statements safety net, every 5 minutes beside the billing sweep. With the statements
switch off it reads and writes nothing."""
from celery import shared_task

from apps.statements import delivery, issue


@shared_task(name="statements.ensure", ignore_result=True)
def ensure():
    return issue.ensure_statements()


@shared_task(name="statements.deliver", ignore_result=True)
def deliver():
    """Emails statements not yet delivered and retries failed ones (5 attempts in all). With
    the statements switch off it reads and writes nothing."""
    return delivery.run_due()
