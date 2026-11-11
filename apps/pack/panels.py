"""What the buyer and the member are told about their registration and their mobile check (port of
TWCinc's negosyoonline_sante_pack/panels.py, the parts the onboarding and the member reads use).
The wording is TWCinc's."""
from apps.members import contacts, otp
from apps.pack import access, onboarding
from apps.pack.models import PackOrder, SanteRegistration

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
