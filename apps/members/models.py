"""A Santé Member: the buyer of one Santé site, a new kind of identity that is never a
NegosyoOnline Subscriber `Account`. Every row belongs to one site. Write these tables through
services.py and sessions.py, never directly."""
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.common.ids import new_public_id

VERIFIED, PENDING = "verified", "pending"
# A contact is pending until a code sent to it is used; the first one marks it verified.
CONTACT_STATES = [(VERIFIED, "Verified"), (PENDING, "Pending verification")]
ONBOARDING = "onboarding"
SOURCES = [(ONBOARDING, "Membership Pack order")]
SIGN_OUT, CONTACT_CHANGE = "sign_out", "contact_change"
REVOKE_REASONS = [(SIGN_OUT, "Signed out"), (CONTACT_CHANGE, "Contact change")]


def new_member_id():
    return new_public_id("mem")


class Member(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_member_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="members")
    full_name = models.CharField(max_length=120)
    mobile = models.CharField(max_length=13)  # +639XXXXXXXXX
    mobile_state = models.CharField(max_length=10, choices=CONTACT_STATES, default=PENDING)
    mobile_verified_at = models.DateTimeField(null=True, blank=True)
    email = models.EmailField(max_length=254)  # always lowercase
    email_state = models.CharField(max_length=10, choices=CONTACT_STATES, default=PENDING)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=16, choices=SOURCES, default=ONBOARDING)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["site", "mobile"], name="members_member_site_mobile"),
            models.UniqueConstraint(fields=["site", "email"], name="members_member_site_email"),
            models.CheckConstraint(
                condition=models.Q(email=Lower("email")), name="members_member_email_lowercase"
            ),
            models.CheckConstraint(
                condition=models.Q(mobile__regex=r"^\+639[0-9]{9}$"),
                name="members_member_mobile_normalised",
            ),
        ]

    def __str__(self):
        return self.public_id


class MemberSession(models.Model):
    """A member's sign-in. Only the SHA-256 of the token is stored; the token itself is shown
    once, to the storefronts server, which keeps it in its own host-only cookie."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="member_sessions")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()  # fixed from sign-in; use never extends it
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=16, blank=True, choices=REVOKE_REASONS)
    ip_advisory = models.CharField(max_length=45, blank=True)  # the visitor's address, never trusted

    class Meta:
        indexes = [models.Index(fields=["member", "revoked_at"], name="members_session_open")]
