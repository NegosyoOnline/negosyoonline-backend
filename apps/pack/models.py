"""Membership Pack orders and the evidence kept with them (port of TWCinc's
negosyoonline_sante_pack/models.py and the CheckoutToken of negosyoonline_orders).

Every row belongs to one site. Pack orders are never deleted and there is no refund state. Proof
submissions, order events and the two buyer-consent records are append-only: no update, no
delete, not even by queryset. Money is integer centavos. There is no per-order billing here: an
order keeps only its unique number and `created_at`."""
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.ids import new_public_id
from apps.pack import constants as C


class ImmutableRecordError(Exception):
    pass


def new_order_id():
    return new_public_id("pko")


def new_proof_id():
    return new_public_id("pps")


class CheckoutToken(models.Model):
    """Guest checkout binding: only the SHA-256 of the raw token is stored. The raw token is
    answered once, to the storefronts server, which keeps it in the buyer's cookie."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_checkout_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()
    # full_name, mobile, email, delivery_address, and the held "agreement" / "enrollment" ticks.
    pending_contact = models.JSONField(default=dict, blank=True)
    onboarding_challenge_id = models.PositiveBigIntegerField(null=True, blank=True)
    verified_mobile = models.CharField(max_length=13, blank=True)
    order_ref = models.PositiveBigIntegerField(null=True, blank=True)  # the order this token placed
    session_issued_at = models.DateTimeField(null=True, blank=True)  # once-only (PK7 uses it)

    class Meta:
        indexes = [models.Index(fields=["site", "expires_at"], name="pack_token_site_expiry")]

    @property
    def requested_mobile(self):
        return (self.pending_contact or {}).get("mobile", "")


class PackOrder(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_order_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_orders")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="pack_orders")
    order_number = models.CharField(max_length=8)  # SMP00001
    price_centavos = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default=C.CURRENCY)
    buyer_name = models.CharField(max_length=120)
    delivery_address = models.JSONField(default=dict)
    payment_method = models.CharField(max_length=8, default=C.PAYMENT_METHOD)
    payment_status = models.CharField(max_length=16, choices=C.PAYMENT_STATUSES)
    rejection_lock_active = models.BooleanField(default=False)
    benefits_active_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=64)
    policy_version = models.CharField(max_length=16, blank=True)  # blank when no policy page is live
    state_version = models.PositiveIntegerField(default=1)  # every write that changes the order bumps it
    # Shipment, entered by hand by the Subscriber (PK-SHIP). Derived status: not shipped, shipped, delivered.
    courier = models.CharField(max_length=10, blank=True, choices=C.COURIERS)
    courier_other_name = models.CharField(max_length=60, blank=True)
    tracking_number = models.CharField(max_length=64, blank=True)
    shipped_on = models.DateField(null=True, blank=True)
    shipment_recorded_at = models.DateTimeField(null=True, blank=True)
    shipment_actor_ref = models.PositiveBigIntegerField(null=True, blank=True)
    shipment_actor_label = models.CharField(max_length=254, blank=True)
    delivered_on = models.DateField(null=True, blank=True)
    delivery_confirmed_at = models.DateTimeField(null=True, blank=True)
    delivery_actor_ref = models.PositiveBigIntegerField(null=True, blank=True)
    delivery_actor_label = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(shipment_recorded_at__isnull=True) | Q(payment_status=C.VERIFIED),
                name="pack_order_ship_needs_verified",
            ),
            models.CheckConstraint(
                condition=Q(delivery_confirmed_at__isnull=True) | Q(shipment_recorded_at__isnull=False),
                name="pack_order_deliver_needs_ship",
            ),
            models.UniqueConstraint(fields=["site", "order_number"], name="pack_order_number_uniq"),
            models.UniqueConstraint(fields=["site", "idempotency_key"], name="pack_order_idem_uniq"),
            models.UniqueConstraint(
                fields=["member"], condition=Q(payment_status__in=C.LIVE_STATUSES),
                name="pack_order_one_live_per_member",
            ),
            models.CheckConstraint(
                condition=Q(price_centavos=C.PRICE_CENTAVOS), name="pack_order_fixed_amount"
            ),
            models.CheckConstraint(
                condition=Q(payment_method=C.PAYMENT_METHOD), name="pack_order_direct_only"
            ),
        ]

    def __str__(self):
        return self.order_number

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Pack orders are never deleted.")

    @property
    def shipment_status(self):
        if self.delivery_confirmed_at is not None:
            return C.DELIVERED
        return C.SHIPPED if self.shipment_recorded_at is not None else C.NOT_SHIPPED

    @property
    def courier_name(self):
        if self.courier == C.COURIER_OTHER:
            return self.courier_other_name
        return C.COURIER_LABELS.get(self.courier, "")

    def add_event(self, kind, *, old=None, new=None, actor_type="system", actor_ref=None, actor_label=""):
        return PackOrderEvent.objects.create(
            site_id=self.site_id, order=self, kind=kind, old=old or {}, new=new or {},
            actor_type=actor_type, actor_ref=actor_ref, actor_label=actor_label,
        )


class EvidenceQuerySet(models.QuerySet):
    """A queryset write would skip AppendOnly's checks, so a queryset refuses every write."""

    def _refuse(self, *args, **kwargs):
        raise ImmutableRecordError(f"{self.model.__name__} rows are append-only.")

    update = delete = bulk_create = bulk_update = _refuse


class AppendOnly(models.Model):
    objects = EvidenceQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError(f"{type(self).__name__} rows are append-only.")
        kwargs["force_insert"] = True  # an existing pk is an IntegrityError, never an UPDATE
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(f"{type(self).__name__} rows are append-only.")


class PackProofSubmission(AppendOnly):
    public_id = models.CharField(max_length=40, unique=True, default=new_proof_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_proofs")
    order = models.ForeignKey(PackOrder, on_delete=models.PROTECT, related_name="submissions")
    seq = models.PositiveSmallIntegerField()
    # A snapshot of the account paid to: the account may later be edited or removed.
    account_public_id = models.CharField(max_length=40)
    account_kind = models.CharField(max_length=8)
    account_provider_name = models.CharField(max_length=60)
    account_name = models.CharField(max_length=120)
    account_number_last4 = models.CharField(max_length=4)
    reference_number = models.CharField(max_length=64)
    amount_sent_centavos = models.PositiveIntegerField()
    sender_bank = models.CharField(max_length=60)
    sender_name = models.CharField(max_length=120)
    sender_account_number = models.CharField(max_length=64)
    transferred_at = models.DateTimeField()
    file_key = models.CharField(max_length=255)
    content_type = models.CharField(max_length=32)
    size = models.PositiveIntegerField()
    submitted_at = models.DateTimeField(default=timezone.now, editable=False)
    ip_advisory = models.CharField(max_length=45, blank=True)

    class Meta:
        ordering = ["order", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["order", "seq"], name="pack_proof_seq_uniq"),
            models.CheckConstraint(
                condition=Q(amount_sent_centavos__gt=0, amount_sent_centavos__lte=C.MAX_AMOUNT_CENTAVOS),
                name="pack_proof_amount_range",
            ),
        ]


class PackProofDecision(AppendOnly):
    """The Subscriber's decision on one proof submission. A submission may be rejected and later
    approved without a resubmission, so the key is (submission, decision), not one row per
    submission."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_decisions")
    order = models.ForeignKey(PackOrder, on_delete=models.PROTECT, related_name="decisions")
    submission = models.ForeignKey(PackProofSubmission, on_delete=models.PROTECT, related_name="decisions")
    decision = models.CharField(max_length=8, choices=[("approved", "Approved"), ("rejected", "Rejected")])
    reason_code = models.CharField(max_length=20, blank=True, choices=C.REJECTION_REASONS)
    other_text = models.CharField(max_length=500, blank=True)
    actor_type = models.CharField(max_length=10, choices=C.ACTOR_TYPES)
    actor_ref = models.PositiveBigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=254, blank=True)
    decided_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["decided_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["submission", "decision"], name="pack_decision_once_per_kind"),
            models.CheckConstraint(
                condition=Q(decision="approved") | ~Q(reason_code=""), name="pack_decision_reject_needs_reason"
            ),
            models.CheckConstraint(
                condition=~Q(reason_code="other") | (~Q(other_text="") & ~Q(other_text__regex=r"^\s+$")),
                name="pack_decision_other_needs_text",
            ),
        ]


class PackOrderEvent(AppendOnly):
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_order_events")
    order = models.ForeignKey(PackOrder, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=32, choices=C.ORDER_EVENT_KINDS)
    old = models.JSONField(default=dict, blank=True)
    new = models.JSONField(default=dict, blank=True)
    actor_type = models.CharField(max_length=10, choices=C.ACTOR_TYPES)
    actor_ref = models.PositiveBigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]


class PackAgreementAcceptance(AppendOnly):
    """The Membership Agreement version a buyer accepted, with its checksum: at the account-details
    step of onboarding, or at checkout for an order placed by an older storefront. A row
    whose order is another site's, whose version is not published, or whose checksum is not the
    version's own is refused."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_acceptances")
    order = models.OneToOneField(PackOrder, on_delete=models.PROTECT, related_name="acceptance")
    agreement = models.ForeignKey("sante.MembershipAgreement", on_delete=models.PROTECT, related_name="+")
    version = models.ForeignKey(
        "sante.MembershipAgreementVersion", on_delete=models.PROTECT, related_name="+"
    )
    sha256 = models.CharField(max_length=64)
    accepted_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    def save(self, *args, **kwargs):
        if self._state.adding:
            version = self.version
            if self.order.site_id != self.site_id:
                raise ImmutableRecordError("The order belongs to another site.")
            if version.state != "published" or version.agreement_id != self.agreement_id:
                raise ImmutableRecordError("Only a published version can be accepted.")
            if version.sha256 != self.sha256:
                raise ImmutableRecordError("The checksum is not the version's own.")
        return super().save(*args, **kwargs)


class PackEnrollmentAuthorization(AppendOnly):
    """The enrollment tick, kept apart from the agreement, in the exact words shown."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_enrollments")
    order = models.OneToOneField(PackOrder, on_delete=models.PROTECT, related_name="enrollment")
    text_version = models.CharField(max_length=16)
    text = models.TextField()
    accepted_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)


class SanteRegistration(models.Model):
    """One Santé registration per member: opened (T1) when the first order is placed, it holds the
    date of birth and the two usernames the buyer gives in onboarding. Only the edges that end with
    the member signing in are carried (T1, T3, T4, T8); the Subscriber's registration workflow
    comes later. Never deleted."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_registrations")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="sante_registrations")
    pack_order = models.ForeignKey(PackOrder, on_delete=models.PROTECT, related_name="registrations")
    status = models.CharField(max_length=24, choices=C.REG_STATUS_CHOICES)
    status_entered_at = models.DateTimeField(default=timezone.now)
    state_version = models.PositiveIntegerField(default=1)
    date_of_birth = models.DateField(null=True, blank=True)
    desired_username = models.CharField(max_length=20, blank=True)
    alternate_username = models.CharField(max_length=20, blank=True)
    details_confirmed_at = models.DateTimeField(null=True, blank=True)
    details_purged_at = models.DateTimeField(null=True, blank=True)
    # Set by the Subscriber's "Mark registered" (T11+) and never changed after: the member's Santé
    # ID is typed by the Subscriber, nothing is read from Santé (ClickUp z8uq9meyqj).
    sante_member_id = models.CharField(max_length=32, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["site", "member"], name="pack_reg_site_member_uniq"),
            models.CheckConstraint(
                condition=Q(status__in=C.REG_STATUSES), name="pack_reg_status_valid"
            ),
        ]

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Registrations are never deleted.")


class PackBuyerNotice(models.Model):
    """The day-76 email to one buyer of one site for one deletion date (ClickUp z8uq9mep8p). The
    row is the once-only record: `unique(site, member, deletion_due_at)`. It is written when the
    first send is tried, so a failed send keeps its count (`attempts`, `last_attempt_at`) and a
    notice that was given up on (the date passed, or the retries ran out) keeps `gave_up_at`, so
    each is logged once. It holds no address and no text."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_buyer_notices")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="pack_buyer_notices")
    deletion_due_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    gave_up_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "member", "deletion_due_at"], name="pack_buyer_notice_once"
            ),
        ]


class SanteRegistrationEvent(AppendOnly):
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_reg_events")
    registration = models.ForeignKey(SanteRegistration, on_delete=models.PROTECT, related_name="events")
    edge = models.CharField(max_length=24)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24)
    reason_code = models.CharField(max_length=32, blank=True)
    note = models.CharField(max_length=500, blank=True)
    actor_type = models.CharField(max_length=10, choices=C.ACTOR_TYPES)
    actor_ref = models.PositiveBigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]


class PackRegisteredNotice(models.Model):
    """The "your Santé account is created" email to one member (ClickUp z8uq9meyqj). One row per
    registration: the once-only record, so a duplicate "Mark registered" or event sends no second
    email. It is written in the same transaction as the move to `registered`, and holds delivery
    evidence only: the state, the tries, the last error code and when it was sent. No address, no
    text, no secret."""

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="pack_registered_notices")
    registration = models.OneToOneField(
        SanteRegistration, on_delete=models.PROTECT, related_name="registered_notice"
    )
    status = models.CharField(max_length=10, choices=C.NOTICE_STATUS_CHOICES, default=C.NOTICE_PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=32, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    gave_up_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=C.NOTICE_STATUSES), name="pack_registered_notice_status_valid"
            ),
        ]
