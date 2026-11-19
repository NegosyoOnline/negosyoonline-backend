"""What the buyer and the member are told about their registration and their mobile check (port of
TWCinc's negosyoonline_sante_pack/panels.py, the parts the onboarding and the member reads use).
The wording is TWCinc's."""
from apps.members import contacts, otp
from apps.pack import access, constants as C, interfaces, onboarding
from apps.pack.models import PackAgreementAcceptance, PackOrder, SanteRegistration

MEMBER_REG_LABELS = {
    "awaiting_buyer_details": "Details needed", "waiting_for_accept": "Details received",
    "ready_to_register": "Details received", "needs_buyer_action": "Action needed",
    "blocked": "On hold", "possible_existing_member": "On hold", "registered": "Registered",
    "cancelled": "Cancelled",
}
IN_PROGRESS = "Registration in progress"
MEMBER_REG_LINES = {
    "awaiting_buyer_details": "Add your details so the store can register your membership with Santé.",
    "waiting_for_accept": "The store registers your membership after your payment is verified.",
    "ready_to_register": "The store will register your membership with Santé.",
    "needs_buyer_action": "Please check your details and send them again.",
    "blocked": "The store is checking your registration and will contact you.",
    "possible_existing_member": "The store is checking your registration and will contact you.",
    "cancelled": "Your registration was cancelled. Contact the store if you have questions.",
}
IN_PROGRESS_LINE = "The store is registering your membership with Santé."
WAITING_LINE = "The store is checking your registration and will contact you."
REG_TONES = {
    "awaiting_buyer_details": "wait", "needs_buyer_action": "att", "blocked": "wait",
    "possible_existing_member": "wait", "registered": "ok", "cancelled": "no",
}
DETAILS_OPEN = ("awaiting_buyer_details", "needs_buyer_action")


def erased_hold(reg):
    """A kept pre-submission blocked or possible_existing_member row whose details were erased and
    not re-entered."""
    return (
        reg.status in ("blocked", "possible_existing_member") and reg.details_purged_at is not None
        and reg.details_confirmed_at is None
    )


def details_erased(reg):
    """The customer must enter the erased details again (the erase notice above the form)."""
    return (
        reg is not None and reg.details_purged_at is not None and reg.details_confirmed_at is None
        and reg.status in DETAILS_OPEN
    )


def member_reg_line(reg):
    return WAITING_LINE if erased_hold(reg) else MEMBER_REG_LINES.get(reg.status, IN_PROGRESS_LINE)


def latest_order(member):
    return PackOrder.objects.filter(member=member).order_by("-created_at", "-pk").first()


def _has_address(member):
    from apps.pack import registration
    return registration.has_delivery_address(member)


def registration_panel(member):
    """The member's registration card, or None when there is no registration."""
    reg = SanteRegistration.objects.filter(member=member).first()
    if reg is None:
        return None
    return {
        "status": reg.status, "label": MEMBER_REG_LABELS.get(reg.status, IN_PROGRESS),
        "line": member_reg_line(reg), "tone": REG_TONES.get(reg.status, "rev"),
        "details_form": reg.status in DETAILS_OPEN, "details_erased": details_erased(reg),
        "state_version": reg.state_version,
        # TWCinc shows the Santé member ID on the "registered" card; the Subscriber's "Mark
        # registered" records it (ClickUp z8uq9meyqj).
        "sante_member_id": reg.sante_member_id if reg.status == "registered" else "",
        # The details form asks for the delivery address while the member has none.
        # True only while the order has no Membership Terms acceptance: the details card then asks
        # the tick and posts it with the version id (the pack read's agreement.id).
        "terms_required": not PackAgreementAcceptance.objects.filter(order_id=reg.pack_order_id).exists(),
        "needs_address": reg.status in DETAILS_OPEN and not _has_address(member),
    }


def mobile_check(member):
    """The address step's code card: on until the buyer confirms the number."""
    if member.mobile_state != "pending":
        return {"pending": False}
    ch = onboarding.usable_challenge(member, otp.latest_open(
        member.site, purpose="onboarding", binding_raw=onboarding.member_binding(member)
    ))
    if access.used(member):  # the emailed link only proved the email: no change of number
        masked = contacts.mask("mobile", member.mobile)
        return {"pending": True, "locked": True, "mobile": masked, "code_sent_to": masked if ch is not None else ""}
    return {"pending": True, "mobile": member.mobile, "code_sent_to": ch.target if ch is not None else ""}


PACK_TONES = {
    "pending_review": "rev", "rejected": "att", "verified": "ok", "awaiting_xendit": "rev",
    "xendit_expired": "att", "xendit_mismatch": "rev",
}
METHOD_LABEL = C.METHOD_LABELS[C.PAYMENT_METHOD]


def method_label(order):
    return C.METHOD_LABELS.get(order.payment_method, METHOD_LABEL)


def payment_facts(order):
    """The fields every member-facing order carries for a Xendit-capable order: the method, the
    order's own invoice URL while it can still be paid (never a browser-supplied one), when Xendit
    confirmed it, and the server's amount."""
    from apps.pack import xendit
    return {
        "payment_method": order.payment_method, "payment_method_label": method_label(order),
        "pay_url": xendit.continue_url(order), "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "currency": order.currency,
    }


def lock_for(order):
    return _lock(order)


def _lock(order):
    """The rejection dialog's data: the reason (shown only after sign-in), whether a new proof is
    already in, and the accounts to pay to."""
    if not order.rejection_lock_active:
        return None
    from apps.pack import review
    from apps.sante import pack as sante_pack
    label, other = review.member_reason(order)
    return {
        "reason_label": label, "other_text": other,
        "resubmitted": order.payment_status == C.PENDING_REVIEW,
        "accounts": sante_pack.accounts(order.site),
    }


def pack_panel(member):
    """The member's payment card (TWCinc pack_panel, Xendit and shipment parts left out)."""
    order = latest_order(member)
    if order is None:
        return None
    return {
        "order_number": order.order_number, "payment_status": order.payment_status,
        "payment_label": dict(C.PAYMENT_STATUSES)[order.payment_status], "method_label": method_label(order),
        **payment_facts(order), "price_centavos": order.price_centavos, "tone": PACK_TONES.get(order.payment_status, "rev"),
        "lock": _lock(order), "benefits_off": not interfaces.benefits_active(member),
        "proofs": [
            {
                "id": s.public_id, "seq": s.seq, "submitted_at": s.submitted_at.isoformat(),
                "reference_number": s.reference_number, "amount_sent_centavos": s.amount_sent_centavos,
                "content_type": s.content_type,
            }
            for s in order.submissions.all()
        ],
    }
