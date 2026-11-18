"""The per-site Xendit webhook (spec v1.12 7.1: a webhook verified for one site can never mark a
payment on another site). Adapted from TWCinc negosyoonline_orders/webhooks.py.

    POST /v1/webhooks/xendit/{webhook_key}/    header x-callback-token

The path names the site's random webhook key; the token is compared (constant time) with that
site's own decrypted token; only that site's orders can match. Auth is none: the token is the
authority. Answers 404 while XENDIT_ENABLED is off, for an unknown key, a deleted site or a site
that is not a Santé site. A duplicate event answers 200 and changes nothing. Never logs a header
value or a body; logs carry site ids only.
"""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.common.errors import ApiError
from apps.payments import crypto, xendit_client
from apps.payments.models import SiteXenditCredential, XenditEvent
from apps.sites import templates

logger = logging.getLogger(__name__)
MAX_BODY = 65536
EVENT_UNIQUE = "payments_xendit_event_uniq"


class DuplicateEvent(Exception):
    pass


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _dispatch(site, state):
    """(outcome, order pk) from the app whose order the reference names, else no_match."""
    from apps.pack import xendit as pack_xendit
    return pack_xendit.handle(site, state) or ("no_match", None)


def record_event(site, state, *, event_id, event_type, body_sha256, source):
    """One outer transaction: the handler decides and applies (it locks the site row, then the
    order row); then the event row is inserted in its own savepoint. Only an IntegrityError from the
    (site, provider_event_id) constraint is a duplicate: it rolls back the whole outer transaction
    and raises DuplicateEvent. Any other exception propagates and rolls everything back."""
    with transaction.atomic():
        outcome, ref = _dispatch(site, state)
        try:
            with transaction.atomic():
                XenditEvent.objects.create(
                    site=site, provider_event_id=event_id, event_type=event_type[:64],
                    external_id=state.external_id, invoice_id=state.invoice_id,
                    body_sha256=body_sha256, outcome=outcome, order_ref=ref, source=source,
                )
        except IntegrityError as exc:
            if EVENT_UNIQUE in str(exc) or "provider_event_id" in str(exc):
                raise DuplicateEvent() from exc
            raise
    return outcome, ref


def handle(webhook_key, request):
    """Process one delivery; returns the JSON body to answer with, or raises ApiError."""
    if not settings.XENDIT_ENABLED:
        raise _not_found()
    cred = (
        SiteXenditCredential.objects.select_related("site").filter(webhook_key=webhook_key)
        .exclude(webhook_token_ciphertext="").first()
    )
    if cred is None or cred.site.deleted_at is not None or cred.site.template != templates.SANTE:
        raise _not_found()
    site = cred.site
    try:
        expected = crypto.decrypt(cred.webhook_token_ciphertext)
    except crypto.SecretKeyError:
        raise ApiError("payments_unavailable", "Payments are not available right now.", status=503) from None
    supplied = request.headers.get("x-callback-token") or ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        logger.warning("xendit webhook refused site=%s code=invalid_token", site.pk)
        raise ApiError("invalid_token", "Invalid token.", status=401)
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    if declared > MAX_BODY:
        raise ApiError("payload_too_large", "Payload too large.", status=413)
    body = request.body
    if len(body) > MAX_BODY:
        raise ApiError("payload_too_large", "Payload too large.", status=413)
    try:
        doc = json.loads(body)
        state = xendit_client.parse_invoice(doc)
    except (ValueError, TypeError, AttributeError):
        raise ApiError("invalid_payload", "Invalid payload.", status=400) from None
    if not state.external_id or not state.status:
        raise ApiError("invalid_payload", "Invalid payload.", status=400)
    event_id = (request.headers.get("webhook-id") or f"{state.invoice_id}:{state.status}")[:128]
    if XenditEvent.objects.filter(site=site, provider_event_id=event_id).exists():
        return {"received": True}
    try:
        record_event(
            site, state, event_id=event_id, event_type=str(doc.get("event") or "invoice"),
            body_sha256=hashlib.sha256(body).hexdigest(), source="webhook",
        )
    except DuplicateEvent:
        return {"received": True}  # a concurrent delivery of this event won; everything rolled back
    except Exception:  # noqa: BLE001 - not a duplicate: Xendit must redeliver
        logger.exception("xendit webhook failed site=%s", site.pk)
        raise ApiError("processing_failed", "Processing failed.", status=500) from None
    return {"received": True}
