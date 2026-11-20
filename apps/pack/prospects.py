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
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission, PackVisit, SanteRegistration

PAGE_SIZE = 25
# Six stages, in funnel order (Coach Ed, 2026-11-21, ruling 1100430000601591). A row reports the furthest
# it reached. A visit with no order is Landing Page, Start Watching or Done Watching by the video; an
# inquiry that is not merged into a visit is Landing Page. An order is Checked Out until its payment
# details are in (bank: a proof was submitted, any review outcome; Xendit: paid), then Purchased, then
# Activated once the buyer finished onboarding (a member session was issued, or the checkout token
# says it was), with the registration status beside it.
STAGES = ("landing_page", "start_watching", "done_watching", "checked_out", "purchased", "activated")
CHANNEL_KEYS = {
    InquiryConversation.WEBCHAT: "webchat",
    InquiryConversation.EMAIL: "email",
    InquiryConversation.MESSENGER: "messenger",
}
NO_ORDER = {"placed_at": None, "payment_status": None, "price_centavos": None, "currency": None}
PAGE_SOURCE = "pack_page"  # the Membership Pack page: an order or a visit


def _latest(*values, default=None):
    return max((v for v in values if v is not None), default=default)


def order_rows(site):
    orders = list(PackOrder.objects.filter(site=site).values(
        "pk", "public_id", "order_number", "buyer_name", "member_id", "created_at", "verified_at",
        "payment_status", "payment_method", "paid_at", "price_centavos", "currency", "member__mobile", "member__email", "buyer_mobile", "buyer_email"))
    pks = [o["pk"] for o in orders]
    proofs = dict(
        PackProofSubmission.objects.filter(site=site, order_id__in=pks).values_list("order_id")
        .annotate(at=Max("submitted_at")))
    registrations = {
        r["pack_order_id"]: r for r in SanteRegistration.objects.filter(site=site, pack_order_id__in=pks)
        .values("pack_order_id", "status", "status_entered_at")}
    sessions = dict(
        MemberSession.objects.filter(site=site, member_id__in={o["member_id"] for o in orders if o["member_id"]})
        .values_list("member_id").annotate(at=Max("created_at")))
    issued = set(
        CheckoutToken.objects.filter(site=site, order_ref__in=pks, session_issued_at__isnull=False)
        .values_list("order_ref", flat=True))
    video = dict(
        PackVisit.objects.filter(site=site, order_id__in=pks).values_list("order_id").annotate(p=Max("video_percent")))
    rows = []
    for o in orders:
        stage, last, status = "checked_out", o["created_at"], None
        paid = o["paid_at"] is not None or o["verified_at"] is not None
        if o["pk"] in proofs:
            last = _latest(last, proofs[o["pk"]])
        if o["pk"] in proofs or paid:
            stage = "purchased"
        if o["verified_at"] is not None:
            last = _latest(last, o["verified_at"])
            reg = registrations.get(o["pk"])
            # A registration is opened with the order; it counts as a step once the payment is approved.
            if reg is not None:
                status, last = reg["status"], _latest(last, reg["status_entered_at"])
        # Activated: onboarding finished. The member's session is the durable marker; the checkout
        # token's once-only issue time is the same fact read from the order's side.
        if o["member_id"] in sessions and paid:
            stage, last = "activated", _latest(last, sessions[o["member_id"]])
        elif o["pk"] in issued:
            stage = "activated"
        rows.append({
            "kind": "order", "name": o["buyer_name"], "order_number": o["order_number"], "order_id": o["public_id"],
            "source": PAGE_SOURCE, "stage": stage, "registration_status": status, "last_activity": last,
            "video_percent": video.get(o["pk"]),  # None: no linked visit, unknown
            "mobile": o["member__mobile"] or o["buyer_mobile"], "email": o["member__email"] or o["buyer_email"], "placed_at": o["created_at"].isoformat(),
            "payment_status": o["payment_status"], "price_centavos": o["price_centavos"], "currency": o["currency"],
        })
    return rows


def visit_stage(v):
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
    # A website chat started from a visit of this site is that visitor's one row (the visit row), never a second.
    visited = set(PackVisit.objects.filter(site=site).values_list("visitor_ref", flat=True))
    for c in InquiryConversation.objects.filter(site=site, provider__in=tuple(CHANNEL_KEYS)).exclude(
            pack_visitor_ref__in=visited, pack_visitor_ref__gt="").values(
            "provider", "contact_name", "contact_email", "contact_mobile", "last_message_at", "last_inbound_at", "created_at"):
        # A stored channel keeps the name in contact_name; read live, so a blanked name stays blank.
        rows.append({
            "kind": "inquiry", "name": c["contact_name"], "order_number": None, "order_id": None,
            "source": CHANNEL_KEYS[c["provider"]], "stage": "landing_page",
            "registration_status": None,
            "last_activity": _latest(c["last_message_at"], c["last_inbound_at"], default=c["created_at"]),
            "video_percent": None, "mobile": c["contact_mobile"],
            "email": c["contact_email"], **NO_ORDER,
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
