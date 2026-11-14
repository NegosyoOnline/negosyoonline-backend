"""A Santé Member: the buyer of one Santé site, a new kind of identity that is never a
NegosyoOnline Subscriber `Account`. Every row belongs to one site. Write these tables through
services.py and sessions.py, never directly."""
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.common.ids import new_public_id

VERIFIED, PENDING, UNVERIFIED = "verified", "pending", "unverified"
# A contact is pending until a code sent to it is used; the first one marks it verified. An
# email may also be "unverified": on file, but not yet a way to sign in (TWCinc's channel rule).
MOBILE_STATES = [(VERIFIED, "Verified"), (PENDING, "Pending verification")]
EMAIL_STATES = [(VERIFIED, "Verified"), (UNVERIFIED, "Not verified"), (PENDING, "Pending verification")]
ONBOARDING = "onboarding"
SOURCES = [(ONBOARDING, "Membership Pack order")]  # operator_test waits for HQ and a test site
SIGN_OUT, CONTACT_CHANGE, RECOVERY = "sign_out", "contact_change", "recovery"
REVOKE_REASONS = [(SIGN_OUT, "Signed out"), (CONTACT_CHANGE, "Contact change"), (RECOVERY, "Recovery")]
OTP_PURPOSES = [
    ("signin", "Sign-in"), ("email_verify", "Email verification"),
    ("current_proof", "Current contact proof"), ("replacement_proof", "Replacement contact proof"),
    ("onboarding", "Onboarding"), ("cod_confirmation", "COD confirmation"),
    ("withdrawal", "Withdrawal confirmation"), ("payout_destination", "Payout account confirmation"),
]
SMS, EMAIL = "sms", "email"
OTP_CHANNELS = [(SMS, "SMS"), (EMAIL, "Email")]
OTP_DELIVERY = [("sending", "sending"), ("sent", "sent"), ("failed", "failed"), ("held", "held")]
CONTACT_FIELDS = [("mobile", "Mobile"), ("email", "Email")]
AUDIT_KINDS = [
    ("self_service", "Self-service change"), ("subscriber_recovery", "Subscriber recovery"),
    ("first_verification", "First verification"),
]
ACTOR_TYPES = [
    ("member", "Member"), ("subscriber", "Subscriber"), ("operator", "Operator"), ("system", "System"),
]
HOLD_KINDS = [("recovery", "Recovery hold"), ("payout_fallback", "Payout account change hold")]


class ImmutableRecordError(Exception):
    pass


def new_member_id():
    return new_public_id("mem")


class Member(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_member_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="members")
    full_name = models.CharField(max_length=120)
    mobile = models.CharField(max_length=13)  # +639XXXXXXXXX
    mobile_state = models.CharField(max_length=10, choices=MOBILE_STATES, default=PENDING)
    mobile_verified_at = models.DateTimeField(null=True, blank=True)
    email = models.EmailField(max_length=254)  # always lowercase
    email_state = models.CharField(max_length=10, choices=EMAIL_STATES, default=PENDING)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    delivery_address = models.JSONField(default=dict, blank=True)  # line, barangay, city, province, zip, landmark
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


class MemberOtpChallenge(models.Model):
    """A six-digit code: HMAC-SHA256 (keyed with SECRET_KEY) stored, 10 minutes, single use, 5 wrong guesses burn it, bound to
    one browser (or to the member session). A decoy has no member and a code_hash that can never
    match. The row holds the contact it was sent to, so retention deletes it soon after expiry."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="member_otp_challenges")
    member = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.PROTECT, related_name="otp_challenges"
    )
    purpose = models.CharField(max_length=24, choices=OTP_PURPOSES)
    channel = models.CharField(max_length=8, choices=OTP_CHANNELS)
    target = models.CharField(max_length=254)
    requested_contact = models.CharField(max_length=254)  # as typed; send limits key on it
    code_hash = models.CharField(max_length=64)
    binding_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    delivery = models.CharField(max_length=8, default="sending", choices=OTP_DELIVERY)
    sms_message_id = models.PositiveBigIntegerField(null=True, blank=True)  # filled by the SMS slice
    replacement_for = models.CharField(max_length=8, blank=True, choices=CONTACT_FIELDS)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        indexes = [
            models.Index(
                fields=["site", "binding_hash", "purpose", "used_at", "superseded_at"],
                name="members_otp_open",
            ),
            models.Index(fields=["site", "requested_contact", "created_at"], name="members_otp_contact"),
            models.Index(fields=["site", "target", "created_at"], name="members_otp_target"),
            models.Index(fields=["sms_message_id"], name="members_otp_sms"),
        ]


class MemberRecoveryCheck(models.Model):
    """A Subscriber's recovery checklist result. Append-only except used_at."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="member_recovery_checks")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="recovery_checks")
    actor_account_id = models.PositiveBigIntegerField()
    actor_email_masked = models.CharField(max_length=254)
    items = models.JSONField(default=dict)
    passed = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if (
            self.pk is not None and not self._state.adding
            and list(kwargs.get("update_fields") or []) != ["used_at"]
        ):
            raise ImmutableRecordError("A recovery check changes only when it is used.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Recovery checks are never deleted.")


class MemberContactAudit(models.Model):
    """Append-only history of every contact change: who, when, and the checklist result. The
    contacts are stored masked."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="member_contact_audits")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="contact_audits")
    kind = models.CharField(max_length=24, choices=AUDIT_KINDS)
    field = models.CharField(max_length=8, choices=CONTACT_FIELDS)
    old_masked = models.CharField(max_length=254, blank=True)
    new_masked = models.CharField(max_length=254, blank=True)
    actor_type = models.CharField(max_length=12, choices=ACTOR_TYPES)
    actor_ref = models.PositiveBigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=254, blank=True)
    recovery_check = models.ForeignKey(
        MemberRecoveryCheck, null=True, blank=True, on_delete=models.PROTECT, related_name="audits"
    )
    ip_advisory = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ImmutableRecordError("Member contact audit rows are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Member contact audit rows are append-only.")


class MemberAddressEvent(models.Model):
    """Append-only history of the member's own delivery-address edits: the address before and
    after (the six address keys) and the advisory IP. All three are personal data: a member's
    blanking or erasure blanks them (apps/members/retention.py), through a queryset update, the
    only write these rows ever take after they are created."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="member_address_events")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="address_events")
    old = models.JSONField(default=dict, blank=True)
    new = models.JSONField(default=dict, blank=True)
    ip_advisory = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ImmutableRecordError("Member address events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Member address events are append-only.")


class MemberSecurityHold(models.Model):
    """A security hold. Stored once, never shortened or deleted; active while now < ends_at. A
    recovery hold names its contact audit; a payout-fallback hold names the payout destination
    (a plain integer: this app never imports the payout app)."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="member_security_holds")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="security_holds")
    kind = models.CharField(max_length=16, choices=HOLD_KINDS)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    audit = models.ForeignKey(
        MemberContactAudit, null=True, blank=True, on_delete=models.PROTECT, related_name="holds"
    )
    payout_destination_ref = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        indexes = [models.Index(fields=["member", "ends_at"], name="members_hold_active")]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(kind="recovery") | models.Q(audit__isnull=False),
                name="members_hold_recovery_audit",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="payout_fallback") | models.Q(payout_destination_ref__isnull=False),
                name="members_hold_payout_ref",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ImmutableRecordError("A security hold is never changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("A security hold is never deleted.")


NOTICE_KINDS = [("announcement", "Announcement"), ("promo", "Promo")]
NOTIFICATION_KINDS = [
    ("notice", "Announcement or promo"), ("training", "Training"), ("live", "Live training"),
    ("referral_order", "Referral order"),
]


def new_notice_id():
    return new_public_id("ntc")


def new_notification_id():
    return new_public_id("ntf")


class MemberNotice(models.Model):
    """An announcement or promo the Subscriber writes for the members of their own site (TWCinc
    has one global announcement list; here each site has its own). Shown on the member dashboard
    while `active` and inside its window (null start or end = open on that side). The picture is
    a key in the public media store (apps/catalog/media.py), the URL worked out on output. Write
    through apps/members/notices.py."""

    public_id = models.CharField(max_length=40, unique=True, default=new_notice_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="member_notices")
    kind = models.CharField(max_length=14, choices=NOTICE_KINDS)
    title = models.CharField(max_length=120)
    body = models.TextField(blank=True)  # at most 5000, checked in notices.py
    image_key = models.CharField(max_length=200, blank=True, default="")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["site", "active"], name="members_notice_site_active")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(starts_at__isnull=True) | models.Q(ends_at__isnull=True)
                | models.Q(ends_at__gt=models.F("starts_at")),
                name="members_notice_window_order",
            ),
        ]

    def __str__(self):
        return self.public_id


class MemberNotification(models.Model):
    """One entry in a member's bell. `url` is a path on the site (never an outside address)."""

    public_id = models.CharField(max_length=40, unique=True, default=new_notification_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="member_notifications")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=16, choices=NOTIFICATION_KINDS)
    title = models.CharField(max_length=160)
    url = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["member", "-created_at"], name="members_notif_member")]

    def __str__(self):
        return self.public_id
