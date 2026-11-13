"""Santé shop orders and the evidence kept with them (port of TWCinc's negosyoonline_storefront
models, in this codebase's pack pattern).

Every row belongs to one site. Shop orders are never deleted. Order lines, proof submissions and
order events are append-only: no update, no delete, not even by queryset. Money is integer
centavos. There is no per-order billing: an order keeps its unique number and `created_at`."""
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.ids import new_public_id
from apps.shop import constants as C


class ImmutableRecordError(Exception):
    pass


def new_order_id():
    return new_public_id("sho")


def new_proof_id():
    return new_public_id("shp")


class ShopOrder(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_order_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="shop_orders")
    order_number = models.CharField(max_length=8)  # SO00001
    buyer_kind = models.CharField(max_length=8, choices=C.BUYER_KINDS)
    member = models.ForeignKey(
        "members.Member", null=True, blank=True, on_delete=models.PROTECT, related_name="shop_orders"
    )
    ship_mode = models.CharField(max_length=10, choices=C.SHIP_MODES)
    # Set only for ship_mode "different": the member whose lines' earnings are recorded.
    credited_member = models.ForeignKey(
        "members.Member", null=True, blank=True, on_delete=models.PROTECT,
        related_name="credited_shop_orders",
    )
    receiver_name = models.CharField(max_length=C.RECEIVER_NAME_MAX)
    receiver_mobile = models.CharField(max_length=13)
    contact_email = models.EmailField(max_length=254, blank=True)
    delivery_address = models.JSONField(default=dict)  # province, city, barangay, line, zip, landmark
    payment_status = models.CharField(max_length=16, choices=C.PAYMENT_STATUSES)
    currency = models.CharField(max_length=3, default=C.CURRENCY)
    subtotal_centavos = models.PositiveBigIntegerField()
    shipping_charged_centavos = models.PositiveBigIntegerField(default=0)
    shipping_computed_centavos = models.PositiveBigIntegerField(null=True, blank=True)
    shipping_policy = models.CharField(max_length=8, choices=C.SHIPPING_POLICIES)
    total_centavos = models.PositiveBigIntegerField()
    quote_fingerprint = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=64)
    # SHA-256 of the token the placing buyer holds; the raw token is answered once.
    view_token_hash = models.CharField(max_length=64)
    verified_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_note = models.CharField(max_length=C.NOTE_MAX, blank=True)
    courier = models.CharField(max_length=C.COURIER_MAX, blank=True)
    tracking_no = models.CharField(max_length=C.TRACKING_MAX, blank=True)
    shipped_on = models.DateField(null=True, blank=True)
    delivered_on = models.DateField(null=True, blank=True)
    state_version = models.PositiveIntegerField(default=1)  # every write that changes the order bumps it
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["site", "order_number"], name="shop_order_number_uniq"),
            models.UniqueConstraint(fields=["site", "idempotency_key"], name="shop_order_idem_uniq"),
            models.CheckConstraint(
                condition=~Q(payment_status=C.CANCELLED) | ~Q(cancel_note=""),
                name="shop_order_cancel_needs_note",
            ),
            models.CheckConstraint(
                condition=Q(delivered_on__isnull=True) | Q(shipped_on__isnull=False),
                name="shop_order_delivered_needs_shipped",
            ),
            models.CheckConstraint(
                condition=Q(shipped_on__isnull=True) | Q(payment_status=C.VERIFIED),
                name="shop_order_shipped_needs_verified",
            ),
            models.CheckConstraint(
                condition=(Q(buyer_kind=C.GUEST, member__isnull=True) | Q(buyer_kind=C.MEMBER, member__isnull=False)),
                name="shop_order_buyer_matches_member",
            ),
            models.CheckConstraint(
                condition=Q(buyer_kind=C.MEMBER) | Q(ship_mode=C.GUEST),
                name="shop_order_ship_mode_needs_member",
            ),
            models.CheckConstraint(
                condition=Q(total_centavos=models.F("subtotal_centavos") + models.F("shipping_charged_centavos")),
                name="shop_order_total_adds_up",
            ),
        ]

    def __str__(self):
        return self.order_number

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Shop orders are never deleted.")

    def add_event(self, kind, *, note="", actor_type="system", actor_ref=None, actor_label=""):
        return ShopOrderEvent.objects.create(
            order=self, kind=kind, note=note, actor_type=actor_type, actor_ref=actor_ref,
            actor_label=actor_label,
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


class ShopOrderLine(AppendOnly):
    """One product of an order, copied from the catalog snapshot at placement."""

    order = models.ForeignKey(ShopOrder, on_delete=models.PROTECT, related_name="lines")
    position = models.PositiveSmallIntegerField()
    product_id = models.CharField(max_length=40)  # the catalog product's public id
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=120)
    revision = models.PositiveIntegerField()
    srp_centavos = models.PositiveBigIntegerField()
    earnings_rate = models.DecimalField(max_digits=5, decimal_places=4)
    quantity = models.PositiveSmallIntegerField()
    line_srp_total_centavos = models.PositiveBigIntegerField()
    line_earnings_centavos = models.PositiveBigIntegerField()
    line_members_total_centavos = models.PositiveBigIntegerField()
    line_charged_centavos = models.PositiveBigIntegerField()  # SRP or members total, per ship mode

    class Meta:
        ordering = ["order", "position"]
        constraints = [
            models.UniqueConstraint(fields=["order", "position"], name="shop_line_position_uniq"),
            models.CheckConstraint(
                condition=Q(quantity__gte=C.QTY_MIN, quantity__lte=C.QTY_MAX), name="shop_line_qty_range"
            ),
        ]


class ShopProofSubmission(AppendOnly):
    """The payment proof of an order: one per order, sent with the order. A snapshot of the
    account paid to, since the account may later be edited or removed."""

    public_id = models.CharField(max_length=40, unique=True, default=new_proof_id, editable=False)
    order = models.OneToOneField(ShopOrder, on_delete=models.PROTECT, related_name="proof")
    account_public_id = models.CharField(max_length=40)
    account_kind = models.CharField(max_length=8)
    account_provider_name = models.CharField(max_length=60)
    account_name = models.CharField(max_length=120)
    account_number_last4 = models.CharField(max_length=4)
    reference_number = models.CharField(max_length=C.REFERENCE_MAX)
    amount_sent_centavos = models.PositiveIntegerField()  # advisory: the owner compares it by eye
    file_key = models.CharField(max_length=255)
    content_type = models.CharField(max_length=32)
    size = models.PositiveIntegerField()
    submitted_at = models.DateTimeField(default=timezone.now, editable=False)
    ip_advisory = models.CharField(max_length=45, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount_sent_centavos__gt=0, amount_sent_centavos__lte=C.MAX_AMOUNT_CENTAVOS),
                name="shop_proof_amount_range",
            ),
        ]


class ShopOrderEvent(AppendOnly):
    """The audit trail of an order."""

    order = models.ForeignKey(ShopOrder, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=16, choices=C.EVENT_KINDS)
    actor_type = models.CharField(max_length=10, choices=C.ACTOR_TYPES)
    actor_ref = models.PositiveBigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=254, blank=True)
    note = models.CharField(max_length=C.NOTE_MAX, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]
