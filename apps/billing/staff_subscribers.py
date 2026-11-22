"""The Subscribers routes: where HQ staff see who signed up and where each one stands.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps. A handler answers as
if it did not exist while HQ_SUBSCRIBERS_ENABLED is off, and reads nothing then.

Read only, except Disable and Re-enable of a Site's own SMS line (`subscribers/sms-line/...`),
which need SMS_SITE_LINES_ENABLED as well, a reason and a fresh authenticator code. One row is one Subscriber site: the site, its billing subscription and the owner's
sign-in email. Nothing of a store's own customers is read here: no members, orders, chats or
customer mobiles. Every route is a POST with a small JSON body, as the Finance routes are.
"""
import json
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.utils import timezone

from apps.billing.models import BankTransferPayment, BillingCycle, Subscription
from apps.common.errors import ApiError, error_body
from apps.common.ids import is_public_id
from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsLineDisable
from apps.operators import audit, two_step
from apps.sites import custom_hosts
from apps.sites.models import Site, SiteCustomHost, Subdomain

MAX_BODY = 2048
PAGE_SIZE = 50
TRIAL_ENDING_DAYS = 7
MAX_QUERY = 100
FILTERS = ("all", "trial_ending", "unpaid", "suspended_ended")
AUDIT_TARGET = "subscriber_site"
State = Subscription.State


def _now():
    return timezone.now()


def _iso(value):
    return value.isoformat() if value is not None else None


def _invalid(field, message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={field: [message]}
    )


def _body(request):
    """The JSON object sent, or {} when there is none, it is too large or it is not one."""
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_BODY:
            return {}
        data = json.loads(request.body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _route(view=None, *, needs_lines=False):
    if view is None:
        return lambda inner: _route(inner, needs_lines=needs_lines)

    def handler(request, operator):
        try:
            if not settings.HQ_SUBSCRIBERS_ENABLED or (needs_lines and not sms.site_lines_on()):
                raise ApiError("not_found", "Not found.", status=404)
            response = view(_body(request), operator)
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


def _sites():
    """Every live site, with the latest billing cycle's end and plan read in the same query."""
    latest = BillingCycle.objects.filter(subscription=OuterRef("subscription")).order_by("-sequence")
    return (
        Site.objects.filter(deleted_at__isnull=True)
        .select_related("account", "subscription")
        .annotate(
            latest_cycle_end=Subquery(latest.values("end_at")[:1]),
            latest_plan_code=Subquery(
                BillingCycle.objects.filter(subscription=OuterRef("subscription"))
                .exclude(plan_code="")
                .order_by("-sequence")
                .values("plan_code")[:1]
            ),
            last_payment=Subquery(
                BankTransferPayment.objects.filter(
                    subscription=OuterRef("subscription"), status=BankTransferPayment.Status.CONFIRMED
                )
                .order_by("-decided_at", "-pk")
                .values("decided_at")[:1]
            ),
        )
    )


def _filtered(rows, name, now):
    if name == "trial_ending":
        return rows.filter(
            subscription__state=State.TRIAL_ACTIVE,
            subscription__trial_end_at__lte=now + timedelta(days=TRIAL_ENDING_DAYS),
        )
    if name == "unpaid":
        # Payment needs attention, or the date it was due has passed and the sweep has not
        # moved it yet.
        return rows.filter(
            Q(subscription__state=State.PAYMENT_REQUIRES_ATTENTION)
            | Q(subscription__state=State.TRIAL_ACTIVE, subscription__trial_end_at__lt=now)
            | Q(subscription__state=State.ACTIVE, latest_cycle_end__lt=now)
        )
    if name == "suspended_ended":
        return rows.filter(
            subscription__state__in=(
                State.SUSPENDED, State.CANCELLED, State.CANCELLED_NO_PAYMENT_METHOD
            )
        )
    return rows


def _searched(rows, text):
    text = text.strip()
    parent = settings.SITE_PARENT_HOST
    if parent and text.lower().endswith("." + parent.lower()):
        subdomain_text = text[: -(len(parent) + 1)]
    else:
        subdomain_text = text
    return rows.filter(
        Q(display_name__icontains=text)
        | Q(account__email__icontains=text)
        | Exists(Subdomain.objects.filter(site=OuterRef("pk"), name__icontains=subdomain_text))
        | Exists(SiteCustomHost.objects.filter(site=OuterRef("pk"), host__icontains=text))
    )


def _next_due_at(site):
    subscription = site.subscription
    if subscription.state == State.TRIAL_ACTIVE:
        return subscription.trial_end_at
    if subscription.state == State.ACTIVE:
        return site.latest_cycle_end
    if subscription.state == State.PAYMENT_REQUIRES_ATTENTION:
        return subscription.attention_started_at
    return None


def _subscription_of(site):
    try:
        return site.subscription
    except Subscription.DoesNotExist:
        return None


def _row(site):
    """One subscriber as the list and the detail show it. A site with no subscription row
    has a null state and null dates: it is still listed."""
    subscription = _subscription_of(site)
    return {
        "site_id": site.public_id,
        "business_name": site.display_name,
        "site_address": custom_hosts.public_host(site),
        "template": site.template,
        "plan": site.latest_plan_code if subscription else None,
        "state": subscription.state if subscription else None,
        "trial_ends_at": _iso(subscription.trial_end_at) if subscription else None,
        "next_due_at": _iso(_next_due_at(site)) if subscription else None,
        "last_payment_at": _iso(site.last_payment) if subscription else None,
        "owner_email": site.account.email,
        "created_at": _iso(site.created_at),
    }


def _payment(payment):
    """A payment as the Subscribers detail lists it. Never where the file is kept or its hash,
    and none of the sender's details."""
    return {
        "id": payment.public_id,
        "status": payment.status,
        "plan": payment.plan_code,
        "amount_centavos": payment.amount_centavos,
        "amount_received_centavos": payment.amount_received_centavos,
        "currency": payment.currency,
        "transferred_on": payment.transferred_on.isoformat(),
        "submitted_at": _iso(payment.submitted_at),
        "decided_at": _iso(payment.decided_at),
    }


@_route
def listing(body, operator):
    name = body.get("filter", "all")
    if name not in FILTERS:
        raise _invalid("filter", "Choose one of the listed filters.")
    text = body.get("q")
    if text is not None and (not isinstance(text, str) or len(text) > MAX_QUERY):
        raise _invalid("q", "Enter up to 100 characters.")
    cursor = body.get("cursor") or ""
    if not isinstance(cursor, str):
        raise ApiError("invalid_cursor", "That page marker is not valid.")

    rows = _filtered(_sites(), name, _now())
    if text and text.strip():
        rows = _searched(rows, text)
    rows = rows.order_by("-created_at", "-pk")
    if cursor:
        last = Site.objects.filter(public_id=cursor).first() if is_public_id(cursor, "site") else None
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.")
        rows = rows.filter(
            Q(created_at__lt=last.created_at) | Q(created_at=last.created_at, pk__lt=last.pk)
        )
    items = list(rows[: PAGE_SIZE + 1])
    more = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    return JsonResponse({
        "rows": [_row(site) for site in items],
        "next": items[-1].public_id if more else None,
    })


@_route
def detail(body, operator):
    site_id = body.get("site_id")
    if not isinstance(site_id, str) or not site_id:
        raise _invalid("site_id", "Name the site.")
    site = _sites().filter(public_id=site_id).first()
    if site is None:
        raise ApiError("not_found", "No such subscriber.", status=404)
    subscription = _subscription_of(site)
    payments = (
        list(subscription.transfers.order_by("-submitted_at", "-pk")) if subscription else []
    )
    # Ids and counts only: the audit entry holds no email, host or amount.
    audit.record(
        operator, "hq_subscriber_viewed", target_type=AUDIT_TARGET, target_id=site.public_id,
        detail={"payments": len(payments)},
    )
    return JsonResponse({
        **_row(site), "payments": [_payment(p) for p in payments], "sms_line": _line_state(site),
    })


# HQ's Disable / Re-enable of a Site's own phone line (SMS-INCLUDED). The row is `SmsLineDisable`;
# `sms.site_has_line` is what reads it.

MAX_REASON = 300
REAUTH_MESSAGE = "That code did not work. Enter the current code from your authenticator app."


def _active_disable(site):
    return SmsLineDisable.objects.filter(site=site, reenabled_at__isnull=True).first()


def _line_state(site):
    """The Site's line as HQ sees it, or None while SMS_SITE_LINES_ENABLED is off. No token, hash,
    phone name or number."""
    if not sms.site_lines_on():
        return None
    disable = _active_disable(site)
    return {
        "has_line": sms.site_has_line(site),
        "has_phone": SmsDevice.objects.filter(site=site, is_active=True).exists(),
        "disabled": disable is not None,
        "reason": disable.reason if disable else None,
        "disabled_at": _iso(disable.disabled_at) if disable else None,
    }


def _line_site(body):
    site_id = body.get("site_id")
    if not isinstance(site_id, str) or not site_id:
        raise _invalid("site_id", "Name the site.")
    site = Site.objects.filter(public_id=site_id, deleted_at__isnull=True).first()
    if site is None:
        raise ApiError("not_found", "No such subscriber.", status=404)
    return site


def _reason(body):
    reason = body.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise _invalid("reason", "Give a reason.")
    if len(reason) > MAX_REASON:
        raise _invalid("reason", "Use up to 300 characters.")
    return reason


def _fresh_code(body, operator):
    """Refuse unless the body holds a fresh 6-digit authenticator code: one message for a wrong,
    stale, reused or locked code and for a recovery code. The failure is audited with counts only."""
    identity = operator.admin_identities.filter(is_active=True).first()
    result = (
        two_step.verify_fresh_totp(identity.pk, body.get("code"), surface=two_step.SURFACE_HQ)
        if identity is not None
        else two_step.Result(ok=False)
    )
    if result.ok:
        return
    identity = operator.admin_identities.filter(is_active=True).first()
    audit.record(
        operator, "hq_sms_line_reauth_failed", target_type=AUDIT_TARGET,
        detail={
            "failed_attempts": identity.failed_attempts if identity else 0,
            "locked": bool(result.locked),
        },
    )
    raise ApiError("reauth_failed", REAUTH_MESSAGE, status=403)


@_route(needs_lines=True)
def line_disable(body, operator):
    site = _line_site(body)
    reason = _reason(body)
    if _active_disable(site) is not None:
        raise ApiError("already_disabled", "This line is already disabled.", status=409)
    _fresh_code(body, operator)
    try:
        with transaction.atomic():
            SmsLineDisable.objects.create(site=site, disabled_by=operator, reason=reason)
            audit.record(
                operator, "hq_sms_line_disabled", target_type=AUDIT_TARGET,
                target_id=site.public_id, reason=reason,
            )
    except IntegrityError:  # a second Disable raced this one
        raise ApiError("already_disabled", "This line is already disabled.", status=409)
    return JsonResponse({"sms_line": _line_state(site)})


@_route(needs_lines=True)
def line_reenable(body, operator):
    site = _line_site(body)
    reason = _reason(body)
    if _active_disable(site) is None:
        raise ApiError("not_disabled", "This line is not disabled.", status=409)
    _fresh_code(body, operator)
    with transaction.atomic():
        changed = SmsLineDisable.objects.filter(site=site, reenabled_at__isnull=True).update(
            reenabled_at=_now()
        )
        if not changed:
            raise ApiError("not_disabled", "This line is not disabled.", status=409)
        audit.record(
            operator, "hq_sms_line_reenabled", target_type=AUDIT_TARGET,
            target_id=site.public_id, reason=reason,
        )
    return JsonResponse({"sms_line": _line_state(site)})


ROUTES = {
    "subscribers/list/": listing,
    "subscribers/detail/": detail,
    "subscribers/sms-line/disable/": line_disable,
    "subscribers/sms-line/reenable/": line_reenable,
}
