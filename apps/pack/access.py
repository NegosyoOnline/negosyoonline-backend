"""The secure order-access link (port of TWCinc's negosyoonline_sante_pack/access.py): a buyer
whose mobile is still unconfirmed cannot sign in by SMS, so the after-payment email carries the
way back. One link at a time, 7 days, works once, and a mail scanner's GET spends nothing (the
page's button POSTs). It stops working as soon as the mobile is confirmed. Each link is a
used-once row of the member challenge table; the URL carries that row's signed id, never a secret
stored anywhere, so a retried email can sign the same row again."""
import secrets as pysecrets
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.common import throttle
from apps.members import otp
from apps.members.models import MemberContactAudit, MemberOtpChallenge, MemberSession
from apps.pack import constants as C

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
