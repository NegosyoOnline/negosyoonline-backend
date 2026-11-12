"""The Subscriber's Meta Pixel settings, under /v1/sites/{site_id}/meta/:

    GET    .../meta/        -> {pixel_id, token_set, test_event_code, last_test, events}
    PUT    .../meta/        {pixel_id?, access_token?, test_event_code?}
    DELETE .../meta/token/  clears the token (the Pixel keeps working in the browser)
    POST   .../meta/test/   sends one flagged PageView and answers {outcome}

No route returns the access token, not even a mask or a length. While META_EVENTS_ENABLED is
off every route answers 404, signed in or not. The site is found in the router's sign-in step
through site_for(account, site_id, template=SANTE), so a missing, removed, foreign or non-Santé
site answers 404 before any body is read; then the Billing-only rule (403 `billing_only`).
"""
import re

from django.conf import settings
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.accounts.sessions import client_ip
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.meta_ads import crypto, services
from apps.meta_ads.models import MetaDelivery, MetaSettings
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for

WRITE_LIMIT, TEST_LIMIT, WINDOW = 20, 10, 3600  # per account per hour
RECENT = 20
PIXEL_RE = re.compile(r"^[0-9]{8,20}$")
CODE_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
TOKEN_RE = re.compile(r"^\S{40,800}$")


def meta_site(request):
    """The router's sign-in step. It finds the site before any body is read."""
    if not settings.META_EVENTS_ENABLED:
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site_for(account, site_id, template=templates.SANTE)
        request.meta_site = _owner_site(request, site_id)
    return account


router = Router(tags=["meta-pixel"], auth=meta_site)


class SettingsIn(Schema):
    model_config = ConfigDict(extra="forbid")
    pixel_id: str | None = None
    access_token: str | None = None
    test_event_code: str | None = None


def _row(site):
    return MetaSettings.objects.get_or_create(site=site)[0]


def _out(site):
    row = MetaSettings.objects.filter(site=site).first()
    recent = MetaDelivery.objects.filter(site=site)[:RECENT]
    return {
        "pixel_id": row.pixel_id if row else "",
        "token_set": bool(row and row.token_encrypted),
        "test_event_code": row.test_event_code if row else "",
        "last_test": (
            {"at": row.last_test_at.isoformat(), "outcome": row.last_test_outcome}
            if row and row.last_test_at else None
        ),
        "events": [
            {
                "event_name": d.event_name, "event_id": d.event_id, "created_at": d.created_at.isoformat(),
                "outcome": d.outcome, "is_test": d.is_test,
            }
            for d in recent
        ],
    }


def _invalid(field, message):
    return ApiError("invalid", message, status=422, fields={field: [message]})


@router.get("/sites/{site_id}/meta/")
def meta_detail(request, site_id: str):
    return _out(request.meta_site)


@router.put("/sites/{site_id}/meta/")
def meta_update(request, site_id: str, payload: SettingsIn):
    hit("meta-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    site = request.meta_site
    pixel = None if payload.pixel_id is None else payload.pixel_id.strip()
    code = None if payload.test_event_code is None else payload.test_event_code.strip()
    token = (payload.access_token or "").strip()
    if pixel and not PIXEL_RE.match(pixel):
        raise _invalid("pixel_id", "A Pixel ID is 8 to 20 digits.")
    if code and not CODE_RE.match(code):
        raise _invalid("test_event_code", "A test event code is letters and digits only.")
    if token and not TOKEN_RE.match(token):
        raise _invalid("access_token", "That access token does not look right.")
    encrypted = ""
    if token:
        try:
            encrypted = crypto.encrypt(token)
        except crypto.TokenKeyError:
            raise ApiError("unavailable", "Saving a token is not available right now.", status=503) from None
    row = _row(site)
    fields = []
    if pixel is not None and pixel != row.pixel_id:
        row.pixel_id, row.last_test_at, row.last_test_outcome = pixel, None, ""
        fields += ["pixel_id", "last_test_at", "last_test_outcome"]
    if code is not None and code != row.test_event_code:
        row.test_event_code = code
        fields.append("test_event_code")
    if encrypted:
        row.token_encrypted = encrypted
        fields.append("token_encrypted")
    if fields:
        row.save(update_fields=[*fields, "updated_at"])
    return _out(site)


@router.delete("/sites/{site_id}/meta/token/")
def meta_clear_token(request, site_id: str):
    hit("meta-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    MetaSettings.objects.filter(site=request.meta_site).update(token_encrypted="")
    return _out(request.meta_site)


@router.post("/sites/{site_id}/meta/test/")
def meta_test(request, site_id: str):
    hit("meta-test", request.account.pk, limit=TEST_LIMIT, window=WINDOW)
    row = MetaSettings.objects.filter(site=request.meta_site).first()
    if row is None or not row.pixel_id or not row.token_encrypted or not row.test_event_code:
        raise ApiError(
            "incomplete", "Save a Pixel ID, an access token and a test event code first.", status=409,
        )
    outcome = services.run_test(
        row, ip=client_ip(request) or "", user_agent=request.META.get("HTTP_USER_AGENT", "")[:400],
    )
    return {"outcome": outcome}
