"""A fake Xendit for tests and for the local preview. It makes no network call.

It is used only when settings.XENDIT_FAKE is on, which settings.py allows only while DJANGO_DEBUG
is true (so never on Railway). Invoices live in this process's memory. It answers the four calls
apps/payments/xendit_client.py makes, in Xendit's shapes, and offers `pay`, `expire` and
`webhook_body` so a test or the local pages can play Xendit's side:

    fake_xendit.reset()
    inv = fake_xendit.pay(invoice_id)            # marks it PAID; returns the document
    body = fake_xendit.webhook_body(invoice_id)  # the JSON Xendit would POST to the webhook

Keys: a key containing "rejected" is answered 401 (unauthorized); "down" is answered unavailable.
"""
import itertools
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings

from apps.payments.xendit_client import XenditError

_invoices = {}
_counter = itertools.count(1)
calls = []  # (method, path) of every call, never a key


def reset():
    _invoices.clear()
    calls.clear()


def _now():
    return datetime.now(dt_timezone.utc)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def base_url():
    return getattr(settings, "XENDIT_FAKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def call(api_key, method, path, *, payload=None, params=None):
    calls.append((method, path))
    if "rejected" in api_key:
        raise XenditError("unauthorized")
    if "down" in api_key:
        raise XenditError("unavailable")
    if method == "GET" and path == "/balance":
        return {"balance": 0}
    if method == "POST" and path == "/v2/invoices":
        n = next(_counter)
        invoice_id = f"fake-inv-{n}-{uuid.uuid4().hex[:8]}"
        created = _now()
        doc = {
            "id": invoice_id, "external_id": payload["external_id"], "status": "PENDING",
            "amount": payload["amount"], "currency": payload.get("currency", "PHP"),
            "description": payload.get("description", ""),
            "invoice_url": f"{base_url()}/fake-xendit/invoice/{invoice_id}/",
            "expiry_date": _iso(created + timedelta(seconds=payload["invoice_duration"])),
            "created": _iso(created), "success_redirect_url": payload["success_redirect_url"],
            "failure_redirect_url": payload["failure_redirect_url"],
        }
        _invoices[invoice_id] = doc
        return dict(doc)
    if method == "GET" and path == "/v2/invoices":
        wanted = (params or {}).get("external_id")
        return [dict(d) for d in _invoices.values() if d["external_id"] == wanted]
    if method == "GET" and path.startswith("/v2/invoices/"):
        doc = _invoices.get(path.rsplit("/", 1)[1])
        if doc is None:
            raise XenditError("invalid")
        return dict(doc)
    raise XenditError("invalid")


def get(invoice_id):
    return _invoices.get(invoice_id)


def pay(invoice_id, *, amount=None, currency=None):
    """Play the buyer paying: PAID with Xendit's own paid_at and (optionally) a different amount."""
    doc = _invoices[invoice_id]
    doc["status"] = "PAID"
    doc["paid_at"] = _iso(_now())
    doc["paid_amount"] = doc["amount"] if amount is None else amount
    if currency:
        doc["currency"] = currency
    return dict(doc)


def expire(invoice_id):
    doc = _invoices[invoice_id]
    doc["status"] = "EXPIRED"
    return dict(doc)


def webhook_body(invoice_id):
    return dict(_invoices[invoice_id])
