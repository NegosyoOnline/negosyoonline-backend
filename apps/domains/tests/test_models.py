"""The 0001 constraints (plan r4.5), on SQLite and PostgreSQL alike (no row locks used)."""
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.domains.models import (
    ClaimState, Domain, DomainDnsRecord, DomainEvent, DomainHostname, DomainPurchaseLedger,
    DomainRegistration, DomainTldPrice, ImmutableRecordError, NameMismatch,
)
from apps.domains.tests.base import DomainsTestCase


class Constraints(DomainsTestCase):
    def refuses(self, fn):
        with self.assertRaises(IntegrityError), transaction.atomic():
            fn()

    def test_one_registration_per_site_for_life(self):
        site = self.site()
        DomainRegistration.objects.create(site=site)
        self.refuses(lambda: DomainRegistration.objects.create(site=site))

    def test_registration_name_is_unique_nullable_and_lowercase(self):
        DomainRegistration.objects.create(site=self.site())
        DomainRegistration.objects.create(site=self.site())  # two NULL names are fine
        sent = dict(state="sent", register_sent_at=timezone.now(), send_count=1)
        DomainRegistration.objects.create(site=self.site(), name="a.com", **sent)
        self.refuses(lambda: DomainRegistration.objects.create(site=self.site(), name="a.com", **sent))
        self.refuses(lambda: DomainRegistration.objects.create(site=self.site(), name="B.com", **sent))
        self.refuses(lambda: DomainRegistration.objects.create(site=self.site(), check_name="C.com"))

    def test_a_row_that_has_sent_can_never_say_it_has_sent_twice(self):
        reg = DomainRegistration.objects.create(site=self.site(), name="a.com", state="sent",
                                                register_sent_at=timezone.now(), send_count=1)
        reg.send_count = 2
        self.refuses(reg.save)

    def test_the_sent_columns_stay_consistent_with_the_state(self):
        site = self.site()
        self.refuses(lambda: DomainRegistration.objects.create(site=site, state="sent"))  # not sent yet
        self.refuses(lambda: DomainRegistration.objects.create(site=site, state="intent", send_count=1,
                                                               register_sent_at=timezone.now()))
        self.refuses(lambda: DomainRegistration.objects.create(site=site, state="succeeded", name=None,
                                                               send_count=1, register_sent_at=timezone.now()))
        self.refuses(lambda: DomainRegistration.objects.create(site=site, state="conflict", name="a.com", send_count=0))

    def test_a_registration_cannot_be_deleted(self):
        reg = DomainRegistration.objects.create(site=self.site())
        with self.assertRaises(ImmutableRecordError):
            reg.delete()
        with self.assertRaises(ImmutableRecordError):
            DomainRegistration.objects.all().delete()

    def test_one_active_claim_per_site(self):
        site = self.site()
        Domain.objects.create(site=site, name="a.com")
        self.refuses(lambda: Domain.objects.create(site=site, name="a.com"))
        Domain.objects.filter(site=site).update(state="ended")
        Domain.objects.create(site=site, name="a.com")

    def test_claim_name_and_sender_checks(self):
        site = self.site()
        self.refuses(lambda: Domain.objects.create(site=site, name="A.com"))
        for bad in ("Orders", "-x", "a@b", ""):
            with self.subTest(bad=bad):
                self.refuses(lambda b=bad: Domain.objects.create(site=self.site(), name="x.com", sender_local_part=b))

    def test_public_ids_are_opaque(self):
        claim = Domain.objects.create(site=self.site(), name="a.com")
        self.assertRegex(claim.public_id, r"^dom_[0-9a-hjkmnp-tv-z]{20}$")

    def test_one_holder_per_hostname(self):
        a = self.live_claim(name="a.com")
        b = self.claim(self.site(), "b.com", ClaimState.CONNECTING)
        row = DomainHostname(claim=b, role="www", hostname="www.b.com", holding=True)
        row.save()
        DomainHostname.objects.filter(pk=row.pk).update(hostname="www.a.com", holding=False)
        self.refuses(lambda: DomainHostname.objects.filter(pk=row.pk).update(holding=True))
        self.refuses(lambda: DomainHostname.objects.filter(pk=row.pk).update(hostname="WWW.b.com"))
        self.assertTrue(a.hostnames.filter(holding=True).exists())

    def test_one_hostname_per_role(self):
        claim = self.live_claim(name="a.com")
        self.refuses(lambda: DomainHostname.objects.create(claim=claim, role="www", hostname="www.a.com"))

    def test_dns_record_ids_are_unique_per_zone_and_types_are_limited(self):
        claim = self.live_claim(name="a.com")
        make = lambda **kw: DomainDnsRecord.objects.create(
            claim=claim, purpose="vercel", zone_id="z", record_id="r", record_type="A", name="a.com", content="1", ttl=1, **kw)
        make()
        self.refuses(make)
        self.refuses(lambda: DomainDnsRecord.objects.create(
            claim=claim, purpose="vercel", zone_id="z", record_id="r2", record_type="SRV", name="a.com", content="1", ttl=1))

    def test_the_ledger_is_one_row(self):
        DomainPurchaseLedger.objects.get_or_create(pk=1)
        self.refuses(lambda: DomainPurchaseLedger.objects.create(pk=2))

    def test_tld_price_is_usd_only(self):
        ok = dict(tld="com", registrable=True, tier="standard", sentinel_name="x.com", read_at=timezone.now(), allowed=True)
        DomainTldPrice.objects.create(currency="USD", **ok)
        self.refuses(lambda: DomainTldPrice.objects.create(currency="EUR", **{**ok, "tld": "net"}))

    def test_no_connect_kind_and_no_proof_fields_exist(self):
        fields = {f.name for f in Domain._meta.get_fields()}
        for gone in ("kind", "txt_value", "proof_state", "registration", "sender_address"):
            self.assertNotIn(gone, fields)
        states = {c.value for c in ClaimState}
        for gone in ("pending_proof", "waiting", "contested"):
            self.assertNotIn(gone, states)


class EventTests(DomainsTestCase):
    def test_events_are_append_only(self):
        site = self.site()
        event = DomainEvent.objects.create(site=site, kind="x")
        event.kind = "y"
        with self.assertRaises(ImmutableRecordError):
            event.save()
        with self.assertRaises(ImmutableRecordError):
            event.delete()
        for call in (lambda: DomainEvent.objects.all().update(kind="z"), lambda: DomainEvent.objects.all().delete(),
                     lambda: DomainEvent.objects.bulk_create([DomainEvent(site=site, kind="q")]),
                     lambda: DomainEvent.objects.bulk_update([event], ["kind"])):
            with self.assertRaises(ImmutableRecordError):
                call()

    def test_one_eligible_per_site_and_one_send_per_registration(self):
        site = self.site()
        DomainEvent.objects.create(site=site, kind="eligible")
        with self.assertRaises(IntegrityError), transaction.atomic():
            DomainEvent.objects.create(site=site, kind="eligible")
        DomainEvent.objects.create(site=site, kind="other")
        DomainEvent.objects.create(site=site, kind="other")
        reg = DomainRegistration.objects.create(site=site)
        DomainEvent.objects.create(site=site, registration=reg, kind="register_sent")
        with self.assertRaises(IntegrityError), transaction.atomic():
            DomainEvent.objects.create(site=site, registration=reg, kind="register_sent")


class NameBindingTests(DomainsTestCase):
    def test_a_claim_whose_name_is_not_the_registered_name_cannot_be_saved_from_confirmed_on(self):
        claim = self.claim(name="brand.com", state=ClaimState.CONFIRMED)
        claim.name = "other.com"
        with self.assertRaises(NameMismatch):
            claim.save()

    def test_the_exemption_is_an_explicit_argument(self):
        claim = self.claim(name="brand.com", state=ClaimState.CONFIRMED)
        claim.name = "other.com"
        claim.save(allow_name_release=True)

    def test_before_confirmed_the_name_is_a_free_display_copy(self):
        claim = self.claim(name="brand.com", state=ClaimState.QUOTED)
        claim.name = "other.com"
        claim.save()

    def test_a_null_registration_name_never_advances_a_claim_and_never_trips_the_assertion(self):
        site = self.site()
        DomainRegistration.objects.create(site=site)  # intent, no name
        claim = Domain.objects.create(site=site, name="x.com", state=ClaimState.CONFIRMED)
        claim.reason = "waiting"
        claim.save()

    def test_a_claim_with_no_registration_cannot_be_in_a_bound_state(self):
        with self.assertRaises(NameMismatch):
            Domain.objects.create(site=self.site(), name="x.com", state=ClaimState.REGISTERED)

    def test_a_hostname_is_only_the_registered_name_or_www_plus_it(self):
        claim = self.claim(name="brand.com", state=ClaimState.CONNECTING)
        for host, role in (("www.other.com", "www"), ("other.com", "apex"), ("brand.com", "www"), ("www.brand.com", "apex")):
            with self.subTest(host=host, role=role), self.assertRaises(NameMismatch):
                DomainHostname(claim=claim, role=role, hostname=host).save()
        DomainHostname(claim=claim, role="www", hostname="www.brand.com").save()

    def test_no_hostname_for_a_registration_that_is_not_succeeded(self):
        claim = self.claim(name="brand.com", state=ClaimState.CONNECTING, registration="conflict")
        with self.assertRaises(NameMismatch):
            DomainHostname(claim=claim, role="www", hostname="www.brand.com").save()
