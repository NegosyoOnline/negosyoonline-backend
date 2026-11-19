"""The secure order-access link (port of TWCinc's negosyoonline_sante_pack/access.py): a buyer
whose mobile is still unconfirmed cannot sign in by SMS, so the after-payment email carries the
way back. One link at a time, 7 days, works once, and a mail scanner's GET spends nothing (the
page's button POSTs). It stops working as soon as the mobile is confirmed. Each link is a
used-once row of the member challenge table; the URL carries that row's signed id, never a secret
stored anywhere, so a retried email can sign the same row again."""
import secrets as pysecrets
from datetime import timedelta
from typing import NamedTuple

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.common import throttle
from apps.members import otp
from apps.members.models import MemberContactAudit, MemberOtpChallenge, MemberSession
from apps.pack import constants as C
from apps.pack.models import PackOrder

PATH = "/funnel/membership-pack/access/"
LIFETIME = timedelta(days=7)
_signer = signing.Signer(salt="apps.pack.access")


def applies(member):
    return member.source == "onboarding" and member.mobile_state == "pending"


def recovered_since(member, when):
    return MemberContactAudit.objects.filter(
        member=member, kind="subscriber_recovery", created_at__gte=when
    ).exists()


def contacts_replaced_since(member, when):
    """A Subscriber recovery or the member's own contact change at or after `when`. The member's
    own change is read from the session it revoked, not from a self-service audit: a buyer fixing
    a typo in the funnel writes that audit kind and replaces nothing a checkout cookie could hold."""
    return recovered_since(member, when) or MemberSession.objects.filter(
        member=member, revoke_reason="contact_change", revoked_at__gte=when
    ).exists()


def _binding(member):
    return f"pack-member-access:{member.pk}"


def _links(member):
    return MemberOtpChallenge.objects.filter(
        site_id=member.site_id, member=member, purpose="onboarding", channel="email",
        binding_hash=otp.binding_hash(_binding(member)),
    )


def _open(member, now):
    return (
        _links(member).filter(used_at__isnull=True, superseded_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at", "-pk").first()
    )


def _issue(member, now):
    return MemberOtpChallenge.objects.create(
        site_id=member.site_id, member=member, purpose="onboarding", channel="email",
        target=member.email, requested_contact=member.email, code_hash=pysecrets.token_hex(32),
        binding_hash=otp.binding_hash(_binding(member)), expires_at=now + LIFETIME, delivery="sent",
    )


def signed_id(row):
    return _signer.sign(str(row.pk))


def link_for(member, host):
    """The open link's full URL, opening one if none is open."""
    now = timezone.now()
    row = _open(member, now)
    if row is None or not is_open(row, now):
        row = _issue(member, now)
    return f"https://{host}{PATH}{signed_id(row)}/"


def renew(member, *, limited=True):
    """A new link replaces any open one. Limited per member for the buyer's own button; a
    Subscriber's recovery passes limited=False. The caller emails it to the checkout address only."""
    if limited:
        scope, limit, window = C.RENEW_LIMIT
        throttle.hit(scope, member.pk, limit=limit, window=window)
    _links(member).filter(used_at__isnull=True, superseded_at__isnull=True).update(
        superseded_at=timezone.now()
    )


def read(site, token):
    """The link row a token names, or None for anything this app did not sign for this site."""
    try:
        pk = int(_signer.unsign(token))
    except (signing.BadSignature, ValueError):
        return None
    row = MemberOtpChallenge.objects.select_related("member").filter(
        pk=pk, site=site, purpose="onboarding", channel="email", member__isnull=False
    ).first()
    return row if row is not None and row.binding_hash == otp.binding_hash(_binding(row.member)) else None


def is_open(row, now=None):
    now = now or timezone.now()
    return (
        row.used_at is None and row.superseded_at is None and row.expires_at > now
        and applies(row.member) and row.target == row.member.email
        and not contacts_replaced_since(row.member, row.created_at)
    )


def open_link(member):
    now = timezone.now()
    row = _open(member, now)
    return row if row is not None and is_open(row, now) else None


def redeem(row):
    """The member once, or None. The row lock makes a double click spend the link once."""
    with transaction.atomic():
        locked = MemberOtpChallenge.objects.select_for_update().get(pk=row.pk)
        if not is_open(locked):
            return None
        locked.used_at = timezone.now()
        locked.save(update_fields=["used_at"])
    return locked.member


def used(member):
    """Whoever opened the link only proved the typed email, which may be someone else's."""
    return _links(member).filter(used_at__isnull=False).exists()


# -- the link of an order that has no member yet (bank task z8uq9mgpzd, Coach Ed 2026-11-19) ---------------
#
# A bank order under review or rejected has no member, so it has no mobile to confirm and no member row to
# sign. Its link is a used-once row of the same challenge table with no member, bound to the order; the
# signed id carries "o<row>.<order>". Redeeming it spends the row and starts a fresh checkout token whose
# order is that order: the buyer lands on the status page, where the reason and the new-proof form are.
# It works only while the order has no member and is under review or rejected, and only for the email
# typed at checkout; once the Subscriber approves, the member's own link (above) takes over.

ORDER_STATES = (C.PENDING_REVIEW, C.REJECTED)


class Link(NamedTuple):
    kind: str  # "member" or "order"
    row: MemberOtpChallenge
    member: object  # the member of a member link, else None
    order: object  # the order of an order link, else None


def _order_binding(order):
    return f"pack-order-access:{order.pk}"


def order_applies(order):
    return order.member_id is None and order.payment_status in ORDER_STATES and bool(order.buyer_email)


def _order_links(order):
    return MemberOtpChallenge.objects.filter(
        site_id=order.site_id, member__isnull=True, purpose="onboarding", channel="email",
        binding_hash=otp.binding_hash(_order_binding(order)),
    )


def order_signed_id(row, order):
    return _signer.sign(f"o{row.pk}.{order.pk}")


def order_is_open(row, order, now=None):
    now = now or timezone.now()
    return (
        row.used_at is None and row.superseded_at is None and row.expires_at > now
        and order_applies(order) and row.target == order.buyer_email
    )


def order_link_for(order, host):
    """The open link's full URL for an order, opening one if none is open."""
    now = timezone.now()
    row = (
        _order_links(order).filter(used_at__isnull=True, superseded_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at", "-pk").first()
    )
    if row is None or not order_is_open(row, order, now):
        row = MemberOtpChallenge.objects.create(
            site_id=order.site_id, member=None, purpose="onboarding", channel="email",
            target=order.buyer_email, requested_contact=order.buyer_email, code_hash=pysecrets.token_hex(32),
            binding_hash=otp.binding_hash(_order_binding(order)), expires_at=now + LIFETIME, delivery="sent",
        )
    return f"https://{host}{PATH}{order_signed_id(row, order)}/"


def renew_order(order, *, limited=True):
    if limited:
        scope, limit, window = C.RENEW_LIMIT
        throttle.hit(f"{scope}-order", order.pk, limit=limit, window=window)
    _order_links(order).filter(used_at__isnull=True, superseded_at__isnull=True).update(
        superseded_at=timezone.now()
    )


def resolve(site, token):
    """The link a token names for this site (a member's or an order's), or None."""
    try:
        value = _signer.unsign(token)
    except signing.BadSignature:
        return None
    if value.startswith("o"):
        try:
            row_pk, order_pk = (int(x) for x in value[1:].split("."))
        except ValueError:
            return None
        order = PackOrder.objects.filter(site=site, pk=order_pk).first()
        row = MemberOtpChallenge.objects.filter(
            pk=row_pk, site=site, purpose="onboarding", channel="email", member__isnull=True
        ).first()
        if order is None or row is None or row.binding_hash != otp.binding_hash(_order_binding(order)):
            return None
        return Link("order", row, None, order)
    row = read(site, token)
    return Link("member", row, row.member, None) if row is not None else None


def link_open(link):
    return order_is_open(link.row, link.order) if link.kind == "order" else is_open(link.row)


def link_renewable(link):
    return order_applies(link.order) if link.kind == "order" else applies(link.member)


def redeem_order(row, order):
    """True once: spends the order link under the row lock (a double click spends it once)."""
    with transaction.atomic():
        locked = MemberOtpChallenge.objects.select_for_update().get(pk=row.pk)
        fresh = PackOrder.objects.select_for_update().get(pk=order.pk)
        if not order_is_open(locked, fresh):
            return False
        locked.used_at = timezone.now()
        locked.save(update_fields=["used_at"])
    return True
