from django.test import SimpleTestCase, override_settings

from apps.domains import names
from apps.domains.models import ClaimState
from apps.domains.tests.base import PLATFORM, PSL, DomainsTestCase


class NormaliseTests(SimpleTestCase):
    def test_lowercases_and_strips_the_trailing_dot(self):
        self.assertEqual(names.normalise("Brand.COM."), "brand.com")

    def test_refuses_what_is_not_a_bare_ascii_name(self):
        for raw in ("", " brand.com", "brand.com ", "https://brand.com", "brand.com:8080", "brand.com/x",
                    "*.brand.com", "br_and.com", "bränd.com", "xn--bd-xyz.com", "127.0.0.1", "brand",
                    "-brand.com", "a@b.com", "brand.c0m", None, 5):
            with self.subTest(raw=raw):
                with self.assertRaises(names.NameRefused):
                    names.normalise(raw)


@override_settings(DOMAINS_PSL_FILE=PSL, SITE_PARENT_HOST="negosyotest.com", DOMAINS_PLATFORM_ZONES=PLATFORM)
class PublicSuffixTests(DomainsTestCase):
    def test_registrable_domain(self):
        self.assertEqual(names.registrable_domain("www.brand.com"), "brand.com")
        self.assertEqual(names.registrable_domain("shop.brand.co.uk"), "brand.co.uk")
        self.assertIsNone(names.registrable_domain("co.uk"))
        self.assertIsNone(names.registrable_domain("com"))

    def test_wildcard_and_exception_rules(self):
        self.assertIsNone(names.registrable_domain("foo.ck"))  # *.ck makes foo.ck a suffix
        self.assertEqual(names.registrable_domain("bar.foo.ck"), "bar.foo.ck")
        self.assertEqual(names.registrable_domain("www.ck"), "www.ck")  # !www.ck

    def test_missing_list_refuses_every_name(self):
        with override_settings(DOMAINS_PSL_FILE="/nonexistent/psl.dat"):
            names._cache.update(key=None, rules=None)
            self.assertFalse(names.psl_available())
            self.assertEqual(names.refusal("brand.com"), names.PSL_MISSING)
            self.assertEqual(names.refusal("anything-else.net"), names.PSL_MISSING)

    def test_empty_list_counts_as_missing(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".dat") as handle:
            handle.write("// only a comment\n")
            handle.flush()
            with override_settings(DOMAINS_PSL_FILE=handle.name):
                self.assertFalse(names.psl_available())
                self.assertEqual(names.refusal("brand.com"), names.PSL_MISSING)


class RefusalTests(DomainsTestCase):
    def test_a_plain_registrable_name_is_accepted(self):
        for name in ("brand.com", "my-shop.store", "brand.co.uk"):
            with self.subTest(name=name):
                self.assertEqual(names.refusal(name), "")

    def test_public_suffixes_and_subdomains_are_refused(self):
        for name in ("com", "co.uk", "www.brand.com", "shop.brand.com", "foo.ck"):
            with self.subTest(name=name):
                self.assertNotEqual(names.refusal(name), "")

    def test_platform_names_and_everything_under_them(self):
        for name in ("negosyonline.com", "negosyoonline.com", "api.negosyonline.com", "x.negosyoonline.com"):
            with self.subTest(name=name):
                self.assertNotEqual(names.refusal(name), "")

    def test_the_site_parent_host_registrable_domain(self):
        self.assertNotEqual(names.refusal("negosyotest.com"), "")

    def test_protected_zones_in_the_setting(self):
        self.assertNotEqual(names.refusal("santebarleyph.com"), "")
        with override_settings(DOMAINS_PLATFORM_ZONES=("live4moreonline.store",)):
            self.assertNotEqual(names.refusal("live4moreonline.store"), "")

    def test_platform_hosts_from_settings(self):
        with override_settings(HQ_HOST="hq.example-platform.net", DASHBOARD_URL="https://dash.example-platform.org"):
            self.assertNotEqual(names.refusal("example-platform.net"), "")
            self.assertNotEqual(names.refusal("example-platform.org"), "")

    def test_a_name_held_by_another_claim(self):
        self.live_claim(name="taken.com")
        self.assertNotEqual(names.refusal("taken.com"), "")
        own = self.live_claim(name="mine.com")
        self.assertEqual(names.refusal("mine.com", exclude_claim=own), "")

    def test_a_connected_host_of_the_manual_path(self):
        from apps.sites.models import SiteCustomHost
        site = self.site()
        SiteCustomHost.objects.create(site=site, host="www.manual.store", state=SiteCustomHost.State.ACTIVE)
        self.assertNotEqual(names.refusal("manual.store"), "")


class VendoredPublicSuffixListTests(SimpleTestCase):
    """The list shipped in the repo (the default DOMAINS_PSL_FILE), not the fixture."""

    def test_vendored_file_loads_and_resolves(self):
        from django.conf import settings
        vendored = str(settings.BASE_DIR / "apps" / "domains" / "data" / "public_suffix_list.dat")
        with override_settings(DOMAINS_PSL_FILE=vendored):
            names._cache.update(key=None, rules=None)
            try:
                self.assertTrue(names.psl_available())
                self.assertEqual(names.registrable_domain("example.com"), "example.com")
                self.assertEqual(names.registrable_domain("www.example.com"), "example.com")
                self.assertEqual(names.registrable_domain("foo.co.uk"), "foo.co.uk")
                self.assertEqual(names.registrable_domain("a.b.foo.co.uk"), "foo.co.uk")
                self.assertIsNone(names.registrable_domain("co.uk"))
            finally:
                names._cache.update(key=None, rules=None)
