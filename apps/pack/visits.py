"""The Membership Pack page visitor (ClickUp z8uq9mfw14): how far a visitor got and the contact typed in
the checkout step, stored only in PackVisit. Adapted from TWCinc funnels/views/ajax.py (33-178: create
prospect, video progress with the 90 percent mark, checkout contact) and funnels/models.py Prospect.

The storefront asks for a visit and gets a server-made ref; its browser keeps the ref in localStorage.
Every row is site-scoped, every write is rate limited, nothing is sent to a third party except, with the visitor's consent and Meta events on, the hashed
ref and saved contact to Meta's Conversions API (apps.meta_ads.events).
A contact is saved when the visitor leaves a checkout field and is held only here: it is not a member,
it takes no part in the duplicate check, and retention blanks it (apps.pack.retention).
"""
import re
import secrets

from django.db.models import F, Q
from django.utils import timezone

from apps.common import throttle
from apps.common.errors import ApiError
from apps.pack import constants as C
from apps.pack.models import PackVisit

REF_RE = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
MARKS = (25, 50, 75, 90, 100)  # the video marks the player reports; a visitor's percent is the highest
DONE_PERCENT = 90  # TWCinc stages.DONE_WATCHING_PCT
EVENTS = ("start", "percent", "checkout")
MOBILE_RE = re.compile(r"^[0-9+()\- ]{1,20}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}$")


def _unknown():
    return ApiError("not_found", "Not found.", status=404, event="visit_unknown", reason="missing")


def _get(site, ref):
    if not isinstance(ref, str) or not REF_RE.match(ref):
        raise _unknown()
    visit = PackVisit.objects.filter(site=site, visitor_ref=ref).first()
    if visit is None:
        raise _unknown()
    return visit


def _limit(rule, key):
    scope, limit, window = rule
    throttle.hit(scope, key, limit=limit, window=window)


def begin(site, ref, *, ip):
    """The visit this ref names on this site, or a new one with a ref made here. Returns the ref."""
    now = timezone.now()
    if ip:
        _limit(C.VISIT_IP_LIMIT, ip)
    if isinstance(ref, str) and REF_RE.match(ref):
        if PackVisit.objects.filter(site=site, visitor_ref=ref).update(last_activity_at=now):
            return ref
    while True:
        new = secrets.token_urlsafe(16)
        if not PackVisit.objects.filter(site=site, visitor_ref=new).exists():
            break
    PackVisit.objects.create(site=site, visitor_ref=new, landed_at=now, last_activity_at=now, created_at=now)
    return new


def note_event(site, ref, kind, percent=None, *, ip):
    """One event for a visit: `start` (pressed play), `percent` (a video mark, the highest kept; 90
    or more is done watching) or `checkout` (opened the checkout step)."""
    if kind not in EVENTS:
        raise ApiError("invalid_event", "Event must be start, percent or checkout.", status=422)
    if kind == "percent" and percent not in MARKS:
        raise ApiError("invalid_percent", "Percent must be 25, 50, 75, 90 or 100.", status=422)
    if ip:
        _limit(C.VISIT_EVENT_IP_LIMIT, ip)
    visit = _get(site, ref)
    _limit(C.VISIT_REF_LIMIT, visit.pk)
    now = timezone.now()
    changes = {"last_activity_at": now}
    if kind in ("start", "percent") and visit.started_watching_at is None:
        changes["started_watching_at"] = now
    if kind == "percent":
        changes["video_percent"] = max(visit.video_percent, percent)
        if changes["video_percent"] >= DONE_PERCENT and visit.done_watching_at is None:
            changes["done_watching_at"] = now
    if kind == "checkout" and visit.checkout_started_at is None:
        changes["checkout_started_at"] = now
    # The highest percent wins even when two marks arrive out of order.
    PackVisit.objects.filter(pk=visit.pk).update(**{k: v for k, v in changes.items() if k != "video_percent"})
    if "video_percent" in changes:
        PackVisit.objects.filter(pk=visit.pk, video_percent__lt=changes["video_percent"]).update(
            video_percent=changes["video_percent"])
    return True


def clean_contact(name, mobile, email):
    """What a visitor typed, kept only if it looks like what the field is for. A field that does not
    is dropped, never an error: the buyer is still typing."""
    name = " ".join((name or "").split())[:120]
    mobile = (mobile or "").strip()
    mobile = mobile if MOBILE_RE.match(mobile) else ""
    email = (email or "").strip().lower()
    email = email if EMAIL_RE.match(email) else ""
    return name, mobile, email


def save_contact(site, ref, name, mobile, email, *, ip):
    """The checkout contact step, saved when a field loses focus. Name, mobile and email only; an
    empty save changes nothing. It does not touch members and passes none of the duplicate checks."""
    if ip:
        _limit(C.VISIT_EVENT_IP_LIMIT, ip)
    visit = _get(site, ref)
    _limit(C.VISIT_CONTACT_LIMIT, visit.pk)
    name, mobile, email = clean_contact(name, mobile, email)
    if not (name or mobile or email):
        return False
    now = timezone.now()
    PackVisit.objects.filter(pk=visit.pk).update(
        contact_name=name, contact_mobile=mobile, contact_email=email, contact_saved_at=now,
        last_activity_at=now, checkout_started_at=visit.checkout_started_at or now,
    )
    return True


def link_order(site, ref, order):
    """When an order is placed with a visit's ref, the visit points at it. Never raises: the order
    is already placed. A ref of another site, or an unknown one, links nothing."""
    if not isinstance(ref, str) or not REF_RE.match(ref):
        return False
    return bool(PackVisit.objects.filter(site=site, visitor_ref=ref, order__isnull=True).update(
        order=order, last_activity_at=timezone.now()))


def valid_ref(ref):
    return isinstance(ref, str) and bool(REF_RE.match(ref))


def note_chat_contact(site, ref, *, email="", mobile=""):
    """A website chat started from this visit (ClickUp z8uq9mh9ev): the email or mobile the visitor typed
    in the chat is written onto the visit's own contact fields, only where they are empty. Never raises,
    never matches by name, and a malformed, unknown or other-site ref does nothing. True when the ref
    names a visit of this site (the chat then has no prospect row of its own)."""
    if not valid_ref(ref):
        return False
    visit = PackVisit.objects.filter(site=site, visitor_ref=ref).first()
    if visit is None:
        return False
    changes = {}
    if email and not visit.contact_email and EMAIL_RE.match(email):
        changes["contact_email"] = email
    if mobile and not visit.contact_mobile and MOBILE_RE.match(mobile):
        changes["contact_mobile"] = mobile
    if changes:
        PackVisit.objects.filter(pk=visit.pk).update(contact_saved_at=timezone.now(), **changes)
    return True


def blank_contacts(rows):
    """Blank the typed contact of these visits."""
    return rows.exclude(contact_name="", contact_mobile="", contact_email="").update(
        contact_name="", contact_mobile="", contact_email="")
