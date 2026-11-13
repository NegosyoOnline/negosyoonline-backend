"""The daily invoice alert (Billing Build B5, PR 10; decision Q9, 2026-11-13).

At 08:00 Manila time, while an invoice is waiting to be written, every active staff user gets
one internal email: how many items wait on each queue tab, the oldest sale date and the fixed
notice. It holds no customer name, site, reference or amount. With the register off it reads
and sends nothing. A cache key per Manila day keeps a beat retry or a second run from sending
again; a day on which nobody could be emailed releases the key so the next run tries again.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.invoices import services
from apps.invoices.models import ManualInvoiceItem as Item
from apps.notifications.services import EmailNotSent, send_email
from apps.operators.models import User

logger = logging.getLogger(__name__)

CATEGORY = "invoice_queue_alert"
QUEUE_PATH = "/finance/invoices/"
NOTICE = "DO NOT issue invoices in a single delayed Monday batch."
_KEY = "invoices:daily_alert:{day}"
_KEEP_SECONDS = 2 * 24 * 3600


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _link():
    # The HQ app's origin is the one setting that names it; without it the path is all we have.
    origin = settings.HQ_STAFF_ORIGIN.rstrip("/")
    return f"{origin}{QUEUE_PATH}" if origin else QUEUE_PATH


def run():
    """Returns what happened: {"skipped": reason} or {"sent": how many emails went out}."""
    from apps.statements import switch  # noqa: PLC0415

    if not switch.register_enabled():
        return {"skipped": "off"}
    today = services.manila_date(timezone.now())
    pending = Item.objects.filter(status=Item.Status.PENDING)
    count = pending.count()
    if not count:
        return {"skipped": "nothing pending"}
    key = _KEY.format(day=today.isoformat())
    if not cache.add(key, 1, _KEEP_SECONDS):
        return {"skipped": "already sent today"}
    oldest = pending.order_by("sale_date").values_list("sale_date", flat=True).first()
    recorded = Item.objects.filter(status=Item.Status.RECORDED).count()
    uploaded = Item.objects.filter(status=Item.Status.UPLOADED).count()
    age = (today - oldest).days
    subject = f"NegosyoOnline HQ: {_plural(count, 'invoice')} to write, oldest {_plural(age, 'day')}"
    body = "\n".join([
        "Invoices waiting in the HQ invoice queue:",
        f"Ready to write: {count}",
        f"Issued, awaiting upload: {recorded}",
        f"Awaiting verification: {uploaded}",
        f"Oldest waiting sale date: {services.paper_date(oldest)}",
        "",
        NOTICE,
        "",
        f"Open the queue: {_link()}",
    ])
    addresses = list(
        User.objects.filter(is_staff=True, is_active=True).exclude(email="")
        .order_by("pk").values_list("email", flat=True)
    )
    sent = 0
    for address in addresses:
        try:
            send_email(to=address, category=CATEGORY, subject=subject, body=body)
            sent += 1
        except EmailNotSent:
            logger.warning("Invoice alert not sent to one staff address")
    if not sent:
        cache.delete(key)
    return {"sent": sent}
