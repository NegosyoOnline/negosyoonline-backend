"""Shipment by hand (port of the behaviour of TWCinc's negosyoonline_orders/fulfillment.py, no
courier API): the Subscriber who owns the site records the courier, tracking number and shipped
date of a verified order, corrects them, and marks delivery. Each step locks the order row, checks
state_version, then the rule (after the lock, so a racing approval is settled), writes the fields,
bumps state_version and writes an append-only event. There is no "packed" step, no undo of shipped
and delivery is final. The status is derived from the fields; it is never a payment status."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.pack import constants as C, notices
from apps.pack.review import _lock, _save, subscriber_actor


def manila_today():
    return timezone.now().astimezone(ZoneInfo(settings.TIME_ZONE)).date()


def _date(value):
    """None for blank, False for an unreadable value, else a date."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return False


def _clean(courier, other, tracking, shipped_on):
    errors = {}
    courier, other, tracking = (courier or "").strip(), (other or "").strip(), (tracking or "").strip()
    if courier not in C.COURIER_LABELS:
        errors["courier"] = [C.MSG_SHIP["courier"]]
    if courier == C.COURIER_OTHER and not 1 <= len(other) <= 60:
        errors["courier_other_name"] = [C.MSG_SHIP["courier_other_name"]]
    if not 1 <= len(tracking) <= 64:
        errors["tracking_number"] = [C.MSG_SHIP["tracking_number"]]
    shipped = _date(shipped_on)
    shipped = manila_today() if shipped is None else shipped
    if shipped is False or shipped > manila_today():
        errors["shipped_on"] = [C.MSG_SHIP["shipped_on"]]
    if errors:
        raise ApiError("invalid_shipment", C.MSG_CHECK_SHIPMENT, status=422, fields=errors)
    return {
        "courier": courier, "courier_other_name": other if courier == C.COURIER_OTHER else "",
        "tracking_number": tracking, "shipped_on": shipped,
    }


def _snapshot(order):
    return {
        "courier": order.courier, "courier_other_name": order.courier_other_name,
        "tracking_number": order.tracking_number, "shipped_on": str(order.shipped_on or ""),
    }


def record_shipment(account, site, order_id, *, state_version, courier, tracking_number,
                    courier_other_name="", shipped_on=None):
    data = _clean(courier, courier_other_name, tracking_number, shipped_on)
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        if order.payment_status != C.VERIFIED:
            raise ApiError("wrong_status", C.MSG_NOT_SHIPPABLE, status=409)
        if order.shipment_recorded_at is not None:
            raise ApiError("already_shipped", C.MSG_ALREADY_SHIPPED, status=409)
        for key, value in data.items():
            setattr(order, key, value)
        order.shipment_recorded_at = timezone.now()
        order.shipment_actor_ref, order.shipment_actor_label = ref, label
        _save(order, [*data, "shipment_recorded_at", "shipment_actor_ref", "shipment_actor_label"])
        order.add_event(
            C.EVENT_SHIPMENT_RECORDED, new=_snapshot(order), actor_type=kind, actor_ref=ref, actor_label=label,
        )
        notices.after_commit(notices.order_shipped, order.pk)
    return order


def correct_shipment(account, site, order_id, *, state_version, courier, tracking_number,
                     courier_other_name="", shipped_on=None):
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        if order.delivery_confirmed_at is not None:
            raise ApiError("delivered_final", C.MSG_DELIVERED_FINAL, status=409)
        if order.shipment_recorded_at is None:
            raise ApiError("not_shipped", C.MSG_NOT_SHIPPED, status=409)
        data = _clean(courier, courier_other_name, tracking_number, shipped_on or order.shipped_on)
        old = _snapshot(order)
        for key, value in data.items():
            setattr(order, key, value)
        _save(order, list(data))
        order.add_event(
            C.EVENT_SHIPMENT_CORRECTED, old=old, new=_snapshot(order),
            actor_type=kind, actor_ref=ref, actor_label=label,
        )
    return order


def confirm_delivery(account, site, order_id, *, state_version, delivered_on=None):
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        if order.shipment_recorded_at is None:
            raise ApiError("not_shipped", C.MSG_NOT_SHIPPED, status=409)
        if order.delivery_confirmed_at is not None:
            raise ApiError("delivered_final", C.MSG_ALREADY_DELIVERED, status=409)
        day = _date(delivered_on)
        day = manila_today() if day is None else day
        if day is False or day < order.shipped_on or day > manila_today():
            raise ApiError(
                "invalid_delivery", C.MSG_CHECK_SHIPMENT, status=422,
                fields={"delivered_on": [C.MSG_SHIP["delivered_on"]]},
            )
        order.delivered_on, order.delivery_confirmed_at = day, timezone.now()
        order.delivery_actor_ref, order.delivery_actor_label = ref, label
        _save(order, ["delivered_on", "delivery_confirmed_at", "delivery_actor_ref", "delivery_actor_label"])
        order.add_event(
            C.EVENT_DELIVERED, new={"delivered_on": str(day), "source": "manual"},
            actor_type=kind, actor_ref=ref, actor_label=label,
        )
        notices.after_commit(notices.order_delivered, order.pk)
    return order
