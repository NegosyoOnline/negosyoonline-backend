"""Custom domains: the tables of plan r4.5 (ClickUp z8uq9mhgn7, design z8uq9mcyu0).

One migration (0001) holds every table the three branches use, so B2 (purchase) and B3 (email)
add code only. Every foreign key is PROTECT. Money is integer cents. Nothing here is read or
written while DOMAINS_ENABLED is off.
"""
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.common.ids import new_public_id


class ImmutableRecordError(Exception):
    pass


class NameMismatch(Exception):
    """A claim's name is not its site's registered name (plan r4.5 item 3 (2)): fail closed."""


def new_domain_id():
    return new_public_id("dom")


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
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(f"{type(self).__name__} rows are append-only.")


class NoDeleteQuerySet(models.QuerySet):
    """`update` stays allowed (the purchase step 1 is a conditional UPDATE); `delete` is refused."""

    def delete(self):
        raise ImmutableRecordError(f"{self.model.__name__} rows are never deleted.")


class ClaimState(models.TextChoices):
    CHECKING = "checking"
    QUOTED = "quoted"
    CONFIRMED = "confirmed"
    AWAITING_GATE = "awaiting_gate"
    REGISTERING = "registering"
    REGISTERED = "registered"
    ZONE = "zone"
    CONNECTING = "connecting"
    ACTIVATING = "activating"
    LIVE = "live"
    ATTENTION = "attention"
    FAILED = "failed"
    CONFLICT = "conflict"
    REMOVAL_PENDING = "removal_pending"
    ENDED = "ended"


class EmailState(models.TextChoices):
    NONE = "email_none"
    WAITING_CAPACITY = "email_waiting_capacity"
    CREATING = "email_creating"
    DNS = "email_dns"
    VERIFYING = "email_verifying"
    VERIFIED = "email_verified"
    ACTIVE = "email_active"
    FAILED = "email_failed"


class RegistrationState(models.TextChoices):
    INTENT = "intent"
    SENT = "sent"
    SUCCEEDED = "succeeded"
    CONFLICT = "conflict"


class ActorType(models.TextChoices):
    SYSTEM = "system"
    SUBSCRIBER = "subscriber"
    OPERATOR = "operator"


# Claim states in which a claim's name must be its registration's name (from `confirmed` on).
BOUND_STATES = (
    ClaimState.CONFIRMED, ClaimState.AWAITING_GATE, ClaimState.REGISTERING, ClaimState.REGISTERED,
    ClaimState.ZONE, ClaimState.CONNECTING, ClaimState.ACTIVATING, ClaimState.LIVE,
    ClaimState.ATTENTION, ClaimState.FAILED, ClaimState.CONFLICT, ClaimState.REMOVAL_PENDING,
)
# States that serve the hostname (the only served states), and the states that hold it.
SERVED_STATES = (ClaimState.LIVE, ClaimState.ATTENTION)
HOLDING_STATES = (
    ClaimState.CONNECTING, ClaimState.ACTIVATING, ClaimState.LIVE, ClaimState.ATTENTION,
    ClaimState.REMOVAL_PENDING,
)


class DomainPurchaseLedger(models.Model):
    """The lock row that serialises purchase step 1 (B2). Created by code with pk=1."""

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(pk=1), name="domains_ledger_single_row"),
        ]


class DomainRegistration(models.Model):
    site = models.OneToOneField("sites.Site", on_delete=models.PROTECT, related_name="domain_registration")
    name = models.CharField(max_length=253, null=True, blank=True)
    state = models.CharField(max_length=16, choices=RegistrationState.choices, default=RegistrationState.INTENT)
    intent_at = models.DateTimeField(default=timezone.now)
    check_name = models.CharField(max_length=253, blank=True)
    check_at = models.DateTimeField(null=True, blank=True)
    check_registrable = models.BooleanField(null=True, blank=True)
    check_tier = models.CharField(max_length=16, blank=True)
    check_registration_cost_cents = models.PositiveIntegerField(null=True, blank=True)
    check_renewal_cost_cents = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    register_sent_at = models.DateTimeField(null=True, blank=True)
    send_count = models.PositiveSmallIntegerField(default=0)
    provider_status = models.CharField(max_length=32, blank=True)
    provider_code = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(null=True, blank=True)
    auto_renew_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    objects = NoDeleteQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name"], name="domains_registration_name_unique"),
            models.CheckConstraint(
                condition=Q(name__isnull=True) | Q(name=Lower(F("name"))),
                name="domains_registration_name_lowercase",
            ),
            models.CheckConstraint(
                condition=Q(check_name="") | Q(check_name=Lower(F("check_name"))),
                name="domains_registration_check_name_lowercase",
            ),
            models.CheckConstraint(condition=Q(send_count__lte=1), name="domains_registration_send_count_max"),
            models.CheckConstraint(
                condition=(
                    Q(state="intent", register_sent_at__isnull=True, send_count=0)
                    | Q(
                        state__in=["sent", "succeeded", "conflict"],
                        register_sent_at__isnull=False, send_count=1, name__isnull=False,
                    )
                ),
                name="domains_registration_sent_consistent",
            ),
        ]

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("A registration is never deleted.")

    def __str__(self):
        return f"registration {self.pk} ({self.state})"


class Domain(models.Model):
    """A claim: one site's request for one domain (managed only; no kind, no proof fields)."""

    public_id = models.CharField(max_length=40, unique=True, default=new_domain_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="domain_claims")
    name = models.CharField(max_length=253)
    state = models.CharField(max_length=20, choices=ClaimState.choices, default=ClaimState.CHECKING)
    reason = models.CharField(max_length=200, blank=True)
    state_changed_at = models.DateTimeField(default=timezone.now)
    quote_expires_at = models.DateTimeField(null=True, blank=True)
    connecting_started_at = models.DateTimeField(null=True, blank=True)
    activating_started_at = models.DateTimeField(null=True, blank=True)
    attention_since = models.DateTimeField(null=True, blank=True)
    email_state = models.CharField(max_length=24, choices=EmailState.choices, default=EmailState.NONE)
    email_started_at = models.DateTimeField(null=True, blank=True)
    email_waiting_since = models.DateTimeField(null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    sender_local_part = models.CharField(max_length=64, default="orders")
    resend_domain_id = models.CharField(max_length=64, blank=True)
    resend_region = models.CharField(max_length=32, blank=True)
    resend_status = models.CharField(max_length=32, blank=True)
    resend_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site"], condition=~Q(state="ended"), name="domains_one_active_claim_per_site"
            ),
            models.CheckConstraint(condition=Q(name=Lower(F("name"))), name="domains_domain_name_lowercase"),
            models.CheckConstraint(
                condition=Q(sender_local_part__regex=r"^[a-z0-9][a-z0-9._-]{0,63}$"),
                name="domains_domain_sender_local_part",
            ),
        ]

    def __str__(self):
        return self.public_id

    def registration(self):
        return DomainRegistration.objects.filter(site_id=self.site_id).first()

    def assert_name_bound(self, *, allow_name_release=False):
        """The claim's name is a display copy; from `confirmed` on it must equal the registration
        name, else fail closed. Skipped only by the explicit argument (cancel, a refused worker
        Check, a rename of an `intent` row), and while the registration name is NULL."""
        if allow_name_release or self.state not in BOUND_STATES:
            return
        reg = self.registration()
        if reg is None or reg.name is None:
            if reg is None:
                raise NameMismatch("The claim has no registration.")
            return
        if reg.name != self.name:
            raise NameMismatch("The claim's name is not the site's registered name.")

    def save(self, *args, allow_name_release=False, **kwargs):
        self.assert_name_bound(allow_name_release=allow_name_release)
        return super().save(*args, **kwargs)


class DomainHostname(models.Model):
    class Role(models.TextChoices):
        WWW = "www"
        APEX = "apex"

    claim = models.ForeignKey(Domain, on_delete=models.PROTECT, related_name="hostnames")
    role = models.CharField(max_length=8, choices=Role.choices)
    hostname = models.CharField(max_length=253)
    holding = models.BooleanField(default=False)
    vercel_domain_name = models.CharField(max_length=253, blank=True)
    vercel_added_at = models.DateTimeField(null=True, blank=True)
    vercel_verified = models.BooleanField(default=False)
    vercel_cert_state = models.CharField(max_length=32, blank=True)
    redirect_to = models.CharField(max_length=253, blank=True)
    last_config_ok_at = models.DateTimeField(null=True, blank=True)
    last_probe_at = models.DateTimeField(null=True, blank=True)
    probe_failing_since = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["claim", "role"], name="domains_hostname_one_per_role"),
            models.UniqueConstraint(
                fields=["hostname"], condition=Q(holding=True), name="domains_one_holder_per_hostname"
            ),
            models.CheckConstraint(condition=Q(hostname=Lower(F("hostname"))), name="domains_hostname_lowercase"),
            models.CheckConstraint(
                condition=Q(vercel_domain_name=Lower(F("vercel_domain_name"))),
                name="domains_hostname_vercel_name_lowercase",
            ),
        ]

    def __str__(self):
        return self.hostname

    def assert_own_name(self):
        """The hostname is only ever the site's succeeded registration name or `www.` plus it."""
        reg = self.claim.registration()
        if reg is None or reg.state != RegistrationState.SUCCEEDED or not reg.name:
            raise NameMismatch("No succeeded registration for this hostname.")
        if self.hostname not in (reg.name, f"www.{reg.name}"):
            raise NameMismatch("The hostname is not the registered name.")
        if (self.role == self.Role.WWW) != (self.hostname == f"www.{reg.name}"):
            raise NameMismatch("The hostname does not match its role.")

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.assert_own_name()
        return super().save(*args, **kwargs)


class DomainDnsRecord(models.Model):
    class Purpose(models.TextChoices):
        VERCEL = "vercel"
        RESEND = "resend"

    class RecordType(models.TextChoices):
        A = "A"
        CNAME = "CNAME"
        TXT = "TXT"
        MX = "MX"

    claim = models.ForeignKey(Domain, on_delete=models.PROTECT, related_name="dns_records")
    hostname = models.ForeignKey(DomainHostname, on_delete=models.PROTECT, null=True, blank=True, related_name="dns_records")
    purpose = models.CharField(max_length=8, choices=Purpose.choices)
    zone_id = models.CharField(max_length=64)
    record_id = models.CharField(max_length=64)
    record_type = models.CharField(max_length=8, choices=RecordType.choices)
    name = models.CharField(max_length=253)
    content = models.CharField(max_length=1000)
    priority = models.PositiveSmallIntegerField(null=True, blank=True)
    ttl = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["zone_id", "record_id"], name="domains_dnsrecord_once"),
            models.CheckConstraint(
                condition=Q(record_type__in=["A", "CNAME", "TXT", "MX"]), name="domains_dnsrecord_type_allowed"
            ),
        ]


class DomainTldPrice(models.Model):
    tld = models.CharField(max_length=63, unique=True)
    registrable = models.BooleanField()
    tier = models.CharField(max_length=16)
    registration_cost_cents = models.PositiveIntegerField(null=True, blank=True)
    renewal_cost_cents = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3)
    sentinel_name = models.CharField(max_length=253)
    read_at = models.DateTimeField()
    allowed = models.BooleanField()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(currency="USD"), name="domains_tldprice_usd"),
        ]


class DomainEvent(AppendOnly):
    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="domain_events")
    claim = models.ForeignKey(Domain, on_delete=models.PROTECT, null=True, blank=True, related_name="events")
    registration = models.ForeignKey(
        DomainRegistration, on_delete=models.PROTECT, null=True, blank=True, related_name="events"
    )
    kind = models.CharField(max_length=48)
    actor_type = models.CharField(max_length=10, choices=ActorType.choices, default=ActorType.SYSTEM)
    actor_id = models.IntegerField(null=True, blank=True)
    from_state = models.CharField(max_length=24, blank=True)
    to_state = models.CharField(max_length=24, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site"], condition=Q(kind="eligible"), name="domains_event_one_eligible_per_site"
            ),
            models.UniqueConstraint(
                fields=["registration"], condition=Q(kind="register_sent"),
                name="domains_event_one_send_per_registration",
            ),
        ]
        indexes = [models.Index(fields=["kind", "created_at"], name="domains_event_kind_at")]
