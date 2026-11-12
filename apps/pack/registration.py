"""Santé registration up to the member signing in (port of TWCinc's negosyoonline_sante_pack/
registration.py). Only the edges that end with the member's own sign-in are carried: T1 at
placement, T3 or T4 when the buyer gives their details (T13 when a later slice asks again), T8
when the Subscriber approves the payment, and T24 is listed so the table stays TWCinc's. Nothing
here reads another site's rows. The Subscriber's registration workflow is a later slice."""
from django.db import transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.members import contacts, services as member_services
from apps.pack import address_zip, constants as C, records, registration_rules as rules
from apps.pack.models import PackAgreementAcceptance, PackOrder, SanteRegistration, SanteRegistrationEvent

READY = "ready_target"
EDGES = {  # edge: (from status or tuple, to status or READY)
    "T1": (None, "awaiting_buyer_details"),
    "T3": ("awaiting_buyer_details", "waiting_for_accept"),
    "T4": ("awaiting_buyer_details", "ready_to_register"),
    "T8": ("waiting_for_accept", "ready_to_register"),
    "T13": ("needs_buyer_action", READY),
    "T24": (("waiting_for_accept", "ready_to_register", "in_registration", "retry_wait"), "needs_buyer_action"),
}
SYSTEM = ("system", None, "")

WRONG_STATUS_MESSAGE = "This action is not available for this registration now."
DELIVERY_NAME_ERROR = "Enter the receiver's full name."
DELIVERY_FIELD_ERRORS = {
    "line": "Enter the address.", "province": "Select the province.",
    "city": "Select the city or municipality.", "barangay": "Select the barangay.",
    "landmark": "Enter a landmark, like the barangay hall.",
}
TERMS_ERROR = "Please agree to the Santé Membership Terms to continue."
MOBILE_FIRST = "Confirm your mobile number first."
ZIP_UNCONFIRMED = (
    "We could not confirm the ZIP code for your delivery address. Please contact the store."
)


def _wrong_status():
    return ApiError("wrong_status", WRONG_STATUS_MESSAGE, status=409)


def _event(reg, edge, from_status, to_status, *, actor, reason_code="", note=""):
    kind, ref, label = actor
    return SanteRegistrationEvent.objects.create(
        site_id=reg.site_id, registration=reg, edge=edge, from_status=from_status or "",
        to_status=to_status, reason_code=reason_code, note=note, actor_type=kind, actor_ref=ref,
        actor_label=label,
    )


def activate(order, now=None):
    """T1 at placement (direct, with its first proof). Idempotent."""
    now = now or timezone.now()
    reg, created = SanteRegistration.objects.get_or_create(
        site_id=order.site_id, member_id=order.member_id,
        defaults={"pack_order": order, "status": "awaiting_buyer_details", "status_entered_at": now},
    )
    if created:
        _event(reg, "T1", None, "awaiting_buyer_details", actor=SYSTEM)
    return reg


def _payment_ok(reg):
    return reg.pack_order.payment_status in C.PAYMENT_OK


def _from_ok(edge, status):
    src = EDGES[edge][0]
    return status in src if isinstance(src, tuple) else status == src


def _transition(reg, edge, *, actor, reason_code="", note="", now=None):
    if not _from_ok(edge, reg.status):
        raise _wrong_status()
    now, old, to = now or timezone.now(), reg.status, EDGES[edge][1]
    if to == READY:
        if reg.details_purged_at is not None and reg.details_confirmed_at is None:
            to = "needs_buyer_action"  # ready needs details; the edge and reason are kept
        else:
            to = "ready_to_register" if _payment_ok(reg) else "waiting_for_accept"
    if to != old:
        reg.status, reg.status_entered_at = to, now
    reg.state_version += 1
    reg.save()
    return _event(reg, edge, old, to, actor=actor, reason_code=reason_code, note=note)


def _require_open_details(reg):
    if reg.status not in ("awaiting_buyer_details", "needs_buyer_action"):
        raise _wrong_status()


def _apply_details(reg, cleaned, *, actor, reason_code, note=""):
    _require_open_details(reg)
    reg.date_of_birth, reg.desired_username = cleaned["date_of_birth"], cleaned["desired_username"]
    reg.alternate_username, reg.details_confirmed_at = cleaned["alternate_username"], timezone.now()
    edge = "T13" if reg.status == "needs_buyer_action" else ("T4" if _payment_ok(reg) else "T3")
    return _transition(reg, edge, actor=actor, reason_code=reason_code, note=note)


def clean_delivery(data):
    """The funnel's delivery step: who receives the pack, their mobile, and where. Stored with the
    five address keys the rest of the app reads, plus the receiver and the country."""
    data, errors = data or {}, {}
    out = {k: str(data.get(k) or "").strip()[:200] for k in ("line", "barangay", "city", "province", "landmark")}
    out["country"] = "Philippines"  # shown, never typed
    out["zip"] = ""
    for key, message in DELIVERY_FIELD_ERRORS.items():
        if not out[key]:
            errors[key] = message
    if not errors.keys() & {"province", "city", "barangay"}:
        # The ZIP is derived on the server from the chosen barangay; a typed one counts only when
        # the lookup cannot say.
        derived = address_zip.derive_address_fields(
            out["province"], out["city"], out["barangay"], data.get("postal_code") or data.get("zip") or ""
        )
        if derived.ok:
            out["zip"] = derived.zip_code or ""
        else:
            errors[derived.error_field] = derived.error_message
    out["receiver_name"] = str(data.get("receiver_name") or "").strip()
    if not 1 <= len(out["receiver_name"]) <= 120:
        errors["receiver_name"] = DELIVERY_NAME_ERROR
    try:
        out["receiver_mobile"] = contacts.clean_mobile(data.get("receiver_mobile"))
    except ApiError as exc:
        errors["receiver_mobile"] = ((exc.fields or {}).get("mobile") or ["Enter the receiver's mobile number."])[0]
    if errors:
        raise ApiError(
            "invalid_address", C.MSG_CHECK_FIELDS, status=422, fields={k: [v] for k, v in errors.items()}
        )
    return out


def save_delivery(site, member, order, data):
    """Keeps the cleaned delivery details on the order being shipped, and only there: the
    receiver may not be the subscriber, so neither the receiver nor the address goes onto the
    member's account."""
    cleaned = clean_delivery(data)
    with transaction.atomic():
        locked = PackOrder.objects.select_for_update().get(pk=order.pk, site=site)
        locked.delivery_address = cleaned
        locked.state_version += 1  # the owner's open order view goes stale, as for every other write
        locked.save(update_fields=["delivery_address", "state_version", "updated_at"])
    order.delivery_address, order.state_version = locked.delivery_address, locked.state_version
    return cleaned


def has_delivery_address(member):
    return bool(member.delivery_address) or any(
        o.delivery_address for o in PackOrder.objects.filter(member=member)
    )


HOME_PROVINCE_ERROR = "Enter the full province name, for example Camarines Sur."


def _held_address(order, member):
    """(holder, address) of the address on file: the order first, then the member's account,
    then another of the member's orders."""
    if order.delivery_address:
        return order, order.delivery_address
    if member.delivery_address:
        return member, member.delivery_address
    for other in PackOrder.objects.filter(member=member).exclude(pk=order.pk).order_by("-pk"):
        if other.delivery_address:
            return other, other.delivery_address
    return None, {}


def _confirm_stored_zip(order, member):
    """An address already on file reaches Santé with a derived or valid ZIP too, by the delivery
    step's rules: a ZIP the dataset can say wins over a differing held one, which is corrected; a
    held 4-digit ZIP the dataset cannot say stands; a blank or malformed one is derived again, and
    when nothing can give one the submission is refused. With no address anywhere the "address
    required" refusal holds."""
    holder, held = _held_address(order, member)
    if not held:
        raise ApiError(
            "invalid_address", C.MSG_CHECK_FIELDS, status=422, fields={"line": [DELIVERY_FIELD_ERRORS["line"]]}
        )
    derived = address_zip.derive_address_fields(held.get("province"), held.get("city"), held.get("barangay"), "")
    held_zip = str(held.get("zip") or "").strip()
    if derived.ok and derived.zip_precision in address_zip.AUTHORITATIVE:
        if held_zip == derived.zip_code:
            return
        new_zip = derived.zip_code  # the dataset wins over a differing held ZIP
    elif address_zip.is_valid_zip(held_zip):
        return
    elif derived.ok:
        new_zip = derived.zip_code
    else:
        raise ApiError("zip_unconfirmed", ZIP_UNCONFIRMED, status=422)
    fixed = {**held, "zip": new_zip}
    if isinstance(holder, PackOrder):
        locked = PackOrder.objects.select_for_update().get(pk=holder.pk)
        locked.delivery_address = fixed
        locked.state_version += 1
        locked.save(update_fields=["delivery_address", "state_version", "updated_at"])
        if holder is order:
            order.delivery_address, order.state_version = locked.delivery_address, locked.state_version
    else:
        member.delivery_address = fixed
        member.save(update_fields=["delivery_address"])


def _terms_note(order):
    """What the buyer agreed to, from the acceptance record (written at checkout by an older
    storefront, or at this step just before), never from a bare tick. Refuses when there is none."""
    row = PackAgreementAcceptance.objects.select_related("version").filter(order=order).first()
    if row is None:
        raise ApiError("terms_not_on_record", C.MSG_TERMS_NOT_ON_RECORD, status=409)
    accepted_on = timezone.localtime(row.accepted_at)  # the buyer's (Manila) calendar date
    return (
        f"Santé Membership Terms {row.version.version_label} accepted on "
        f"{accepted_on:%Y-%m-%d} (version {row.version_id}, sha256 {row.sha256[:12]})"
    )


def _derive_home_address(raw):
    """The dashboard card's five-key address, its ZIP derived on the server from the barangay
    through the pack's own dataset, as the funnel's delivery step does it: a typed ZIP counts only
    where the dataset cannot say. Refuses a missing line, city or province, an unknown province
    and a ZIP nothing can give."""
    cleaned = member_services.clean_address(raw or {})
    derived = address_zip.derive_address_fields(
        cleaned["province"], cleaned["city"], cleaned["barangay"], cleaned["zip"]
    )
    if not derived.ok:
        field = "zip" if derived.error_field == "postal_code" else derived.error_field
        message = HOME_PROVINCE_ERROR if field == "province" else derived.error_message
        raise ApiError("invalid_address", message, status=422, fields={field: [message]})
    return {**cleaned, "zip": derived.zip_code}


def _store_address(member, cleaned):
    member.delivery_address = cleaned
    member.save(update_fields=["delivery_address"])
    for order in PackOrder.objects.filter(member=member):
        if not order.delivery_address:
            order.delivery_address = cleaned
            order.save(update_fields=["delivery_address"])


def submit_details(site, member, *, date_of_birth, desired_username, alternate_username="",
                   delivery_address=None, terms_accepted=None, agreement_version_id=""):
    """The Santé details form. The mobile is confirmed first. `terms_accepted` is None where no
    tick is asked (the dashboard card); the funnel's form passes the tick and a missing one is
    refused. While the order has no acceptance on record, the funnel form also posts the version id
    its popup showed and the acceptance is written here, in the same transaction, before the note;
    an order that already has one (placed by an older storefront) writes no second row. The dashboard
    card (terms_accepted None) never writes one: an order with none is refused as not on record. The
    note names the Membership Terms version the buyer agreed to, read from the acceptance record. The dashboard card asks for the delivery address while
    the member has none (or when one is posted); the ZIP is derived and checked on the server."""
    if member.mobile_state == "pending":
        raise ApiError("mobile_not_verified", MOBILE_FIRST, status=409)
    posted = bool(delivery_address and any(str(v or "").strip() for v in delivery_address.values()))
    address = _derive_home_address(delivery_address) if posted or not has_delivery_address(member) else None
    try:
        cleaned, fields = rules.clean_details(
            date_of_birth=date_of_birth, desired_username=desired_username,
            alternate_username=alternate_username,
        ), {}
    except ApiError as exc:
        cleaned, fields = None, dict(exc.fields or {})
    if terms_accepted is False:
        fields["terms"] = [TERMS_ERROR]
    if fields:
        raise ApiError("invalid_details", C.MSG_CHECK_FIELDS, status=422, fields=fields)
    with transaction.atomic():
        order_id = (
            SanteRegistration.objects.filter(site=site, member=member)
            .values_list("pack_order_id", flat=True).first()
        )
        if order_id is None:
            raise ApiError("not_found", "Not found.", status=404)
        PackOrder.objects.select_for_update().filter(pk=order_id).first()  # order, then registration
        reg = (
            SanteRegistration.objects.select_for_update().select_related("pack_order")
            .filter(site=site, member=member).first()
        )
        if reg is None:
            raise ApiError("not_found", "Not found.", status=404)
        _require_open_details(reg)  # a wrong status answers first
        order = reg.pack_order
        if terms_accepted and not PackAgreementAcceptance.objects.filter(order=order).exists():
            records.accept_at_details(order, agreement_version_id)
        note = _terms_note(order)
        if address is None:
            _confirm_stored_zip(order, member)
        _apply_details(
            reg, cleaned, actor=("member", member.pk, contacts.mask("mobile", member.mobile)),
            reason_code="buyer", note=note,
        )
        if address is not None:
            _store_address(member, address)
    return reg


def on_payment_verified(order):
    """T8: an approval moves a waiting registration to ready_to_register. Inside the approval
    transaction, after the order row lock (lock order: order, then registration)."""
    reg = (
        SanteRegistration.objects.select_for_update().select_related("pack_order")
        .filter(pack_order=order).first()
    )
    if reg is not None and reg.status == "waiting_for_accept":
        _transition(reg, "T8", actor=SYSTEM)
