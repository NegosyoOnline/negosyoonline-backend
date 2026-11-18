"""The owner's Xendit routes and the Xendit webhook (Coach Ed, 2026-11-18). Kept apart from
api.py, which is the bank and e-wallet routes.

    PUT    /v1/sites/{site_id}/payment-methods/xendit/   {api_key, webhook_token}
    DELETE /v1/sites/{site_id}/payment-methods/xendit/
    POST   /v1/webhooks/xendit/{webhook_key}/            header x-callback-token (no sign-in)

The owner routes answer the same 404 as any unknown route while Xendit is not offered (platform
switch XENDIT_ENABLED off, or a site that is not a Santé site), and 403 `billing_only` for a
suspended or ended site, like every owner function. No secret is ever answered.
"""
from ninja import Router

from apps.payments import services, webhooks
from apps.payments.api import StrictSchema, _api_base, _count_write, payment_site

router = Router(tags=["payment-methods"], auth=payment_site)
# The webhook: no sign-in, the site's own token is the authority (apps/payments/webhooks.py).
public = Router(tags=["payment-methods"], auth=None)


class XenditPut(StrictSchema):
    api_key: str
    webhook_token: str


@router.put("/sites/{site_id}/payment-methods/xendit/")
def xendit_save(request, site_id: str, payload: XenditPut):
    """Save the owner's own Xendit secret key and webhook verification token (encrypted, never
    returned). The key is checked with one read at Xendit first. Answers the payment-methods read."""
    services.require_offered(request.payment_site)
    _count_write(request)
    services.save_xendit(
        request.account, request.payment_site, api_key=payload.api_key,
        webhook_token=payload.webhook_token,
    )
    return services.settings_out(request.payment_site, _api_base(request))


@router.delete("/sites/{site_id}/payment-methods/xendit/")
def xendit_remove(request, site_id: str):
    """Remove both secrets and switch Xendit off; the webhook URL stays. Also answers
    `awaiting_xendit_orders`: the orders still waiting for a Xendit payment."""
    services.require_offered(request.payment_site)
    _count_write(request)
    waiting = services.clear_xendit(request.account, request.payment_site)
    return {**services.settings_out(request.payment_site, _api_base(request)), "awaiting_xendit_orders": waiting}


@public.post("/webhooks/xendit/{webhook_key}/")
def xendit_webhook(request, webhook_key: str):
    return webhooks.handle(webhook_key, request)
