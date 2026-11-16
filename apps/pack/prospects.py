"""The Subscriber's prospect list (ClickUp z8uq9mfw14, under z8uq9meyqk): the people who placed a
Membership Pack order on one Santé site or wrote to it, newest activity first. Read-only, from rows
that already exist; stored inquiry rows only, no provider is ever called. Every query is filtered by
the site. A row carries a name, a mobile and an email, in full, where one is already stored (Coach Ed,
2026-11-16, D2): an order the buyer's, a visit the contact captured on blur, a stored message the
email the person gave (no phone is stored there). Never an address, proof, date of birth or Meta data.
An order also carries what the Orders page shows (date, payment status, amount) for its summary popup.
Order, visit and inquiry rows are never matched by name.

Layout adapted from TWCinc partnership/views/lists.py (ProspectListDataView) and partnership/stages.py
(one furthest-step stage per row, counts per stage taken before search and stage narrowing),
and funnels/models.py Prospect (the visit).
"""
from django.db.models import Max

from apps.inquiries.models import InquiryConversation
from apps.members.models import MemberSession
from apps.pack.models import PackOrder, PackProofSubmission, PackVisit, SanteRegistration

PAGE_SIZE = 25
# TWCinc's eight stages (partnership/stages.py), in funnel order. A row reports the furthest it reached.
# Mapping here: a message to the site is New Lead; a visit is Landing Page, then Start Watching and Done
# Watching by the video, then Add to Cart once the checkout step was opened or a contact saved; an
# order placed (with or without a proof) is Checked Out; an approved payment is Account Setup (with
# the registration status beside it); a member who signed in to the dashboard is Activated.
STAGES = (
    "new_lead", "landing_page", "start_watching", "done_watching", "added_to_cart", "checked_out",
    "account_setup", "activated",
)
CHANNEL_KEYS = {
    InquiryConversation.PANCAKE: "facebook",
    InquiryConversation.WEBCHAT: "webchat",
    InquiryConversation.EMAIL: "email",
}
NO_ORDER = {"placed_at": None, "payment_status": None, "price_centavos": None, "currency": None}
PAGE_SOURCE = "pack_page"  # the Membership Pack page: an order or a visit


def _latest(*values, default=None):
    return max((v for v in values if v is not None), default=default)


def order_rows(site):
    orders = list(PackOrder.objects.filter(site=site).values(
        "pk", "public_id", "order_number", "buyer_name", "member_id", "created_at", "verified_at",
        "payment_status", "price_centavos", "currency", "member__mobile", "member__email"))
    pks = [o["pk"] for o in orders]
    proofs = dict(
        PackProofSubmission.objects.filter(site=site, order_id__in=pks).values_list("order_id")
        .annotate(at=Max("submitted_at")))
    registrations = {
        r["pack_order_id"]: r for r in SanteRegistration.objects.filter(site=site, pack_order_id__in=pks)
        .values("pack_order_id", "status", "status_entered_at")}
    sessions = dict(
        MemberSession.objects.filter(site=site, member_id__in={o["member_id"] for o in orders})
        .values_list("member_id").annotate(at=Max("created_at")))
    video = dict(
        PackVisit.objects.filter(site=site, order_id__in=pks).values_list("order_id").annotate(p=Max("video_percent")))
    rows = []
    for o in orders:
        stage, last, status = "checked_out", o["created_at"], None
        if o["pk"] in proofs:
            last = _latest(last, proofs[o["pk"]])
        if o["verified_at"] is not None:
            stage, last = "account_setup", _latest(last, o["verified_at"])
            reg = registrations.get(o["pk"])
            # A registration is opened with the order; it counts as a step once the payment is approved.
            if reg is not None:
                status, last = reg["status"], _latest(last, reg["status_entered_at"])
            if o["member_id"] in sessions:
                stage, last = "activated", _latest(last, sessions[o["member_id"]])
        rows.append({
            "kind": "order", "name": o["buyer_name"], "order_number": o["order_number"], "order_id": o["public_id"],
            "source": PAGE_SOURCE, "stage": stage, "registration_status": status, "last_activity": last,
            "video_percent": video.get(o["pk"]),  # None: no linked visit, unknown
            "mobile": o["member__mobile"], "email": o["member__email"], "placed_at": o["created_at"].isoformat(),
            "payment_status": o["payment_status"], "price_centavos": o["price_centavos"], "currency": o["currency"],
        })
    return rows


def visit_stage(v):
    if v["checkout_started_at"] is not None or v["contact_saved_at"] is not None:
        return "added_to_cart"
    if v["done_watching_at"] is not None:
        return "done_watching"
    if v["started_watching_at"] is not None or v["video_percent"] > 0:
        return "start_watching"
    return "landing_page"


def visit_rows(site):
    """Visits that have not placed an order. An unfinished checkout shows the contact as typed, in full."""
    rows = []
    for v in PackVisit.objects.filter(site=site, order__isnull=True).values(
            "contact_name", "contact_mobile", "contact_email", "video_percent", "started_watching_at",
            "done_watching_at", "checkout_started_at", "contact_saved_at", "last_activity_at"):
        rows.append({
            "kind": "visit", "name": v["contact_name"], "order_number": None, "order_id": None,
            "source": PAGE_SOURCE, "stage": visit_stage(v), "registration_status": None,
            "last_activity": v["last_activity_at"], "video_percent": v["video_percent"],
            "mobile": v["contact_mobile"], "email": v["contact_email"], **NO_ORDER,
        })
    return rows


def inquiry_rows(site):
    rows = []
    for c in InquiryConversation.objects.filter(site=site).values(
            "provider", "display_name", "contact_name", "contact_email", "last_message_at", "last_inbound_at", "created_at"):
        # A stored channel keeps the name in contact_name; read live, so a blanked name stays blank.
        name = c["contact_name"] if c["provider"] in InquiryConversation.STORED_PROVIDERS else c["display_name"]
        rows.append({
            "kind": "inquiry", "name": name, "order_number": None, "order_id": None,
            "source": CHANNEL_KEYS.get(c["provider"], "facebook"), "stage": "new_lead",
            "registration_status": None,
            "last_activity": _latest(c["last_message_at"], c["last_inbound_at"], default=c["created_at"]),
            "video_percent": None, "mobile": "",  # no phone is stored on a conversation
            "email": c["contact_email"] if c["provider"] in InquiryConversation.STORED_PROVIDERS else "", **NO_ORDER,
        })
    return rows


def _matches(row, q):
    if q in row["name"].lower():
        return True
    if row["order_number"] is not None and q in row["order_number"].lower():
        return True
    return q in row["mobile"].lower() or q in row["email"].lower()


def prospects(site, *, stage="", q="", page=1):
    rows = order_rows(site) + visit_rows(site) + inquiry_rows(site)
    stage_counts = {"all": len(rows), **{s: 0 for s in STAGES}}
    for r in rows:
        stage_counts[r["stage"]] += 1
    q = q.strip().lower()
    if q:
        rows = [r for r in rows if _matches(r, q)]
    if stage:
        rows = [r for r in rows if r["stage"] == stage]
    rows.sort(key=lambda r: (r["last_activity"], r["order_id"] or "", r["kind"]), reverse=True)
    total = len(rows)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(page, pages)
    window = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    for r in window:
        r["last_activity"] = r["last_activity"].isoformat()
    return {"page": page, "page_size": PAGE_SIZE, "pages": pages, "total": total, "stage_counts": stage_counts,
            "results": window}
