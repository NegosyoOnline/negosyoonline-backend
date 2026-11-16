"""The Subscriber's prospect list (ClickUp z8uq9mfw14, under z8uq9meyqk): the people who placed a
Membership Pack order on one Santé site or wrote to it, newest activity first. Read-only, from rows
that already exist; stored inquiry rows only, no provider is ever called. Every query is filtered by
the site. Each row carries a name and nothing else about the person: no email, mobile, address,
proof or date of birth. Order rows and inquiry rows are never matched by name.

Layout adapted from TWCinc partnership/views/lists.py (ProspectListDataView) and partnership/stages.py
(one furthest-step stage per row, counts per stage taken before search and stage narrowing).
"""
from django.db.models import Max

from apps.inquiries.models import InquiryConversation
from apps.members.models import MemberSession
from apps.pack.models import PackOrder, PackProofSubmission, SanteRegistration

PAGE_SIZE = 25
# Furthest step first is the order of the funnel; a row reports the last one it reached.
ORDER_STAGES = ("order_placed", "proof_submitted", "payment_verified", "registration", "dashboard_access")
MESSAGE_STAGE = "message"
STAGES = ORDER_STAGES + (MESSAGE_STAGE,)
CHANNEL_KEYS = {
    InquiryConversation.PANCAKE: "facebook",
    InquiryConversation.WEBCHAT: "webchat",
    InquiryConversation.EMAIL: "email",
}


def _latest(*values, default=None):
    return max((v for v in values if v is not None), default=default)


def order_rows(site):
    orders = list(PackOrder.objects.filter(site=site).values(
        "pk", "public_id", "order_number", "buyer_name", "member_id", "created_at", "verified_at"))
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
    rows = []
    for o in orders:
        stage, last, status = "order_placed", o["created_at"], None
        if o["pk"] in proofs:
            stage, last = "proof_submitted", _latest(last, proofs[o["pk"]])
        if o["verified_at"] is not None:
            stage, last = "payment_verified", _latest(last, o["verified_at"])
            reg = registrations.get(o["pk"])
            # A registration is opened with the order; it counts as a step once the payment is approved.
            if reg is not None:
                stage, status, last = "registration", reg["status"], _latest(last, reg["status_entered_at"])
            if o["member_id"] in sessions:
                stage, last = "dashboard_access", _latest(last, sessions[o["member_id"]])
        rows.append({
            "kind": "order", "name": o["buyer_name"], "order_number": o["order_number"], "order_id": o["public_id"],
            "source": "order", "stage": stage, "registration_status": status, "last_activity": last,
        })
    return rows


def inquiry_rows(site):
    rows = []
    for c in InquiryConversation.objects.filter(site=site).values(
            "provider", "display_name", "contact_name", "last_message_at", "last_inbound_at", "created_at"):
        # A stored channel keeps the name in contact_name; read live, so a blanked name stays blank.
        name = c["contact_name"] if c["provider"] in InquiryConversation.STORED_PROVIDERS else c["display_name"]
        rows.append({
            "kind": "inquiry", "name": name, "order_number": None, "order_id": None,
            "source": CHANNEL_KEYS.get(c["provider"], "facebook"), "stage": MESSAGE_STAGE,
            "registration_status": None,
            "last_activity": _latest(c["last_message_at"], c["last_inbound_at"], default=c["created_at"]),
        })
    return rows


def _matches(row, q):
    if q in row["name"].lower():
        return True
    return row["order_number"] is not None and q in row["order_number"].lower()


def prospects(site, *, stage="", q="", page=1):
    rows = order_rows(site) + inquiry_rows(site)
    stage_counts = {"all": len(rows), **{s: 0 for s in STAGES}}
    for r in rows:
        stage_counts[r["stage"]] += 1
    q = q.strip().lower()
    if q:
        rows = [r for r in rows if _matches(r, q)]
    if stage:
        rows = [r for r in rows if r["stage"] == stage]
    rows.sort(key=lambda r: (r["last_activity"], r["order_id"] or ""), reverse=True)
    total = len(rows)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(page, pages)
    window = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    for r in window:
        r["last_activity"] = r["last_activity"].isoformat()
    return {"page": page, "page_size": PAGE_SIZE, "pages": pages, "total": total, "stage_counts": stage_counts,
            "results": window}
