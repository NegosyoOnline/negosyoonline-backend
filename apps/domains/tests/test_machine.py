"""The state machine with an injected clock, the fakes and a fake probe."""
import logging
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.domains import machine
from apps.domains.models import ClaimState, Domain, DomainDnsRecord, DomainEvent, DomainHostname, RegistrationState
from apps.domains.providers.fake import FakeVercel
from apps.domains.tests.base import DomainsTestCase, make_bundle, probe_returning

T0 = timezone.now().replace(microsecond=0)


def hours(n):
    return timedelta(hours=n)


class MachineTests(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.bundle = make_bundle()
        self.vercel, self.cf = self.bundle.fakes["vercel"], self.bundle.fakes["cloudflare"]
        self.site_ = self.site()
        self.claim_ = self.claim(self.site_, "brand.com", ClaimState.REGISTERED)
        self.probe = probe_returning(self.site_.public_id)

    def step(self, now=T0, probe_fn=None):
        word = machine.advance(self.claim_.pk, providers=self.bundle, now=now, probe_fn=probe_fn or self.probe)
        self.claim_.refresh_from_db()
        return word

    def to_connecting(self):
        """In state `connecting`, nothing sent to Vercel yet."""
        self.cf.add_zone("brand.com")
        self.assertEqual(self.step(), "zone")
        self.assertEqual(self.step(), "connecting")
        self.assertEqual(self.vercel.calls, [])
        return self.cf.zones["brand.com"]["id"]

    def connected(self):
        """One connecting pass done: both hostnames added, records written, in `activating`."""
        self.to_connecting()
        self.assertEqual(self.step(), "activating")

    def test_the_whole_path_to_live(self):
        self.connected()
        self.assertEqual(self.step(), "live")
        self.assertEqual(self.claim_.state, ClaimState.LIVE)
        www = self.claim_.hostnames.get(role="www")
        self.assertTrue(www.holding and www.vercel_verified)
        self.assertEqual(self.probe.calls, ["www.brand.com"])

    def test_vercel_gets_both_hostnames_apex_with_the_308_redirect(self):
        self.connected()
        adds = [r for r in self.vercel.requests if r[0] == "POST" and r[1].endswith("/domains")]
        self.assertEqual([a[2]["name"] for a in adds], ["www.brand.com", "brand.com"])
        self.assertNotIn("redirect", adds[0][2])
        self.assertEqual(adds[1][2]["redirect"], "www.brand.com")
        self.assertEqual(adds[1][2]["redirectStatusCode"], 308)

    def test_dns_records_come_from_vercel_dns_only_and_are_recorded(self):
        self.connected()
        records = {(r.record_type, r.name, r.content) for r in DomainDnsRecord.objects.all()}
        self.assertEqual(records, {("CNAME", "www.brand.com", FakeVercel.CNAME), ("A", "brand.com", FakeVercel.IPV4)})
        for rec in self.cf.records[self.cf.zones["brand.com"]["id"]].values():
            self.assertIs(rec["proxied"], False)

    def test_a_second_pass_writes_no_duplicates(self):
        self.connected()
        count = DomainDnsRecord.objects.count()
        Domain.objects.filter(pk=self.claim_.pk).update(state=ClaimState.CONNECTING)
        self.step()
        self.assertEqual(DomainDnsRecord.objects.count(), count)

    def test_a_missing_zone_waits_then_fails_after_72_hours(self):
        self.assertEqual(self.step(), "zone")
        self.assertEqual(self.step(T0 + hours(1)), "waiting_zone")
        self.assertEqual(self.step(T0 + hours(73)), "failed")
        self.assertEqual(self.claim_.state, ClaimState.FAILED)

    def test_connecting_fails_after_72_hours(self):
        self.to_connecting()
        self.claim_.connecting_started_at = T0
        self.claim_.save()
        with mock.patch.object(self.bundle.vercel, "verify", return_value={"verified": False, "name": "", "verification": [], "redirect": ""}):
            self.assertEqual(self.step(T0 + hours(1)), "connecting")
            self.assertEqual(self.step(T0 + hours(73)), "failed")

    def test_activating_fails_after_24_hours_of_failed_probes(self):
        self.connected()
        bad = probe_returning(self.site_.public_id, ok=False)
        self.assertEqual(self.step(T0 + hours(1), bad), "activating")
        self.assertEqual(self.step(T0 + hours(25), bad), "failed")

    def test_a_probe_that_names_another_site_is_a_failure(self):
        self.connected()
        self.assertEqual(self.step(T0 + hours(1), probe_returning("site_someoneelse")), "activating")

    def test_failed_retry_goes_back_to_zone_never_to_a_purchase(self):
        self.step()
        self.claim_.state = ClaimState.FAILED
        self.claim_.save()
        self.assertTrue(machine.retry(self.claim_))
        self.assertEqual(self.claim_.state, ClaimState.ZONE)
        self.assertFalse(machine.retry(self.claim_))

    def test_an_unrecorded_record_in_the_way_is_a_conflict(self):
        zid = self.to_connecting()
        self.cf.records[zid]["a" * 32] = {"id": "a" * 32, "type": "CNAME", "name": "www.brand.com", "content": "else.example"}
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(), "conflict")
        self.assertEqual(self.claim_.state, ClaimState.CONFLICT)

    def test_a_domain_on_another_vercel_project_is_a_conflict(self):
        self.to_connecting()
        self.vercel.fail_next(409)
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(), "conflict")

    def test_an_apex_failure_never_fails_www(self):
        self.to_connecting()
        real_add = self.bundle.vercel.add

        def add(name, **kw):
            if name == "brand.com":
                raise machine.ProviderError("unavailable", True)
            return real_add(name, **kw)

        with mock.patch.object(self.bundle.vercel, "add", side_effect=add):
            self.assertEqual(self.step(), "activating")
        self.assertTrue(DomainEvent.objects.filter(kind="apex_failed").exists())

    def test_a_transient_provider_error_retries_and_changes_nothing(self):
        self.to_connecting()
        self.vercel.fail_next(503)
        self.assertEqual(self.step(), "retry")
        self.assertEqual(self.claim_.state, ClaimState.CONNECTING)

    def test_a_registration_that_is_not_succeeded_is_never_worked_on(self):
        from apps.domains.models import DomainRegistration
        DomainRegistration.objects.filter(site=self.site_).update(state=RegistrationState.CONFLICT)
        self.assertEqual(self.step(), "waiting")
        self.assertEqual(self.vercel.calls + self.cf.calls, [])

    def test_a_forged_claim_name_makes_no_provider_call(self):
        Domain.objects.filter(pk=self.claim_.pk).update(name="other.com")
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(), "refused")
        self.assertEqual(self.vercel.calls + self.cf.calls, [])

    def test_purchase_states_are_never_advanced_here(self):
        for state in (ClaimState.CHECKING, ClaimState.CONFIRMED, ClaimState.AWAITING_GATE, ClaimState.REGISTERING, ClaimState.LIVE):
            Domain.objects.filter(pk=self.claim_.pk).update(state=state)
            self.assertEqual(self.step(), "idle")
        self.assertEqual(self.vercel.calls + self.cf.calls, [])

    def test_single_flight(self):
        cache.add(f"domains:claim:{self.claim_.pk}", 1, 60)
        self.assertEqual(self.step(), "busy")
        self.assertEqual(self.vercel.calls + self.cf.calls, [])

    def test_provider_off_takes_no_step(self):
        self.assertEqual(machine.advance(self.claim_.pk, providers=None), "off")

    @override_settings(DOMAINS_PROVIDER_PAUSED=True)
    def test_the_kill_switch_stops_every_step(self):
        self.cf.add_zone("brand.com")
        self.claim_.state = ClaimState.ZONE
        self.claim_.save()
        self.assertEqual(self.step(), "paused")
        self.assertEqual(self.vercel.calls + self.cf.calls, [])

    def test_a_site_on_hold_waits(self):
        self.site_.operator_hold = True
        self.site_.save()
        self.assertEqual(self.step(), "site_off")

    def test_a_refusal_found_at_activating_fails_the_claim(self):
        self.connected()
        with override_settings(DOMAINS_PLATFORM_ZONES=("brand.com",)):
            self.assertEqual(self.step(), "failed")


class LiveCheckTests(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.bundle = make_bundle()
        self.site_ = self.site()
        self.claim_ = self.live_claim(self.site_)
        self.vercel = self.bundle.fakes["vercel"]
        for name in ("www.brand.com", "brand.com"):
            self.vercel.domains[name] = {"name": name, "verified": True, "verification": [], "redirect": ""}
        self.good = probe_returning(self.site_.public_id)
        self.bad = probe_returning(self.site_.public_id, ok=False)

    def check(self, now, probe_fn):
        word = machine.hourly_check(self.claim_.pk, providers=self.bundle, now=now, probe_fn=probe_fn)
        self.claim_.refresh_from_db()
        return word

    def test_healthy(self):
        self.assertEqual(self.check(T0, self.good), "ok")
        self.assertEqual(self.claim_.state, ClaimState.LIVE)

    def test_six_hours_of_failure_is_attention_and_it_recovers(self):
        self.assertEqual(self.check(T0, self.bad), "failing")
        self.assertEqual(self.check(T0 + hours(5), self.bad), "failing")
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.check(T0 + hours(6), self.bad), "attention")
        self.assertEqual(self.claim_.state, ClaimState.ATTENTION)
        self.assertEqual(self.check(T0 + hours(7), self.good), "ok")
        self.assertEqual(self.claim_.state, ClaimState.LIVE)

    def test_attention_for_14_days_is_failed(self):
        self.check(T0, self.bad)
        with self.assertLogs("domains", level=logging.ERROR):
            self.check(T0 + hours(6), self.bad)
        self.assertEqual(self.check(T0 + hours(6) + timedelta(days=14), self.bad), "failed")
        self.assertEqual(self.claim_.state, ClaimState.FAILED)

    def test_a_vercel_that_lost_the_domain_counts_as_failing(self):
        del self.vercel.domains["www.brand.com"]
        self.assertEqual(self.check(T0, self.good), "failing")


class RemovalTests(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.bundle = make_bundle()
        self.site_ = self.site()
        self.claim_ = self.claim(self.site_, "brand.com", ClaimState.REGISTERED)
        self.bundle.fakes["cloudflare"].add_zone("brand.com")
        self.probe = probe_returning(self.site_.public_id)
        for _ in range(5):
            machine.advance(self.claim_.pk, providers=self.bundle, now=T0, probe_fn=self.probe)
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.state, ClaimState.LIVE)

    def test_removal_takes_down_only_recorded_entries_and_keeps_the_registration(self):
        cf, vercel = self.bundle.fakes["cloudflare"], self.bundle.fakes["vercel"]
        zid = cf.zones["brand.com"]["id"]
        cf.records[zid]["f" * 32] = {"id": "f" * 32, "type": "TXT", "name": "brand.com", "content": "not ours"}
        self.assertTrue(machine.request_removal(self.claim_, reason="test", now=T0))
        self.assertEqual(self.claim_.state, ClaimState.REMOVAL_PENDING)
        self.assertEqual(machine.advance(self.claim_.pk, providers=self.bundle, now=T0), "ended")
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.state, ClaimState.ENDED)
        self.assertEqual(vercel.domains, {})
        self.assertEqual(list(cf.records[zid]), ["f" * 32])  # the record we did not write stays
        self.assertFalse(DomainDnsRecord.objects.filter(removed_at__isnull=True).exists())
        self.assertFalse(DomainHostname.objects.filter(holding=True).exists())
        from apps.domains.models import DomainRegistration
        self.assertTrue(DomainRegistration.objects.filter(site=self.site_, state="succeeded").exists())

    def test_an_error_keeps_removal_pending(self):
        machine.request_removal(self.claim_, now=T0)
        self.bundle.fakes["vercel"].fail_next(403)
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(machine.advance(self.claim_.pk, providers=self.bundle, now=T0), "refused")
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.state, ClaimState.REMOVAL_PENDING)

    @override_settings(DOMAINS_PROVIDER_PAUSED=True)
    def test_paused_removal_stays_queued(self):
        machine.request_removal(self.claim_, now=T0)
        self.assertEqual(machine.advance(self.claim_.pk, providers=self.bundle, now=T0), "paused")
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.state, ClaimState.REMOVAL_PENDING)

    def test_a_removed_claim_can_be_followed_by_a_new_one(self):
        machine.request_removal(self.claim_, now=T0)
        machine.advance(self.claim_.pk, providers=self.bundle, now=T0)
        Domain.objects.create(site=self.site_, name="brand.com", state=ClaimState.REGISTERED)
