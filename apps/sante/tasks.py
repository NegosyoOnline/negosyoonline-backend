"""Background jobs of the Santé app. The worker runs them; the state lives in SanteContentCopy."""
from celery import shared_task

from apps.sante import content_copy


@shared_task(name="sante.copy_content", ignore_result=True)
def copy_content(copy_pk):
    """Copy one requested shared course or media item into the requesting store."""
    content_copy.run_copy(copy_pk)


@shared_task(name="sante.poll_video_copy", ignore_result=True)
def poll_videos(copy_pk, attempt=0):
    """Check the new videos of a Copying course; finish it when Bunny says they are all ready."""
    content_copy.poll_videos(copy_pk, attempt)
