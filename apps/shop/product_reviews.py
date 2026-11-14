"""Product reviews (CAT-1 r5b). A buyer reviews one line of their own order, and only that:

- The buyer proves the order the way the order view does: the placing buyer's `view_token`, or the
  signed-in member whose order it is. Anything else, and another site's order, answers the one 404.
- The order must have its Delivered event and must not be cancelled.
- One review per order line (a database unique). The buyer may change it once, within 7 days of
  writing it; a second change, or a change after the window, answers 409 `review_locked`.
- Nothing else creates a review: no seed, no admin, no command. The Subscriber hides or shows one
  and never edits the buyer's words. A hidden review is not counted and not shown.
- A store reads only its own site's reviews. The text is stored as plain text and returned as data.
"""
import hmac
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count
from django.utils import timezone

from apps.common.errors import ApiError
from apps.members import sessions
from apps.shop import constants as C, placement
from apps.shop.models import ProductReview, ShopOrder


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _invalid(fields):
    return ApiError(
        "invalid_request", C.MSG_REVIEW_FIELDS, status=422,
        fields={name: [message] for name, message in fields.items()},
    )


def _owns(site, order, view_token, member_token):
    """The same proof the order view takes: the view token, or the member's own signed-in order."""
    if (view_token or "").strip():
        return hmac.compare_digest(placement.token_hash(view_token), order.view_token_hash)
    if (member_token or "").strip() and settings.MEMBERS_ENABLED:
        session = sessions.find_session(site, member_token)
        return session is not None and order.member_id == session.member_id
    return False


def default_display_name(order):
    """First name and the initial of the last name: "Maria Santos" is "Maria S."."""
    name = order.member.full_name if order.member_id else order.receiver_name
    parts = (name or "").split()
    if not parts:
        return "Verified buyer"
    full = parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."
    return full[:C.REVIEW_NAME_MAX]


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _clean(data, *, creating):
    """(cleaned, errors) for the given values. Text arrives as text and the rating as a JSON integer."""
    cleaned, errors = {}, {}
    if "rating" in data or creating:
        value = data.get("rating")
        if _whole(value) and C.RATING_MIN <= value <= C.RATING_MAX:
            cleaned["rating"] = value
        else:
            errors["rating"] = C.MSG_REVIEW_RATING
    if "body" in data:
        value = data["body"]
        if isinstance(value, str) and len(value.strip()) <= C.REVIEW_BODY_MAX:
            cleaned["body"] = value.strip()
        else:
            errors["body"] = C.MSG_REVIEW_BODY
    if data.get("display_name") is not None:
        value = data["display_name"]
        if isinstance(value, str) and 1 <= len(value.strip()) <= C.REVIEW_NAME_MAX:
            cleaned["display_name"] = " ".join(value.split())
        else:
            errors["display_name"] = C.MSG_REVIEW_NAME
    return cleaned, errors


def _find_line(site, order_id, position, view_token, member_token):
    """The buyer's own order and one of its lines, or the one 404."""
    order = (
        ShopOrder.objects.select_related("member").prefetch_related("events")
        .filter(site=site, public_id=order_id).first()
    )
    if order is None or not _owns(site, order, view_token, member_token):
        raise _not_found()
    line = order.lines.filter(position=position).first() if _whole(position) else None
    if line is None:
        raise _not_found()
    return order, line


def reviewable(order):
    """Delivered (its event) and not cancelled."""
    return order.payment_status != C.CANCELLED and any(
        event.kind == C.EVENT_DELIVERED for event in order.events.all()
    )


def can_edit(review, now=None):
    now = now or timezone.now()
    return review.edited_at is None and now <= review.created_at + timedelta(days=C.REVIEW_EDIT_DAYS)


def create(site, order_id, position, *, view_token, member_token, data):
    order, line = _find_line(site, order_id, position, view_token, member_token)
    if not reviewable(order):
        raise ApiError("not_delivered", C.MSG_REVIEW_NOT_DELIVERED, status=409)
    cleaned, errors = _clean(data, creating=True)
    if errors:
        raise _invalid(errors)
    cleaned.setdefault("body", "")
    cleaned.setdefault("display_name", default_display_name(order))
    try:
        with transaction.atomic():
            return ProductReview.objects.create(
                site=site, order=order, order_line=line, product_id=line.product_id, **cleaned
            )
    except IntegrityError:  # the unique per order line: a second review, or a double submit
        raise ApiError("already_reviewed", C.MSG_REVIEW_EXISTS, status=409) from None


@transaction.atomic
def edit(site, order_id, position, *, view_token, member_token, data):
    order, line = _find_line(site, order_id, position, view_token, member_token)
    review = ProductReview.objects.select_for_update().filter(order_line=line).first()
    if review is None:
        raise ApiError("not_found", C.MSG_REVIEW_NONE, status=404)
    if not reviewable(order):
        raise ApiError("not_delivered", C.MSG_REVIEW_NOT_DELIVERED, status=409)
    cleaned, errors = _clean(data, creating=False)
    if errors:
        raise _invalid(errors)
    changed = {name: value for name, value in cleaned.items() if getattr(review, name) != value}
    if not changed:
        return review  # nothing differs: the one edit is not used
    if not can_edit(review):
        raise ApiError("review_locked", C.MSG_REVIEW_LOCKED, status=409)
    for name, value in changed.items():
        setattr(review, name, value)
    review.edited_at = timezone.now()
    review.save(update_fields=[*changed, "edited_at", "updated_at"])
    return review


def review_for_line(line):
    return ProductReview.objects.filter(order_line=line).first()


def reviews_by_line(orders):
    """{order line id: review} for these orders, in one query."""
    return {r.order_line_id: r for r in ProductReview.objects.filter(order__in=list(orders))}


# Output

def buyer_out(review):
    """What the buyer sees of their own review."""
    return {
        "id": review.public_id, "rating": review.rating, "body": review.body,
        "display_name": review.display_name, "created_at": review.created_at.isoformat(),
        "edited": review.edited_at is not None, "can_edit": can_edit(review),
    }


def public_out(review):
    """What a storefront shows: no order, no buyer detail, always a verified buyer."""
    return {
        "id": review.public_id, "rating": review.rating, "body": review.body,
        "display_name": review.display_name, "created_at": review.created_at.isoformat(),
        "edited": review.edited_at is not None, "verified_buyer": True,
    }


def storefront_page(site, product_id, page):
    """The product's visible reviews of this store, newest first, with the average and the count of
    all its visible reviews. `page` counts from 1."""
    visible = ProductReview.objects.filter(site=site, product_id=product_id, is_hidden=False)
    totals = visible.aggregate(count=Count("pk"), average=Avg("rating"))
    page = page if page >= 1 else 1
    start = (page - 1) * C.REVIEW_PAGE_SIZE
    rows = list(visible[start:start + C.REVIEW_PAGE_SIZE + 1])
    average = totals["average"]
    return {
        "rating_count": totals["count"],
        "rating_average": None if average is None else str(
            Decimal(str(average)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        ),
        "page": page, "page_size": C.REVIEW_PAGE_SIZE,
        "has_more": len(rows) > C.REVIEW_PAGE_SIZE,
        "reviews": [public_out(r) for r in rows[:C.REVIEW_PAGE_SIZE]],
    }


def owner_out(review):
    return {
        **public_out(review), "product_id": review.product_id, "product_code": review.order_line.code,
        "product_name": review.order_line.name, "order_number": review.order.order_number,
        "is_hidden": review.is_hidden,
        "hidden_at": review.hidden_at.isoformat() if review.hidden_at else None,
    }


def owner_list(site, *, product_id="", hidden=None):
    rows = ProductReview.objects.filter(site=site).select_related("order", "order_line")
    if product_id:
        rows = rows.filter(product_id=product_id)
    if hidden is not None:
        rows = rows.filter(is_hidden=hidden)
    return [owner_out(r) for r in rows[:C.OWNER_LIST_MAX]]


@transaction.atomic
def set_hidden(site, review_id, hidden):
    """Hide or show one review of this store. The words are never touched."""
    review = (
        ProductReview.objects.select_for_update(of=("self",))
        .select_related("order", "order_line").filter(site=site, public_id=review_id).first()
    )
    if review is None:
        raise _not_found()
    if review.is_hidden != hidden:
        review.is_hidden = hidden
        review.hidden_at = timezone.now() if hidden else None
        review.save(update_fields=["is_hidden", "hidden_at", "updated_at"])
    return review
