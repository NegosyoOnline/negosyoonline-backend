"""Payment method endpoints for the Subscriber's Payments page. Shapes here are the
frontend's contract (apps/dash/public/assets/js/payments.js).

Every endpoint is signed-in only and reaches its site through site_for(account, site_id), so
a missing site, a removed site and another account's site all answer the same 404. While
PAYMENT_METHODS_ENABLED is off every route answers that 404 too, signed in or not.

These are owner functions, so they follow Billing's rule (plan D11) through the sites API's
own check: once the site's subscription is suspended or ended they answer 403 `billing_only`,
after the 404 check.

Both checks run in the router's sign-in step, which comes before the body is read: a body
sent to a site that is not the caller's is never looked at.

Xendit (Coach Ed, 2026-11-18): the owner's key routes are in api_xendit.py. While Xendit is not
offered (platform switch XENDIT_ENABLED off, or a site that is not a Santé site) the read shows it
as not set up and off, and the PATCH refuses to switch it on. An account is named by its public id.
"""
from ninja import Router, Schema
from pydantic import ConfigDict, StrictBool

from apps.accounts.api import signed_in
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.payments import services
from apps.sites.api import _owner_site
from apps.sites.services import site_for

WRITE_LIMIT, WRITE_WINDOW = 20, 3600  # payment settings writes per account per hour


def payment_site(request):
    """The router's sign-in check. It also finds the caller's site, before any body is read,
    and leaves it on the request."""
    if not services.enabled():
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site_for(account, site_id)
        request.payment_site = _owner_site(request, site_id)
    return account


router = Router(tags=["payment-methods"], auth=payment_site)


class StrictSchema(Schema):
    """A request body. An unknown key is refused."""

    model_config = ConfigDict(extra="forbid")


class MethodsPatch(StrictSchema):
    direct_enabled: StrictBool | None = None
    xendit_enabled: StrictBool | None = None


def _api_base(request):
    """The API's own address as this request reached it (the host is validated by ALLOWED_HOSTS):
    the webhook URL the owner sets in Xendit is built from it."""
    return request.build_absolute_uri("/")


def _count_write(request):
    hit("payment-methods-write", request.account.pk, limit=WRITE_LIMIT, window=WRITE_WINDOW)


@router.get("/sites/{site_id}/payment-methods/")
def payment_methods_detail(request, site_id: str):
    return services.settings_out(request.payment_site, _api_base(request))


@router.patch("/sites/{site_id}/payment-methods/")
def payment_methods_update(request, site_id: str, payload: MethodsPatch):
    """Switch direct transfer on or off. A key that is left out, or null, is left as it is.
    `xendit_enabled: true` is refused with 400 `invalid_request`."""
    _count_write(request)
    site = request.payment_site
    services.set_enabled(
        request.account, site, direct=payload.direct_enabled, xendit=payload.xendit_enabled
    )
    return services.settings_out(site, _api_base(request))


@router.post("/sites/{site_id}/payment-methods/accounts/", response={201: dict})
def payment_account_add(request, site_id: str):
    """A form post (multipart), as the page sends it. `qr` is an optional JPEG, PNG or WebP
    image of at most 2 MB, kept as a private file."""
    _count_write(request)
    row = services.add_account(request.account, request.payment_site, request.POST, request.FILES)
    return 201, services.account_out(row)


@router.post("/sites/{site_id}/payment-methods/accounts/{account_id}/")
def payment_account_change(request, site_id: str, account_id: str):
    """The Change form: the same fields as adding an account, as a form post. No `qr` keeps
    the current QR code; a new one replaces it."""
    _count_write(request)
    row = services.update_account(
        request.account, request.payment_site, account_id, request.POST, request.FILES
    )
    return services.account_out(row)


@router.delete("/sites/{site_id}/payment-methods/accounts/{account_id}/")
def payment_account_remove(request, site_id: str, account_id: str):
    _count_write(request)
    services.remove_account(request.account, request.payment_site, account_id)
    return {"removed": True}
