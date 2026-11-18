"""The local pages that play Xendit's hosted invoice page and its callback, for the preview only.

Routed by config/urls.py only while settings.XENDIT_FAKE is on (which needs DJANGO_DEBUG=true), so
never on Railway. The buyer lands here from the storefront's pay page; "Pay" marks the fake invoice
PAID and delivers the same signed callback Xendit would POST to the site's webhook URL (the real
webhook code runs, with the site's own token), then sends the buyer back to the invoice's success
URL, exactly as Xendit does. "Expire" does the same with EXPIRED. Nothing leaves the machine.
"""
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.test import Client
from django.views.decorators.csrf import csrf_exempt
from django.utils.html import escape

from apps.payments import crypto, fake_xendit
from apps.payments.models import SiteXenditCredential

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fake Xendit invoice (local preview)</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:2rem auto;padding:0 1rem;color:#1a1a1a}}
.box{{border:1px solid #c9c9c9;border-radius:12px;padding:1.25rem}} .tag{{background:#fff3cd;padding:.25rem .5rem;border-radius:6px;font-size:.8rem}}
button{{font:inherit;padding:.7rem 1rem;border-radius:8px;border:1px solid #1a1a1a;background:#fff;margin:.25rem 0;width:100%;cursor:pointer}}
button.primary{{background:#1a1a1a;color:#fff}}</style></head><body><div class="box">
<p><span class="tag">Local preview: this is a fake Xendit, no real payment</span></p>
<h1>Invoice {number}</h1><p>Amount: <strong>{currency} {amount}</strong></p><p>Status: <strong>{status}</strong></p>
{actions}</div></body></html>"""
ACTIONS = """<form method="post"><button class="primary" name="action" value="pay">Pay {currency} {amount}</button>
<button name="action" value="pay_wrong">Pay a different amount (PHP 1,000.00)</button>
<button name="action" value="expire">Let the invoice expire</button></form>"""


def _deliver(request, doc):
    """POST the invoice document to the site's own webhook URL, as Xendit does."""
    try:
        site_pk = int(doc["external_id"].split("-")[1])
    except (IndexError, ValueError):
        return None
    cred = SiteXenditCredential.objects.filter(site_id=site_pk).first()
    if cred is None or not cred.webhook_token_ciphertext:
        return None
    return Client().post(
        f"/v1/webhooks/xendit/{cred.webhook_key}/", json.dumps(doc), content_type="application/json",
        HTTP_X_CALLBACK_TOKEN=crypto.decrypt(cred.webhook_token_ciphertext), HTTP_HOST=request.get_host(),
    )


def _local_return(url):
    """Fake mode only: with XENDIT_FAKE_RETURN_ORIGIN set (a local env var), the same path and query
    on that origin, since the site's public host does not exist on a developer machine."""
    origin = settings.XENDIT_FAKE_RETURN_ORIGIN
    if not origin:
        return url
    parts = urlsplit(url)
    return origin + parts.path + (f"?{parts.query}" if parts.query else "")


@csrf_exempt
def invoice(request, invoice_id):
    if not settings.XENDIT_FAKE or settings.RAILWAY_ENVIRONMENT_NAME:
        raise Http404()
    doc = fake_xendit.get(invoice_id)
    if doc is None:
        raise Http404()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "pay" and doc["status"] == "PENDING":
            doc = fake_xendit.pay(invoice_id)
        elif action == "pay_wrong" and doc["status"] == "PENDING":
            doc = fake_xendit.pay(invoice_id, amount=1000)
        elif action == "expire" and doc["status"] == "PENDING":
            doc = fake_xendit.expire(invoice_id)
        if action in ("pay", "pay_wrong", "expire"):
            _deliver(request, doc)
        # the buyer goes back to the store, as Xendit does; the page there asks the server what happened
        target = doc["success_redirect_url"] if doc["status"] != "EXPIRED" else doc["failure_redirect_url"]
        return HttpResponseRedirect(_local_return(target))
    body = PAGE.format(
        number=escape(doc["id"]), currency=escape(doc["currency"]), amount=f"{float(doc['amount']):,.2f}",
        status=escape(doc["status"]), actions=ACTIONS.format(
            currency=escape(doc["currency"]), amount=f"{float(doc['amount']):,.2f}",
        ) if doc["status"] == "PENDING" else "<p>This invoice is closed.</p>",
    )
    return HttpResponse(body)
