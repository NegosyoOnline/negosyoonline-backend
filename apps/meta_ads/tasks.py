"""The Celery tasks that send to Meta, on the existing worker. A page event carries its payload in
the task message only; an order event is rebuilt from rows. Up to three attempts."""
from celery import shared_task

from apps.meta_ads import services


@shared_task(bind=True, name="meta_ads.send_page_event", ignore_result=True, max_retries=5)
def send_page_event(self, delivery_id, event):
    if services.deliver(delivery_id, event) == services.RETRY:
        raise self.retry(countdown=services.RETRY_DELAY_SECONDS)


@shared_task(bind=True, name="meta_ads.send_order_event", ignore_result=True, max_retries=5)
def send_order_event(self, delivery_id):
    if services.deliver(delivery_id) == services.RETRY:
        raise self.retry(countdown=services.RETRY_DELAY_SECONDS)


@shared_task(name="meta_ads.sweep", ignore_result=True)
def sweep():
    return services.sweep()
