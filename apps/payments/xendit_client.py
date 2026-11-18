"""Per-site Xendit calls with the site owner's own key (spec v1.12 7.1: never a platform key).
Adapted from TWCinc negosyoonline_orders/xendit_client.py onto stdlib urllib.request (no new
dependency). One request, a timeout, no retry. Never logs a key, a header or a body."""
import base64
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils.dateparse import parse_datetime

BASE_URL = "https://api.xendit.co"
TIMEOUT = 20  # seconds, for connect and read together


class XenditError(Exception):
    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind  # unauthorized | unavailable | invalid


@dataclass(frozen=True)
class InvoiceState:
    external_id: str
    invoice_id: str
    status: str  # upper case: PAID | SETTLED | EXPIRED | PENDING ...
    amount: Decimal | None
    currency: str
    paid_at: datetime | None = None  # Xendit's own payment time (UTC, aware); None when absent


def _open(request):
    """The only network call in this module (replaced in tests and by the local fake)."""
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def _call(api_key, method, path, *, payload=None, params=None):
    if settings.XENDIT_FAKE:  # tests and the local preview only; settings.py allows it with DJANGO_DEBUG only
        from apps.payments import fake_xendit
        return fake_xendit.call(api_key, method, path, payload=payload, params=params)
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    token = base64.b64encode(f"{api_key}:".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _open(request) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise XenditError("unauthorized") from None
        if exc.code == 429 or exc.code >= 500:
            raise XenditError("unavailable") from None
        raise XenditError("invalid") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
        raise XenditError("unavailable") from None
    try:
        return json.loads(body)
    except ValueError:
        raise XenditError("unavailable") from None


def check_key(api_key):
    """One server-side read that proves the key works."""
    _call(api_key, "GET", "/balance")


def create_invoice(api_key, *, external_id, amount, description, duration_seconds, success_url, failure_url):
    return _call(api_key, "POST", "/v2/invoices", payload={
        "external_id": external_id, "amount": float(amount), "currency": "PHP",
        "description": description, "invoice_duration": int(duration_seconds),
        "success_redirect_url": success_url, "failure_redirect_url": failure_url,
    })


def get_invoice(api_key, invoice_id):
    return _call(api_key, "GET", f"/v2/invoices/{urllib.parse.quote(str(invoice_id), safe='')}")


def find_invoices(api_key, external_id):
    """Every invoice whose external_id equals ours (never a blind docs[0]), newest first."""
    docs = _call(api_key, "GET", "/v2/invoices", params={"external_id": external_id})
    mine = [d for d in (docs if isinstance(docs, list) else [])
            if isinstance(d, dict) and str(d.get("external_id") or "") == external_id]
    return sorted(mine, key=lambda d: str(d.get("created") or ""), reverse=True)


def newest_unexpired(docs):
    """The newest unexpired invoice, or None: when only expired ones exist a new one is created."""
    live = [d for d in docs if str(d.get("status") or "").upper() != "EXPIRED"]
    return live[0] if live else None


def find_invoice(api_key, external_id):
    """For reconcile: the newest unexpired invoice, else the newest (an EXPIRED or PAID document
    is still the order's state to apply), else None."""
    docs = find_invoices(api_key, external_id)
    return newest_unexpired(docs) or (docs[0] if docs else None)


def _money(value):
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def parse_paid_at(value):
    """An aware ISO-8601 string in UTC, else None. Never raises: a verified payment must always
    parse."""
    try:
        if not isinstance(value, str):
            return None
        parsed = parse_datetime(value.strip())
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(dt_timezone.utc)
    except Exception:  # noqa: BLE001 - ValueError (2026-02-30), OverflowError (extreme aware date)
        return None


def parse_invoice(doc):
    amount = doc.get("paid_amount") if doc.get("paid_amount") is not None else doc.get("amount")
    return InvoiceState(
        external_id=str(doc.get("external_id") or "")[:64], invoice_id=str(doc.get("id") or "")[:64],
        status=str(doc.get("status") or "").upper()[:16], amount=_money(amount),
        currency=str(doc.get("currency") or "").upper()[:3], paid_at=parse_paid_at(doc.get("paid_at")),
    )
