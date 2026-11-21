"""Background jobs of the Santé app. The worker runs them; the state lives in SanteContentCopy."""
from celery import shared_task

from apps.sante import content_copy


@shared_task(name="sante.copy_content", ignore_result=True)
def copy_content(copy_pk):
    """Copy one requested shared course or media item into the requesting store."""
    content_copy.run_copy(copy_pk)
