"""The Xendit reconcile task. `beat` queues it every 15 minutes and `worker` runs it. It does
nothing, and reads no payment table, while XENDIT_ENABLED is off."""
from celery import shared_task


@shared_task(name="payments.xendit_reconcile", ignore_result=True)
def xendit_reconcile():
    from apps.payments import reconcile
    return reconcile.run()
