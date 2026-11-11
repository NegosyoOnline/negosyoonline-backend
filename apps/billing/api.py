"""Billing endpoints. Shapes here are the frontend's contract.

Every endpoint is signed-in only and reaches its site through sites.services.site_for, so a
missing site, a removed site and another account's site answer the same 404 before any
billing row is read. A site with no subscription, and every site while BILLING_ENABLED is
off, answers that 404 too.

The bank-transfer routes (Billing Build B2a) answer 404 as well while payment by bank transfer
is off, and then read none of its tables. They stay open to a suspended or ended site.
"""
import json
import re
from datetime import date, datetime

from django.http import HttpResponse
from ninja import Router, Schema

from apps.accounts.api import signed_in
from apps.accounts.services import record_event
from apps.billing import plan, proofs, services
from apps.billing.models import BankTransferPayment
from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.pagination import DEFAULT_LIMIT, MAX_LIMIT
from apps.common.throttle import hit
from apps.sites.services import site_for

router = Router(tags=["billing"], auth=signed_in)

WRITE_LIMIT, WRITE_WINDOW = 20, 3600  # cancels and reactivations per account per hour
PROOF_LIMIT, PROOF_WINDOW = 10, 3600  # proofs of payment per account per hour
SUBMIT_FIELDS = (
    "plan", "transferred_on", "transfer_reference", "proof", "renewal_consent", "consent_version",
)
PROOF_FILE_NAMES = {
    "image/jpeg": "proof.jpg", "image/png": "proof.png", "application/pdf": "proof.pdf",
}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PlanOut(Schema):
    name: str
    price_centavos: int
    currency: str
    interval: str
    trial_days: int


class CycleOut(Schema):
    sequence: int
    start_at: datetime
    end_at: datetime
    settlement: str


class BillingOut(Schema):
    state: str
    storefront_served: bool
    billing_only: bool
    plan: PlanOut
    trial_start_at: datetime
    trial_end_at: datetime
    current_cycle: CycleOut
    next_due_at: datetime | None
    attention_ends_at: datetime | None
    cancel_requested_at: datetime | None
    service_end_at: datetime | None
    download_until: datetime | None
    deletion_due_at: datetime | None
    pay_by: datetime | None
    clocks_started: bool
    billing_exempt: bool
    outstanding_centavos: int
    can_cancel: bool
    can_reactivate: bool


def _nothing_sent(request):
    """A cancel and a reactivation carry nothing: no body, or `{}`. Anything else is refused."""
    raw = request.body.strip()
    if not raw:
        return
    try:
        sent = json.loads(raw)
    except ValueError:
        sent = None
    if sent != {}:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"request": ["This request takes no values."]},
        )


def _subscription_for(request, site_id):
    site = site_for(request.account, site_id)
    subscription = services.subscription_of(site)
    if subscription is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site, subscription


@router.get("/sites/{site_id}/billing/", response=BillingOut)
def billing_detail(request, site_id: str):
    site, _ = _subscription_for(request, site_id)
    return services.overview(site)


@router.post("/sites/{site_id}/billing/cancel/", response=BillingOut)
def billing_cancel(request, site_id: str):
    site, subscription = _subscription_for(request, site_id)
    _nothing_sent(request)
    hit("billing-write", request.account.pk, limit=WRITE_LIMIT, window=WRITE_WINDOW)
    services.cancel(subscription)
    record_event(request.account, "subscription_cancelled", site=site.public_id)
    return services.overview(site)


@router.post("/sites/{site_id}/billing/reactivate/", response=BillingOut)
def billing_reactivate(request, site_id: str):
    site, subscription = _subscription_for(request, site_id)
    _nothing_sent(request)
    hit("billing-write", request.account.pk, limit=WRITE_LIMIT, window=WRITE_WINDOW)
    services.withdraw_cancellation(subscription)
    record_event(request.account, "subscription_cancellation_withdrawn", site=site.public_id)
    return services.overview(site)


# The plans on offer (Billing Build B3)

class PublicPlanOut(Schema):
    plan: str
    version: int
    name: str
    amount_centavos: int
    currency: str
    term_months: int
    interval: str
    saving_centavos: int | None


class PlansOut(Schema):
    plans: list[PublicPlanOut]


@router.get("/plan/", response=PlansOut, auth=None)
def plans_on_offer(request):
    """The plans anyone may see: Monthly, and Annual while it is on offer. It reads no table.
    The Launch Promo is never listed here; it is shown to the site that can buy it."""
    listed = [plan.MONTHLY]
    if plan.annual_open():
        listed.append(plan.ANNUAL)
    return {"plans": [
        {
            "plan": p.code, "version": p.version, "name": p.name,
            "amount_centavos": p.price_centavos, "currency": plan.CURRENCY,
            "term_months": p.months, "interval": "year" if p.months == 12 else "month",
            # Twelve Monthly payments less what Annual costs.
            "saving_centavos": (
                plan.MONTHLY.price_centavos * p.months - p.price_centavos
                if p.months > 1 else None
            ),
        }
        for p in listed
    ]}


# Bank transfer with proof (Billing Build B2a)

class PaidCycleOut(Schema):
    sequence: int
    start_at: datetime
    end_at: datetime


class ProofOut(Schema):
    content_type: str
    size_bytes: int


class PaymentOut(Schema):
    id: str
    status: str
    plan: str
    amount_centavos: int
    usage_centavos: int
    currency: str
    transferred_on: date
    transfer_reference: str
    submitted_at: datetime
    decided_at: datetime | None
    reject_reason: str | None
    reject_note: str | None
    proof: ProofOut
    cycle: PaidCycleOut | None


class PaymentsOut(Schema):
    items: list[PaymentOut]
    next_cursor: str | None


class NextPlanOut(Schema):
    plan: str
    amount_centavos: int
    interval: str
    first_due_at: datetime | None


class ConsentOut(Schema):
    required: bool
    version: str
    then: NextPlanOut
    first_due_at: datetime | None


class OfferOut(Schema):
    plan: str
    name: str
    amount_centavos: int
    usage_centavos: int
    currency: str
    months: int
    version: int
    term_months: int
    period_start_at: datetime | None
    period_end_at: datetime | None
    starts_at_confirmation: bool
    then: NextPlanOut | None
    consent: ConsentOut | None


class TransferAccountOut(Schema):
    kind: str
    provider_name: str
    account_name: str
    account_number: str


class ProofRulesOut(Schema):
    max_bytes: int
    content_types: list[str]


class UsageDueOut(Schema):
    centavos: int
    lines: int
    payable_alone: bool


class PaymentOptionsOut(Schema):
    payable: bool
    not_payable_reason: str | None
    payable_from: datetime | None
    offers: list[OfferOut]
    usage: UsageDueOut
    transfer_accounts: list[TransferAccountOut]
    under_review: PaymentOut | None
    latest: PaymentOut | None
    proof_rules: ProofRulesOut


def _payment(payment):
    """A payment as its owner sees it. Never the operator, the amount the operator typed or
    where the file is kept."""
    if payment is None:
        return None
    rejected = payment.status == BankTransferPayment.Status.REJECTED
    cycle = payment.cycle
    return {
        "id": payment.public_id,
        "status": payment.status,
        "plan": payment.plan_code,
        "amount_centavos": payment.amount_centavos,
        "usage_centavos": payment.usage_centavos,
        "currency": payment.currency,
        "transferred_on": payment.transferred_on,
        "transfer_reference": payment.transfer_reference,
        "submitted_at": payment.submitted_at,
        "decided_at": payment.decided_at,
        "reject_reason": payment.reject_reason if rejected else None,
        "reject_note": payment.reject_note if rejected else None,
        "proof": {"content_type": payment.proof_content_type, "size_bytes": payment.proof_size},
        "cycle": cycle and {
            "sequence": cycle.sequence, "start_at": cycle.start_at, "end_at": cycle.end_at,
        },
    }


def _transfer_subscription(request, site_id):
    """The caller's site and its subscription, or 404: also while bank transfer is off."""
    site, subscription = _subscription_for(request, site_id)
    if not plan.collection_live():
        raise ApiError("not_found", "Not found.", status=404)
    return site, subscription


def _invalid(field, message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={field: [message]}
    )


@router.get("/sites/{site_id}/billing/payment/", response=PaymentOptionsOut)
def billing_payment_options(request, site_id: str):
    _, subscription = _transfer_subscription(request, site_id)
    options = services.payment_options(subscription)
    options["under_review"] = _payment(options["under_review"])
    options["latest"] = _payment(options["latest"])
    return options


@router.get("/sites/{site_id}/billing/payments/", response=PaymentsOut)
def billing_payments(request, site_id: str, limit: int = DEFAULT_LIMIT, cursor: str = ""):
    """Newest first. `cursor` is the id of the last payment already returned."""
    _, subscription = _transfer_subscription(request, site_id)
    rows = services.transfers_of(subscription).order_by("-pk")
    if cursor:
        last = rows.filter(public_id=cursor).first() if is_public_id(cursor, "btp") else None
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.")
        rows = rows.filter(pk__lt=last.pk)
    limit = max(1, min(limit, MAX_LIMIT))
    items = list(rows[: limit + 1])
    more = len(items) > limit
    items = items[:limit]
    return {
        "items": [_payment(item) for item in items],
        "next_cursor": items[-1].public_id if more else None,
    }


@router.post("/sites/{site_id}/billing/payments/", response={200: PaymentOut, 201: PaymentOut})
def billing_payment_submit(request, site_id: str):
    """A multipart form: `plan`, `transferred_on` (YYYY-MM-DD), `proof` (the file) and,
    optionally, `transfer_reference`. 201 with the payment; 200 with the payment already under
    review when the same file is sent again."""
    site, subscription = _transfer_subscription(request, site_id)
    hit("billing-proof", request.account.pk, limit=PROOF_LIMIT, window=PROOF_WINDOW)
    for name in [*request.POST, *request.FILES]:
        if name not in SUBMIT_FIELDS:
            raise _invalid(name, "This value is not accepted.")
    proof = request.FILES.get("proof")
    if proof is None:
        raise _invalid("proof", "A proof of the transfer is needed.")
    if proof.size > proofs.MAX_BYTES:
        raise _invalid("proof", "The file is too large.")
    sent_on = request.POST.get("transferred_on", "")
    try:
        transferred_on = date.fromisoformat(sent_on) if _ISO_DATE.match(sent_on) else None
    except ValueError:
        transferred_on = None
    payment, created = services.submit_transfer(
        subscription,
        request.account,
        plan_code=request.POST.get("plan"),
        content=proof.read(),
        transferred_on=transferred_on,
        transfer_reference=request.POST.get("transfer_reference", ""),
        renewal_consent=request.POST.get("renewal_consent"),
        consent_version=request.POST.get("consent_version"),
    )
    if created:
        record_event(
            request.account, "billing_proof_submitted", site=site.public_id,
            payment=payment.public_id,
        )
    return (201 if created else 200), _payment(payment)


@router.get("/sites/{site_id}/billing/payments/{payment_id}/", response=PaymentOut)
def billing_payment_detail(request, site_id: str, payment_id: str):
    _, subscription = _transfer_subscription(request, site_id)
    return _payment(services.transfer_of(subscription, payment_id))


@router.get("/sites/{site_id}/billing/payments/{payment_id}/proof/")
def billing_payment_proof(request, site_id: str, payment_id: str):
    """The file itself, to the Subscriber it belongs to only. It has no other address."""
    _, subscription = _transfer_subscription(request, site_id)
    content, content_type = services.proof_for_owner(subscription, payment_id)
    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="{PROOF_FILE_NAMES[content_type]}"'
    return response
