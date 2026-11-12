"""The statements safety net, every 5 minutes beside the billing sweep. With the statements
switch off it reads and writes nothing."""
from celery import shared_task

from apps.statements import issue


@shared_task(name="statements.ensure", ignore_result=True)
def ensure():
    return issue.ensure_statements()
