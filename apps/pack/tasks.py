"""The hourly jobs of the Membership Pack: the day-90 buyer purge and the day-76 buyer email.

`beat` queues them and `worker` runs them. Each does nothing, and reads no pack table, while its
switch is off (`PACK_PURGE_ENABLED`; `PACK_BUYER_NOTICE_ENABLED`, which also needs the purge on).
"""
from celery import shared_task

from apps.pack import buyer_notice, purge


@shared_task(name="pack.purge", ignore_result=True)
def run_purge():
    """Clear the personal details of the buyers of every site past its deletion point. Returns
    counts only."""
    return purge.run()


@shared_task(name="pack.buyer_notice", ignore_result=True)
def run_buyer_notice():
    """Send the day-76 emails that are due, each once. Returns counts only."""
    return buyer_notice.run()
