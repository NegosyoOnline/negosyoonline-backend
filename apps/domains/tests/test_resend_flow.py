"""The email track (plan r4.3 item 3): records come from Resend's answer and are written DNS-only
with recorded ids, verify polling, the wait for capacity, `email_failed` never failing the website,
the sender lookup, removal and whitelisted event details. Fakes under the real adapters."""
import logging
from datetime import timedelta

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.billing.models import Subscription
from apps.domains import email_track, events, machine, sweeps, tasks
from apps.domains.models import (
    ClaimState, Domain, DomainDnsRecord, DomainEvent, DomainRegistration, EmailState, RegistrationState,
)
from apps.domains.tests.base import DomainsTestCase, make_bundle, probe_returning
from apps.notifications import store_email

T0 = timezone.now().replace(microsecond=0)
EMAIL = dict(DOMAINS_EMAIL_ENABLED=True, DOMAINS_MAX_EMAIL_DOMAINS=2, RESEND_REGION="us-east-1")


def minutes(n):
    return timedelta(minutes=n)


@override_settings(**EMAIL)
class FlowCase(DomainsTestCase):
    def setUp(self):
        super().setUp()
        self.bundle = make_bundle()
        self.resend, self.cf = self.bundle.fakes["resend"], self.bundle.fakes["cloudflare"]
        self.site_ = self.site()
        self.claim_ = self.claim(self.site_, "brand.com", ClaimState.REGISTERED)
        self.cf.add_zone("brand.com")

    def step(self, now=T0, claim=None):
        claim = claim or self.claim_
        word = email_track.advance(claim.pk, providers=self.bundle, now=now)
        claim.refresh_from_db()
        return word

    def to_verifying(self, now=T0):
        self.assertEqual(self.step(now), "verifying")
        self.assertEqual(self.claim_.email_state, EmailState.VERIFYING)

    def verified(self):
        self.to_verifying()
        self.resend.set_status(self.claim_.resend_domain_id, "verified")
        self.assertEqual(self.step(T0 + minutes(6)), "verified")


class TrackTests(FlowCase):
    def test_the_whole_track_to_verified(self):
        self.to_verifying()
        c = self.claim_
        self.assertEqual(c.resend_region, "us-east-1")
        self.assertTrue(c.resend_domain_id)
        self.assertEqual(self.resend.verify_calls, 1)
        self.resend.set_status(c.resend_domain_id, "verified")
        self.assertEqual(self.step(T0 + minutes(6)), "verified")
        self.assertEqual((c.email_state, c.resend_status), (EmailState.VERIFIED, "verified"))
        self.assertEqual(c.email_verified_at, T0 + minutes(6))

    def test_records_are_written_dns_only_with_recorded_ids_in_the_zone(self):
        self.to_verifying()
        zone_id = self.cf.zones["brand.com"]["id"]
        rows = DomainDnsRecord.objects.filter(claim=self.claim_, purpose="resend")
        self.assertEqual(rows.count(), 3)
        for row in rows:
            live = self.cf.records[zone_id][row.record_id]
            self.assertIs(live["proxied"], False)
            self.assertEqual((live["type"], live["name"], live["content"]), (row.record_type, row.name, row.content))
            self.assertEqual(row.zone_id, zone_id)
            self.assertTrue(row.name.endswith(".brand.com"))
        mx = rows.get(record_type="MX")
        self.assertEqual(mx.priority, 10)
        self.assertEqual(mx.name, "send.brand.com")

    def test_nothing_is_hard_coded_different_values_are_written_as_given(self):
        values = [
            {"record": "DKIM", "name": "zz._domainkey", "type": "TXT", "ttl": "Auto", "status": "not_started",
             "value": "p=DIFFERENT-KEY-1"},
            {"record": "SPF", "name": "bounce", "type": "CNAME", "ttl": "Auto", "status": "not_started",
             "value": "other-return-path.example"},
        ]
        self.resend.template = lambda name: [dict(v) for v in values]
        self.to_verifying()
        zone_id = self.cf.zones["brand.com"]["id"]
        written = sorted((r["type"], r["name"], r["content"]) for r in self.cf.records[zone_id].values())
        self.assertEqual(written, [
            ("CNAME", "bounce.brand.com", "other-return-path.example"),
            ("TXT", "zz._domainkey.brand.com", "p=DIFFERENT-KEY-1"),
        ])

    def test_create_is_the_site_name_in_the_fixed_region_sending_only(self):
        self.to_verifying()
        body = [r[2] for r in self.resend.requests if r[0] == "POST" and r[1] == "/domains"][0]
        self.assertEqual(body, {"name": "brand.com", "region": "us-east-1",
                                "capabilities": {"sending": "enabled", "receiving": "disabled"}})

    def test_a_record_type_outside_txt_mx_cname_fails_the_email_not_the_website(self):
        self.resend.template = lambda name: [{"record": "X", "name": "a", "type": "A", "value": "1.2.3.4", "ttl": "Auto"}]
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(), "failed")
        self.assertEqual(self.claim_.email_state, EmailState.FAILED)
        self.assertEqual(self.cf.records[self.cf.zones["brand.com"]["id"]], {})

    def test_verify_polling_waits_between_reads_and_keeps_going_while_pending(self):
        self.to_verifying()
        calls = len(self.resend.calls)
        self.assertEqual(self.step(T0 + minutes(1)), "verifying")  # inside the poll interval: no call
        self.assertEqual(len(self.resend.calls), calls)
        self.assertEqual(self.step(T0 + minutes(6)), "verifying")  # polled: still pending
        self.assertEqual(self.claim_.resend_status, "pending")
        self.assertEqual(self.claim_.email_state, EmailState.VERIFYING)
        self.resend.set_status(self.claim_.resend_domain_id, "partially_verified")
        self.assertEqual(self.step(T0 + minutes(12)), "verifying")
        self.resend.set_status(self.claim_.resend_domain_id, "verified")
        self.assertEqual(self.step(T0 + minutes(18)), "verified")

    def test_verify_is_called_again_while_resend_says_not_started(self):
        self.resend.auto_status = "not_started"
        self.to_verifying()
        self.assertEqual(self.step(T0 + minutes(6)), "verifying")
        self.assertEqual(self.resend.verify_calls, 2)

    def test_72_hours_without_verification_is_email_failed(self):
        self.to_verifying()
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(T0 + timedelta(hours=73)), "failed")
        self.assertEqual(self.claim_.email_state, EmailState.FAILED)

    def test_resend_failed_is_email_failed(self):
        self.to_verifying()
        self.resend.set_status(self.claim_.resend_domain_id, "failed")
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(T0 + minutes(6)), "failed")

    def test_a_create_that_says_exists_is_never_adopted(self):
        self.resend.domains["pre"] = {"id": "pre", "name": "brand.com", "status": "verified", "region": "us-east-1", "records": []}
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(), "failed")
        self.assertEqual(self.claim_.resend_domain_id, "")
        self.assertEqual(DomainDnsRecord.objects.count(), 0)
        self.assertNotIn(("GET", "/domains/pre"), self.resend.calls)

    def test_a_record_we_did_not_write_is_in_the_way(self):
        zone_id = self.cf.zones["brand.com"]["id"]
        self.cf.records[zone_id]["a" * 32] = {"id": "a" * 32, "type": "MX", "name": "send.brand.com", "content": "mx.theirs.example"}
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(), "failed")
        self.assertEqual(self.claim_.email_state, EmailState.FAILED)
        self.assertEqual(self.resend.verify_calls, 0)

    def test_a_crash_after_creation_resumes_without_a_second_create(self):
        self.cf.fail_next(500)  # the zone read fails after the Resend create was recorded
        self.assertEqual(self.step(), "retry")
        self.assertEqual(self.claim_.email_state, EmailState.DNS)
        self.assertTrue(self.claim_.resend_domain_id)
        self.assertEqual(self.step(T0 + minutes(1)), "verifying")
        self.assertEqual(len([c for c in self.resend.calls if c == ("POST", "/domains")]), 1)

    def test_a_rerun_after_partial_dns_writes_does_not_duplicate_records(self):
        self.to_verifying()
        Domain.objects.filter(pk=self.claim_.pk).update(email_state=EmailState.DNS)
        self.step(T0 + minutes(10))
        self.assertEqual(DomainDnsRecord.objects.filter(purpose="resend").count(), 3)
        self.assertEqual(len(self.cf.records[self.cf.zones["brand.com"]["id"]]), 3)

    def test_a_missing_zone_waits_then_fails_after_72_hours(self):
        del self.cf.zones["brand.com"]
        self.assertEqual(self.step(), "waiting_zone")
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(T0 + timedelta(hours=73)), "failed")

    def test_switch_off_makes_no_provider_call_and_the_sweep_skips_it(self):
        with override_settings(DOMAINS_EMAIL_ENABLED=False):
            self.assertEqual(self.step(), "off")
            self.assertEqual(email_track.due_ids(), [])
            tasks.run_sweep(bundle=self.bundle)
        self.assertEqual(self.resend.calls, [])
        self.assertEqual(self.claim_.email_state, EmailState.NONE)

    def test_a_site_off_the_payment_gate_does_nothing(self):
        Subscription.objects.filter(site=self.site_).update(state=Subscription.State.SUSPENDED)
        self.assertEqual(self.step(), "site_off")
        self.assertEqual(self.resend.calls, [])

    def test_a_forged_claim_name_sends_nothing(self):
        Domain.objects.filter(pk=self.claim_.pk).update(name="victim.com")
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(self.step(), "refused")
        self.assertEqual(self.resend.calls, [])

    def test_an_unsucceeded_registration_is_not_worked_on(self):
        claim = self.claim(self.site(), "wait.com", ClaimState.REGISTERED, registration=RegistrationState.SENT)
        self.assertEqual(self.step(claim=claim), "waiting")
        self.assertEqual(self.resend.calls, [])


class WorkerPassTests(FlowCase):
    def test_the_minute_sweep_carries_the_track_beside_the_website(self):
        probe = probe_returning(self.site_.public_id)
        for i in range(6):
            tasks.run_sweep(bundle=self.bundle, now=T0 + minutes(i * 6), probe_fn=probe)
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.state, ClaimState.LIVE)
        self.assertEqual(self.claim_.email_state, EmailState.VERIFYING)
        self.resend.set_status(self.claim_.resend_domain_id, "verified")
        out = tasks.run_sweep(bundle=self.bundle, now=T0 + minutes(60), probe_fn=probe)
        self.assertEqual(out["email_verified"], 1)


class CapacityTests(FlowCase):
    def test_at_capacity_the_email_waits_and_the_website_goes_live(self):
        with override_settings(DOMAINS_MAX_EMAIL_DOMAINS=0):
            with self.assertLogs("domains", level=logging.ERROR) as logs:
                self.assertEqual(self.step(), "waiting_capacity")
            self.assertIn("email_waiting_capacity", logs.output[0])
            self.assertEqual(self.claim_.email_state, EmailState.WAITING_CAPACITY)
            self.assertEqual(self.claim_.email_waiting_since, T0)
            self.assertEqual(self.step(T0 + minutes(5)), "waiting_capacity")
            self.assertEqual(self.claim_.email_waiting_since, T0)  # the time it began stays
            probe = probe_returning(self.site_.public_id)
            for _ in range(4):
                tasks.run_sweep(bundle=self.bundle, now=T0 + minutes(10), probe_fn=probe)
            self.claim_.refresh_from_db()
            self.assertEqual(self.claim_.state, ClaimState.LIVE)  # never blocked
            self.assertEqual(self.claim_.email_state, EmailState.WAITING_CAPACITY)
        self.assertEqual(self.resend.calls, [])
        self.assertEqual(DomainEvent.objects.filter(claim=self.claim_, kind="email_waiting_capacity").count(), 1)

    def test_it_resumes_when_a_slot_exists(self):
        with override_settings(DOMAINS_MAX_EMAIL_DOMAINS=0), self.assertLogs("domains", level=logging.ERROR):
            self.step()
        self.assertEqual(self.step(T0 + minutes(30)), "verifying")
        self.assertEqual(self.claim_.email_state, EmailState.VERIFYING)

    def test_our_own_domains_fill_the_slots_and_a_finished_one_frees_one(self):
        with override_settings(DOMAINS_MAX_EMAIL_DOMAINS=1):
            self.assertEqual(self.step(), "verifying")
            second = self.claim(self.site(), "second.com", ClaimState.REGISTERED)
            self.cf.add_zone("second.com")
            with self.assertLogs("domains", level=logging.ERROR):
                self.assertEqual(self.step(claim=second), "waiting_capacity")
            Domain.objects.filter(pk=self.claim_.pk).update(state=ClaimState.ENDED, resend_domain_id="", email_state=EmailState.NONE)
            self.assertEqual(self.step(T0 + minutes(1), claim=second), "verifying")

    def test_the_purchase_code_knows_nothing_of_email_capacity(self):
        import inspect

        from apps.domains import purchase
        source = inspect.getsource(purchase).lower()
        for word in ("email_track", "resend", "email_state", "max_email"):
            self.assertNotIn(word, source)

    def test_waiting_claims_are_served_longest_first(self):
        a = self.claim(self.site(), "a.com", ClaimState.REGISTERED)
        b = self.claim(self.site(), "b.com", ClaimState.REGISTERED)
        Domain.objects.filter(pk=b.pk).update(email_state=EmailState.WAITING_CAPACITY, email_waiting_since=T0 - minutes(5))
        Domain.objects.filter(pk=a.pk).update(email_state=EmailState.WAITING_CAPACITY, email_waiting_since=T0 - minutes(1))
        ids = email_track.due_ids()
        self.assertLess(ids.index(b.pk), ids.index(a.pk))


class FailureIsolationTests(FlowCase):
    def test_email_failed_never_fails_the_website(self):
        Domain.objects.filter(pk=self.claim_.pk).update(email_state=EmailState.FAILED)
        probe = probe_returning(self.site_.public_id)
        for _ in range(4):
            tasks.run_sweep(bundle=self.bundle, now=T0, probe_fn=probe)
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.state, ClaimState.LIVE)
        self.assertEqual(self.claim_.email_state, EmailState.FAILED)

    def test_a_failing_email_step_does_not_move_the_website_state(self):
        with self.assertLogs("domains", level=logging.ERROR):
            self.resend.fail_next(403)
            self.assertEqual(self.step(), "refused")
        self.assertEqual(self.claim_.email_state, EmailState.CREATING)  # kept for the next pass
        self.assertEqual(self.claim_.state, ClaimState.REGISTERED)

    def test_the_operator_retry_of_a_failed_email_leaves_the_website_alone(self):
        self.verified()
        Domain.objects.filter(pk=self.claim_.pk).update(email_state=EmailState.FAILED)
        self.claim_.refresh_from_db()
        self.assertTrue(machine.retry(self.claim_, now=T0))
        self.assertEqual((self.claim_.state, self.claim_.email_state), (ClaimState.REGISTERED, EmailState.DNS))
        self.assertFalse(machine.retry(self.claim_, now=T0))  # nothing failed now

    def test_the_retry_without_a_resend_domain_starts_over(self):
        Domain.objects.filter(pk=self.claim_.pk).update(email_state=EmailState.FAILED)
        self.claim_.refresh_from_db()
        self.assertTrue(email_track.retry(self.claim_, now=T0))
        self.assertEqual(self.claim_.email_state, EmailState.NONE)


class EventDetailTests(FlowCase):
    def test_clean_keeps_whitelisted_short_values_only(self):
        got = events.clean({
            "reason": "x" * 500, "code": "auth", "count": 3, "types": ["TXT", "MX"],
            "body": {"records": [1]}, "api_key": "re_secret", "value": "p=KEY", "id": "abc",
            "status": {"nested": 1},
        })
        self.assertEqual(set(got), {"reason", "code", "count", "types"})
        self.assertEqual(len(got["reason"]), 80)

    def test_the_track_writes_only_whitelisted_detail_and_no_value_or_id(self):
        self.verified()
        flat = repr([e.detail for e in DomainEvent.objects.filter(claim=self.claim_)])
        for event in DomainEvent.objects.filter(claim=self.claim_):
            self.assertLessEqual(set(event.detail), events.DETAIL_KEYS | {"reason"})
        self.assertNotIn(self.claim_.resend_domain_id, flat)
        self.assertNotIn("p=FAKEDKIM", flat)
        self.assertNotIn("tok-resend", flat)
        kinds = {e.kind for e in DomainEvent.objects.filter(claim=self.claim_)}
        self.assertTrue({"resend_create_sent", "resend_created", "email_state"} <= kinds)


class RemovalTests(FlowCase):
    def removal(self):
        self.verified()
        Domain.objects.filter(pk=self.claim_.pk).update(state=ClaimState.REMOVAL_PENDING)

    def test_removal_deletes_the_resend_domain_and_the_recorded_records(self):
        self.removal()
        did, zone_id = self.claim_.resend_domain_id, self.cf.zones["brand.com"]["id"]
        self.assertEqual(machine.advance(self.claim_.pk, providers=self.bundle, now=T0 + minutes(30)), "ended")
        self.claim_.refresh_from_db()
        self.assertNotIn(did, self.resend.domains)
        self.assertEqual(self.cf.records[zone_id], {})
        self.assertFalse(DomainDnsRecord.objects.filter(claim=self.claim_, removed_at__isnull=True).exists())
        self.assertEqual((self.claim_.state, self.claim_.email_state, self.claim_.resend_status),
                         (ClaimState.ENDED, EmailState.NONE, ""))
        calls = self.resend.calls
        self.assertLess(calls.index(("GET", f"/domains/{did}")), calls.index(("DELETE", f"/domains/{did}")))
        self.assertTrue(DomainRegistration.objects.filter(site=self.site_, name="brand.com").exists())  # never deleted

    def test_a_resend_domain_with_another_name_is_not_deleted_and_the_claim_waits(self):
        self.removal()
        self.resend.domains[self.claim_.resend_domain_id]["name"] = "other.com"
        with self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(machine.advance(self.claim_.pk, providers=self.bundle, now=T0 + minutes(30)), "refused")
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.state, ClaimState.REMOVAL_PENDING)
        self.assertIn(self.claim_.resend_domain_id, self.resend.domains)
        self.assertEqual(self.cf.records[self.cf.zones["brand.com"]["id"]], {})  # Resend does not hold the DNS back

    def test_an_already_gone_resend_domain_counts_as_removed(self):
        self.removal()
        del self.resend.domains[self.claim_.resend_domain_id]
        self.assertEqual(machine.advance(self.claim_.pk, providers=self.bundle, now=T0 + minutes(30)), "ended")

    def test_removal_with_the_switch_off_waits_rather_than_leaving_a_domain_behind(self):
        self.removal()
        with override_settings(DOMAINS_EMAIL_ENABLED=False), self.assertLogs("domains", level=logging.ERROR):
            self.assertEqual(machine.advance(self.claim_.pk, providers=self.bundle, now=T0 + minutes(30)), "stuck")
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.state, ClaimState.REMOVAL_PENDING)

    def test_a_claim_that_never_made_a_resend_domain_removes_as_before(self):
        Domain.objects.filter(pk=self.claim_.pk).update(state=ClaimState.REMOVAL_PENDING)
        self.assertEqual(machine.advance(self.claim_.pk, providers=self.bundle, now=T0), "ended")
        self.assertEqual(self.resend.calls, [])


class DailyReadTests(FlowCase):
    def test_the_daily_read_promotes_to_active_and_tracks_status(self):
        self.verified()
        out = sweeps.resend_daily(self.bundle, T0 + timedelta(days=1))
        self.assertEqual(out["read"], 1)
        self.claim_.refresh_from_db()
        self.assertEqual((self.claim_.email_state, self.claim_.resend_status), (EmailState.ACTIVE, "verified"))
        self.resend.set_status(self.claim_.resend_domain_id, "temporary_failure")
        with self.assertLogs("domains", level=logging.ERROR):
            sweeps.resend_daily(self.bundle, T0 + timedelta(days=2))
        self.claim_.refresh_from_db()
        self.assertEqual(self.claim_.resend_status, "temporary_failure")

    def test_it_alerts_on_unrecorded_domains_and_ignores_the_platforms_own(self):
        self.verified()
        self.resend.domains["rogue"] = {"id": "rogue", "name": "rogue.com", "status": "verified", "region": "x", "records": []}
        self.resend.domains["plat"] = {"id": "plat", "name": "mail.negosyonline.com", "status": "verified", "region": "x", "records": []}
        with self.assertLogs("domains", level=logging.ERROR) as logs:
            out = sweeps.resend_daily(self.bundle, T0)
        self.assertEqual(out["unrecorded"], ["rogue.com"])
        self.assertEqual(len(logs.output), 1)

    def test_the_daily_task_reads_resend_only_while_the_switch_is_on(self):
        out = tasks.run_daily(bundle=self.bundle, now=T0)
        self.assertIn("resend", out)
        with override_settings(DOMAINS_EMAIL_ENABLED=False):
            self.assertNotIn("resend", tasks.run_daily(bundle=self.bundle, now=T0))


@override_settings(STORE_EMAILS_BRANDED=True, STORE_SENDERS="")
class SenderLookupTests(FlowCase):
    def send(self, site=None):
        mail.outbox.clear()
        store_email.send_store_email(site or self.site_, to="b@x.test", category="c", subject="s", body="b")
        return mail.outbox[0].from_email

    def go_live_verified(self):
        self.verified()
        Domain.objects.filter(pk=self.claim_.pk).update(state=ClaimState.LIVE, resend_checked_at=timezone.now())

    def test_the_sender_is_orders_at_the_registered_domain_while_verified(self):
        self.go_live_verified()
        self.assertIn("<orders@brand.com>", self.send())

    def test_the_local_part_is_the_claims_choice_and_no_full_address_is_stored(self):
        self.go_live_verified()
        Domain.objects.filter(pk=self.claim_.pk).update(sender_local_part="hello")
        self.assertIn("<hello@brand.com>", self.send())
        self.assertFalse([f.name for f in Domain._meta.get_fields() if "address" in f.name])

    def test_it_falls_back_without_the_branded_switch_or_with_the_domains_switch_off(self):
        self.go_live_verified()
        with override_settings(STORE_EMAILS_BRANDED=False):
            self.assertEqual(store_email.domain_sender(self.site_), "")
        with override_settings(DOMAINS_ENABLED=False):
            self.assertEqual(store_email.domain_sender(self.site_), "")

    def test_it_does_not_need_the_workers_email_switch(self):
        self.go_live_verified()
        with override_settings(DOMAINS_EMAIL_ENABLED=False):  # the api service does not have it
            self.assertIn("<orders@brand.com>", self.send())

    def test_it_falls_back_on_every_status_but_verified(self):
        self.go_live_verified()
        for status in ("temporary_failure", "failed", "pending", "partially_verified", ""):
            Domain.objects.filter(pk=self.claim_.pk).update(resend_status=status)
            with self.subTest(status=status):
                self.assertIn("no-reply@negosyoonline.com", self.send())

    def test_it_falls_back_on_a_stale_reading_and_on_an_unverified_track(self):
        self.go_live_verified()
        Domain.objects.filter(pk=self.claim_.pk).update(resend_checked_at=timezone.now() - timedelta(days=5))
        self.assertEqual(store_email.domain_sender(self.site_), "")
        Domain.objects.filter(pk=self.claim_.pk).update(resend_checked_at=timezone.now(), email_state=EmailState.VERIFYING)
        self.assertEqual(store_email.domain_sender(self.site_), "")

    def test_it_needs_a_holding_claim_and_not_one_being_removed(self):
        self.go_live_verified()
        for state in (ClaimState.REGISTERED, ClaimState.ZONE, ClaimState.REMOVAL_PENDING, ClaimState.ENDED, ClaimState.FAILED):
            Domain.objects.filter(pk=self.claim_.pk).update(state=state)
            with self.subTest(state=state):
                self.assertEqual(store_email.domain_sender(self.site_), "")
        for state in (ClaimState.CONNECTING, ClaimState.ACTIVATING, ClaimState.LIVE, ClaimState.ATTENTION):
            Domain.objects.filter(pk=self.claim_.pk).update(state=state)
            with self.subTest(state=state):
                self.assertEqual(store_email.domain_sender(self.site_), "orders@brand.com")

    def test_a_forged_claim_name_sends_as_no_one(self):
        self.go_live_verified()
        Domain.objects.filter(pk=self.claim_.pk).update(name="victim.com")
        self.assertEqual(store_email.domain_sender(self.site_), "")

    def test_the_lookup_is_keyed_on_the_sites_own_claim(self):
        self.go_live_verified()
        other = self.site()
        self.assertEqual(store_email.domain_sender(other), "")
        self.assertIn("no-reply@negosyoonline.com", self.send(other))

    def test_an_explicit_store_senders_pair_still_wins(self):
        self.go_live_verified()
        with override_settings(STORE_SENDERS=f"{self.site_.public_id}=orders@liveformore.test"):
            self.assertIn("<orders@liveformore.test>", self.send())

    def test_a_failing_lookup_is_the_platform_address(self):
        from unittest import mock
        self.go_live_verified()
        with mock.patch("apps.domains.models.Domain.objects") as objects, self.assertLogs(
            "apps.notifications.store_email", level="WARNING"
        ):
            objects.filter.side_effect = RuntimeError("db down")
            self.assertEqual(store_email.domain_sender(self.site_), "")
        self.assertIn("<orders@brand.com>", self.send())


class ChecksTests(DomainsTestCase):
    @override_settings(DOMAINS_EMAIL_ENABLED=True, RESEND_DOMAINS_API_KEY="", RESEND_REGION="", DOMAINS_MAX_EMAIL_DOMAINS=0)
    def test_the_deploy_check_names_the_unset_settings_only_while_the_switch_is_on(self):
        from apps.domains.checks import check_domains
        found = [w for w in check_domains(None) if w.id == "domains.W005"]
        self.assertEqual(len(found), 1)
        for name in ("RESEND_DOMAINS_API_KEY", "RESEND_REGION", "DOMAINS_MAX_EMAIL_DOMAINS"):
            self.assertIn(name, found[0].msg)
        with override_settings(DOMAINS_EMAIL_ENABLED=False):
            self.assertEqual([w for w in check_domains(None) if w.id == "domains.W005"], [])
