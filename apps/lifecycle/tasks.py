"""The Celery task of the site deletion executor. `beat` queues it every 5 minutes and `worker`
runs it. With SITE_ERASURE_ENABLED off it returns at once and reads no lifecycle table; on, it runs
only batches a person approved (plan z8uq9mb22v, section 6.4)."""
from celery import shared_task

from apps.lifecycle import workflow


@shared_task(name="lifecycle.run_approved", ignore_result=True)
def run_approved():
    """Run the approved batches. Returns counts only."""
    return workflow.run_approved()
