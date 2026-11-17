"""An explicit custom host: lookup, availability rules, normalisation, uniqueness, canonical URL
and the site_custom_domain command. Subdomain behaviour stays covered by test_api_public."""
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.tests.base import TRIAL_END, make_subscription
from apps.sites import custom_hosts
from apps.sites.models import Site, SiteCustomHost
from apps.sites.tests.base import make_account, make_site
from apps.sites.tests.test_api_public import HOST, ByHostTestCase

CUSTOM = "shop.example.test"


def attach(site, host=CUSTOM, **fields):
    return SiteCustomHost.objects.create(host=host, site=site, **fields)


class LookupTests(ByHostTestCase):
    def test_custom_host_serves_the_intended_site_and_the_subdomain_still_does(self):
        sub = make_subscription()
        other = make_subscription(email="ben@example.test", name="bens-store")
        attach(sub.site)

        body = self.assert_served(self.lookup(CUSTOM))
        self.assertEqual(body["id"], sub.site.public_id)
        self.assertEqual(body["custom_host"], CUSTOM)
        same = self.assert_served(self.lookup())  # the platform subdomain of the same site
        self.assertEqual((same["id"], same["custom_host"]), (sub.site.public_id, ""))
        self.assert_not_found(self.lookup("www." + CUSTOM))  # www is never resolved here
        ben = self.lookup("bens-store.negosyotest.com").json()
        self.assertEqual(ben["id"], other.site.public_id)
        self.assertEqual(ben["custom_host"], "")

    def test_it_accepts_case_port_and_trailing_dot(self):
        sub = make_subscription()
        attach(sub.site)
        for host in (CUSTOM.upper(), CUSTOM + ":443", CUSTOM + ".", CUSTOM.upper() + ".:8000"):
            with self.subTest(host=host):
                self.assertEqual(self.assert_served(self.lookup(host))["id"], sub.site.public_id)

    def test_unknown_lookalike_and_unassigned_hosts_are_refused(self):
        sub = make_subscription()
        attach(sub.site)
        for host in (
            "other.example.test", "www." + CUSTOM, "x." + CUSTOM, "example.test", "shop",
            CUSTOM + ".evil.test", "evil" + CUSTOM, CUSTOM + ":abc", "", "shop.exämple.test",
        ):
            with self.subTest(host=host):
                self.assert_not_found(self.lookup(host))

    def test_a_retired_host_is_refused(self):
        sub = make_subscription()
        attach(sub.site, state="retired", retired_at=timezone.now())
        self.assert_not_found(self.lookup(CUSTOM))
        self.assert_served(self.lookup())

    def test_held_removed_and_not_served_rules_apply_to_the_custom_host(self):
        sub = make_subscription()
        attach(sub.site)
        Site.objects.filter(pk=sub.site_id).update(operator_hold=True)
        self.assert_not_found(self.lookup(CUSTOM))
        Site.objects.filter(pk=sub.site_id).update(operator_hold=False)
        self.assert_served(self.lookup(CUSTOM))
        Site.objects.filter(pk=sub.site_id).update(deleted_at=timezone.now())
        self.assert_not_found(self.lookup(CUSTOM))
        Site.objects.filter(pk=sub.site_id).update(deleted_at=None)
        self.at(TRIAL_END)  # the trial has ended: not served, on either host
        self.assert_not_found(self.lookup(CUSTOM))
        self.assert_not_found(self.lookup())

    def test_a_host_is_not_matched_from_a_fragment_of_another_sites_host(self):
        sub = make_subscription()
        attach(sub.site, "live4.example.test")
        self.assert_not_found(self.lookup("live4more.example.test"))
        self.assert_not_found(self.lookup("example.test"))


class RuleTests(TestCase):
    def test_normalised_on_save_and_unique(self):
        site = make_site(make_account())
        row = attach(site, "Shop.Example.TEST.")
        self.assertEqual(row.host, CUSTOM)
        other = make_site(make_account("b@example.test"), name="bens")
        with self.assertRaises(IntegrityError), transaction.atomic():
            attach(other, CUSTOM.upper())

    def test_db_rejects_an_uppercase_host_even_when_save_is_bypassed(self):
        site = make_site(make_account())
        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteCustomHost.objects.bulk_create([SiteCustomHost(host="Bad.Example.test", site=site)])

    def test_one_active_host_per_site(self):
        site = make_site(make_account())
        attach(site)
        with self.assertRaises(IntegrityError), transaction.atomic():
            attach(site, "two.example.test")

    @override_settings(
        SITE_PARENT_HOST="negosyotest.com", HQ_HOST="hq.example.org",
        DASHBOARD_URL="https://dash.example.org", SITE_URL="https://www.example.org",
        API_ALLOWED_ORIGINS=["https://app.example.org"], ALLOWED_HOSTS=["api.example.org"],
    )
    def test_refused_hosts(self):
        site = make_site(make_account())
        for bad in (
            "", " shop.example.test", "exämple.test", "xn--e1afmkfd.test", "shop", "1.2.3.4",
            "shop.example.test:443", "https://shop.example.test", "shop.example.test/x",
            "a@shop.example.test", "-bad.example.test", "bad_.example.test", "a..b.test",
            "negosyotest.com", "x.negosyotest.com", "anas.negosyotest.com", "hq.example.org",
            "dash.example.org", "www.example.org", "app.example.org", "api.example.org",
            "shop.t1", "a" * 64 + ".test", ("a." * 130) + "test",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                custom_hosts.validated(bad)
            with self.subTest(save=bad), self.assertRaises(ValidationError):
                SiteCustomHost(host=bad, site=site).save()
        self.assertEqual(custom_hosts.validated("Live.Example.Test."), "live.example.test")


@override_settings(SITE_PARENT_HOST="negosyotest.com")
class CanonicalUrlTests(TestCase):
    def test_public_host_prefers_the_active_custom_host(self):
        site = make_site(make_account())
        self.assertEqual(custom_hosts.public_host(site), "anas-store.negosyotest.com")
        self.assertEqual(custom_hosts.public_base_url(site), "https://anas-store.negosyotest.com")
        row = attach(site)
        self.assertEqual(custom_hosts.public_base_url(site), f"https://{CUSTOM}")
        row.state, row.retired_at = "retired", timezone.now()
        row.save()
        self.assertEqual(custom_hosts.public_host(site), "anas-store.negosyotest.com")

    def test_customer_facing_links_use_the_custom_host(self):
        from apps.pack import notices, policies

        site = make_site(make_account())
        self.assertEqual(notices.host_for(site), "anas-store.negosyotest.com")
        self.assertEqual(
            notices.dashboard_url(site), "https://anas-store.negosyotest.com/dashboard/"
        )
        attach(site)
        self.assertEqual(notices.host_for(site), CUSTOM)
        self.assertEqual(policies.host_for(site), CUSTOM)
        self.assertEqual(notices.dashboard_url(site), f"https://{CUSTOM}/dashboard/")

    def test_no_host_at_all_keeps_the_old_fallbacks(self):
        from apps.pack import notices

        site = make_site(make_account())
        with override_settings(SITE_PARENT_HOST=""):
            self.assertEqual(notices.host_for(site), "")
            self.assertEqual(notices.dashboard_url(site), "the store's website")


@override_settings(SITE_PARENT_HOST="negosyotest.com")
class CommandTests(TestCase):
    def setUp(self):
        self.site = make_site(make_account(), name="livemore")

    def run_cmd(self, *args, host=CUSTOM, name="livemore"):
        out = StringIO()
        call_command(
            "site_custom_domain", "--site-subdomain", name, "--host", host, *args, stdout=out
        )
        return out.getvalue()

    def test_dry_run_is_the_default_and_writes_nothing(self):
        text = self.run_cmd()
        self.assertIn(self.site.public_id, text)
        self.assertIn("ana@example.test", text)
        self.assertIn("DRY RUN", text)
        self.assertEqual(SiteCustomHost.objects.count(), 0)
        self.run_cmd("--dry-run")
        self.assertEqual(SiteCustomHost.objects.count(), 0)

    def test_apply_attaches_and_is_idempotent_without_new_tenants_or_owner_change(self):
        sites, owner = Site.objects.count(), self.site.account_id
        self.run_cmd("--apply")
        self.assertEqual(SiteCustomHost.objects.get().site_id, self.site.pk)
        self.assertIn("No change", self.run_cmd("--apply"))
        self.assertEqual(SiteCustomHost.objects.count(), 1)
        self.assertEqual(Site.objects.count(), sites)
        self.site.refresh_from_db()
        self.assertEqual(self.site.account_id, owner)

    def test_refuses_a_host_that_belongs_to_another_site(self):
        other = make_site(make_account("b@example.test"), name="bens")
        attach(other)
        with self.assertRaisesMessage(CommandError, "already belongs to site"):
            self.run_cmd("--apply")
        self.assertFalse(self.site.custom_hosts.exists())

    def test_refuses_to_overwrite_a_different_active_host_of_the_same_site(self):
        attach(self.site, "first.example.test")
        with self.assertRaisesMessage(CommandError, "already has the active host"):
            self.run_cmd("--apply")
        self.assertEqual(self.site.custom_hosts.get().host, "first.example.test")

    def test_unknown_site_bad_host_and_removed_site_are_refused(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--apply", name="nobody")
        with self.assertRaises(CommandError):
            self.run_cmd("--apply", host="negosyotest.com")
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        with self.assertRaises(CommandError):
            self.run_cmd("--apply")
        self.assertEqual(SiteCustomHost.objects.count(), 0)

    def test_apply_and_dry_run_together_is_refused(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--apply", "--dry-run")

    def test_retire_and_reattach(self):
        self.run_cmd("--apply")
        self.run_cmd("--retire")  # dry run
        self.assertEqual(self.site.custom_hosts.get().state, "active")
        self.run_cmd("--retire", "--apply")
        self.assertEqual(self.site.custom_hosts.get().state, "retired")
        self.assertIn("No change", self.run_cmd("--retire", "--apply"))
        self.run_cmd("--apply")
        row = self.site.custom_hosts.get()
        self.assertEqual((row.state, row.retired_at), ("active", None))
