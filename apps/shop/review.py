"""The Subscriber's order actions (TWCinc negosyoonline_storefront/actions.py): confirm that the
payment arrived, cancel with a note, mark shipped, mark delivered. There is no reject and no
resubmit: a cancelled buyer places a new order.

Every action locks the order row, checks state_version (409 `stale`), checks the state (409
`wrong_status`), bumps state_version and writes a ShopOrderEvent, all in one transaction. The
buyer's email is sent after commit. Buyers have no action. The Subscriber who owns the site
acts, never staff."""
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.shop import constants as C, notices
from apps.shop.models import ShopOrder


def subscriber_actor(account):
    return ("subscriber", account.pk, account.email)


def _lock(site, order_id, state_version):
    order = ShopOrder.objects.select_for_update().filter(site=site, pk=order_id).first()
    if order is None:
        raise ApiError("not_found", "Not found.", status=404)
    if state_version != order.state_version:
        raise ApiError("stale", C.MSG_STALE, status=409)
    return order


def _wrong_status():
    return ApiError("wrong_status", C.MSG_WRONG_STATUS, status=409)


def _save(order, fields):
    order.state_version += 1
    order.save(update_fields=[*fields, "state_version", "updated_at"])


def today():
    return timezone.localdate()


def _date(value, field):
    """A date no later than today; None means today."""
    if value is None:
        return today()
    if not isinstance(value, date) or value > today():
        raise ApiError("invalid_date", C.MSG_BAD_DATE, status=422, fields={field: [C.MSG_BAD_DATE]})
    return value


def confirm_receipt(account, site, order_id, *, state_version):
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        if order.payment_status != C.PENDING_REVIEW:
            raise _wrong_status()
        order.payment_status, order.verified_at = C.VERIFIED, timezone.now()
        _save(order, ["payment_status", "verified_at"])
        order.add_event(C.EVENT_VERIFIED, actor_type=kind, actor_ref=ref, actor_label=label)
        notices.after_commit(notices.payment_verified, order.pk)
    return order


def cancel(account, site, order_id, *, state_version, note):
    note = (note or "").strip()
    if not 1 <= len(note) <= C.NOTE_MAX:
        raise ApiError(
            "note_required", C.MSG_NOTE_REQUIRED, status=422, fields={"note": [C.MSG_NOTE_REQUIRED]}
        )
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        if order.payment_status != C.PENDING_REVIEW:  # only before the payment is confirmed
            raise _wrong_status()
        order.payment_status, order.cancelled_at, order.cancel_note = C.CANCELLED, timezone.now(), note
        _save(order, ["payment_status", "cancelled_at", "cancel_note"])
        order.add_event(C.EVENT_CANCELLED, note=note, actor_type=kind, actor_ref=ref, actor_label=label)
        notices.after_commit(notices.order_cancelled, order.pk)
    return order


def ship(account, site, order_id, *, state_version, courier, tracking_no="", shipped_on=None):
    courier = " ".join((courier or "").split())
    tracking_no = (tracking_no or "").strip()
    if not 1 <= len(courier) <= C.COURIER_MAX:
        raise ApiError(
            "courier_required", C.MSG_COURIER, status=422, fields={"courier": [C.MSG_COURIER]}
        )
    if len(tracking_no) > C.TRACKING_MAX:
        raise ApiError(
            "invalid_tracking", C.MSG_COURIER, status=422, fields={"tracking_no": ["Tracking number is too long."]}
        )
    shipped_on = _date(shipped_on, "shipped_on")
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        if order.payment_status != C.VERIFIED or order.shipped_on is not None:
            raise _wrong_status()
        order.courier, order.tracking_no, order.shipped_on = courier, tracking_no, shipped_on
        _save(order, ["courier", "tracking_no", "shipped_on"])
        order.add_event(C.EVENT_SHIPPED, actor_type=kind, actor_ref=ref, actor_label=label)
        notices.after_commit(notices.order_shipped, order.pk)
    return order


def deliver(account, site, order_id, *, state_version, delivered_on=None):
    delivered_on = _date(delivered_on, "delivered_on")
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        if order.shipped_on is None or order.delivered_on is not None:
            raise _wrong_status()
        if delivered_on < order.shipped_on:
            raise ApiError(
                "invalid_date", C.MSG_SHIP_DATE_ORDER, status=422,
                fields={"delivered_on": [C.MSG_SHIP_DATE_ORDER]},
            )
        order.delivered_on = delivered_on
        _save(order, ["delivered_on"])
        order.add_event(C.EVENT_DELIVERED, actor_type=kind, actor_ref=ref, actor_label=label)
        notices.after_commit(notices.order_delivered, order.pk)
    return order
