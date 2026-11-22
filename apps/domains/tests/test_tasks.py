"""The tasks do nothing while off, and go through the same machine when on."""
from unittest import mock

from django.test import override_settings

from apps.domains import providers, sweeps, tasks
from apps.domains.models import ClaimState, Domain
from apps.domains.tests.base import DomainsTestCase, make_bundle, probe_returning


class TaskTests(DomainsTestCase):
    @override_settings(DOMAINS_ENABLED=False)
    def test_everything_is_off_by_default(self):
        with mock.patch.object(providers, "current") as current:
            for task in (tasks.sweep, tasks.hourly_check, tasks.daily_sweep):
                self.assertEqual(task(), {"skipped": "switched off"})
            current.assert_not_called()

    @override_settings(DOMAINS_PROVIDER="off")
    def test_provider_off_takes_no_step(self):
        self.assertIsNone(providers.current())
        claim = self.claim()
        self.assertEqual(tasks.sweep(), {"off": 1})
        claim.refresh_from_db()
        self.assertEqual(claim.state, ClaimState.REGISTERED)

    @override_settings(DOMAINS_PROVIDER_PAUSED=True)
    def test_paused_returns_no_providers_and_still_queues_removals(self):
        self.assertIsNone(providers.current())

    @override_settings(DOMAINS_PROVIDER="fake", DEBUG=False)
    def test_a_fake_without_debug_is_refused(self):
        self.assertIsNone(providers.current())

    @override_settings(DOMAINS_PROVIDER="fake", DEBUG=True)
    def test_fake_mode_runs_the_real_adapters_over_in_memory_fakes(self):
        providers.reset_fakes()
        self.addCleanup(providers.reset_fakes)
        bundle = providers.current()
        self.assertIs(bundle, providers.current())
        site = self.site()
        claim = self.claim(site)
        bundle.fakes["cloudflare"].add_zone("brand.com")
        probe = probe_returning(site.public_id)
        for _ in range(4):
            tasks.run_sweep(bundle=bundle, probe_fn=probe)
        claim.refresh_from_db()
        self.assertEqual(claim.state, ClaimState.LIVE)

    @override_settings(DOMAINS_PROVIDER="real", CLOUDFLARE_DNS_API_TOKEN="x", VERCEL_API_TOKEN="x")
    def test_real_mode_builds_the_adapters_but_a_test_never_calls_them(self):
        bundle = providers.current()
        self.assertIsNone(bundle.registrar_read)  # no Registrar adapter in B1

    def test_the_hourly_task_checks_live_claims_only(self):
        bundle = make_bundle()
        site = self.site()
        live = self.live_claim(site)
        self.claim(self.site(), "other.com", ClaimState.REGISTERED)
        out = tasks.run_hourly(bundle=bundle, probe_fn=probe_returning(site.public_id))
        self.assertEqual(sum(out.values()), 1)

    def test_daily_runs_the_audits_and_skips_without_a_provider(self):
        self.assertIn("skipped", tasks.run_daily(bundle=None))
        bundle = make_bundle()
        out = tasks.run_daily(bundle=bundle)
        self.assertEqual(set(out), {"removals_queued", "zone_audit", "vercel", "registrations"})
        self.assertEqual(out["registrations"], {"skipped": "no registrar adapter (B2)"} if bundle.registrar_read is None else out["registrations"])

    def test_registration_read_records_expiry_and_alerts_on_auto_renew_off(self):
        from datetime import timedelta
        from django.utils import timezone
        bundle = make_bundle()
        claim = self.live_claim(name="brand.com")
        now = timezone.now()
        bundle.registrar_read.registrations["brand.com"] = {"expires_at": now + timedelta(days=30), "auto_renew": False}
        with self.assertLogs("domains", level="ERROR") as logs:
            self.assertEqual(sweeps.registration_read(bundle, now), {"read": 1})
        self.assertTrue(any("auto_renew_off" in m for m in logs.output))
        self.assertTrue(any("expires_soon" in m for m in logs.output))
        reg = claim.registration()
        reg.refresh_from_db()
        self.assertIs(reg.auto_renew, False)
        self.assertIsNotNone(reg.auto_renew_checked_at)

    def test_vercel_comparison_alerts_on_unrecorded_and_missing_names(self):
        bundle = make_bundle()
        self.live_claim(name="brand.com")
        from apps.domains.models import DomainHostname
        DomainHostname.objects.filter(role="www").update(vercel_added_at=timezone_now(), vercel_domain_name="www.brand.com")
        bundle.fakes["vercel"].domains["rogue.example"] = {"name": "rogue.example"}
        bundle.fakes["vercel"].domains["x.negosyotest.com"] = {"name": "x.negosyotest.com"}  # under the parent: ignored
        with self.assertLogs("domains", level="ERROR"):
            out = sweeps.vercel_compare(bundle)
        self.assertEqual(out, {"unrecorded": ["rogue.example"], "missing": ["www.brand.com"]})


def timezone_now():
    from django.utils import timezone
    return timezone.now()
