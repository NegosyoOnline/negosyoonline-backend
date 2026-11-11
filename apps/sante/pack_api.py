"""The Membership Pack reads of the storefront surface (registered on apps/storefront/api.py).

    GET /storefront/v1/pack/?host=...
    GET /storefront/v1/pack/agreement/{version_id}/pdf/?host=...
    GET /storefront/v1/payment-accounts/{account_id}/qr/?host=...

Behind SANTE_AGREEMENT_ENABLED. Off, an unknown host, a foreign or non-Santé site, an unknown
version or account: all answer the same 404. The credential check wraps every route in
apps/storefront/api.py, which also sets Cache-Control: no-store on the answers.
"""
from django.http import HttpResponse
from ninja import Router, Schema

from apps.common.errors import ApiError
from apps.sante import agreement, pack
from apps.storefront import services

router = Router()


def _site(host):
    site = services.served_sante_site(host) if agreement.enabled() else None
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site


class AgreementOut(Schema):
    id: int
    label: str
    effective_date: str


class AccountOut(Schema):
    id: str
    kind: str
    account_type: str
    provider_name: str
    account_name: str
    account_number: str
    account_number_last4: str
    has_qr: bool


class PackOut(Schema):
    price_centavos: int
    currency: str
    available: bool
    agreement: AgreementOut | None
    payment_accounts: list[AccountOut]


@router.get("/pack/", response=PackOut)
def pack_view(request, host: str = ""):
    return pack.pack_out(_site(host))


def _file(content, content_type, name):
    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = "private, no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="{name}"'
    return response


@router.get("/pack/agreement/{version_id}/pdf/")
def agreement_pdf(request, version_id: int, host: str = ""):
    _site(host)
    found = agreement.published_bytes(version_id)
    if found is None:
        raise ApiError("not_found", "Not found.", status=404)
    return _file(found[1], "application/pdf", "membership-agreement.pdf")


@router.get("/payment-accounts/{account_id}/qr/")
def payment_account_qr(request, account_id: str, host: str = ""):
    found = pack.qr_of(_site(host), account_id)
    if found is None:
        raise ApiError("not_found", "Not found.", status=404)
    return _file(found[0], found[1], "payment-qr")
