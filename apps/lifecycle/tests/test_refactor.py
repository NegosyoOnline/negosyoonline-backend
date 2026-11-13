"""The per-app blank functions take a site queryset; the account wrappers behave as before
(plan z8uq9mb22v, section 2 and the refactor-safety test of section 9)."""
from django.test import override_settings

from apps.lifecycle.tests.base import LifecycleTestCase, ended_site, second_site
from apps.members import retention as members
from apps.members.models import Member
from apps.meta_ads import retention as meta
from apps.meta_ads.models import MetaSettings
from apps.pack import retention as pack
from apps.payments import retention as payments
from apps.payments.models import SitePaymentAccount
from apps.sante import retention as sante
from apps.sante.models import SanteSiteSettings
from apps.shop import retention as shop
from apps.sites import retention as sites
from apps.sites.models import Site


class SiteFormTests(LifecycleTestCase):
    def setUp(self):
        self.first = ended_site()
        self.account = self.first.site.account
        self.other_site = second_site(self.first)
        for site in (self.first.site, self.other_site):
            SitePaymentAccount.objects.create(
                site=site, kind="bank", account_type="savings", provider_name="BDO",
                account_name="A", account_number="123",
            )
            Member.objects.create(
                site=site, full_name="M", mobile=f"+639171{site.pk:06d}", email=f"m{site.pk}@x.test",
            )
            SanteSiteSettings.objects.create(site=site, sponsor_id="S1")
            MetaSettings.objects.create(site=site)
        self.all_sites = Site.objects.filter(account=self.account)

    def test_the_account_wrapper_and_the_site_function_count_the_same(self):
        with override_settings(
            PAYMENT_METHODS_ENABLED=True, MEMBERS_ENABLED=True, PACK_ENABLED=True,
            SHOP_ENABLED=True, SANTE_SETTINGS_ENABLED=True,
        ):
            pairs = (
                (sites.blank_sites_of(self.account, None, True), sites.blank_sites(self.all_sites, None, True)),
                (sante.blank_settings_of(self.account, True), sante.blank_settings(self.all_sites, True)),
                (payments.blank_payments_of(self.account, None, True), payments.blank_payments(self.all_sites, None, True)),
                (members.blank_members_of(self.account, True), members.blank_members(self.all_sites, True)),
                (pack.blank_packs_of(self.account, True), pack.blank_packs(self.all_sites, True)),
                (shop.blank_shop_of(self.account, True), shop.blank_shop(self.all_sites, True)),
                (meta.blank_meta_of(self.account, True), meta.blank_meta(self.all_sites, True)),
            )
        for wrapper, direct in pairs:
            self.assertEqual(wrapper, direct)
        self.assertEqual(pairs[0][0]["sites"], 2)
        self.assertEqual(pairs[2][0]["site_payment_accounts"], 2)

    def test_a_single_site_queryset_touches_that_site_only(self):
        one = Site.objects.filter(pk=self.first.site.pk)
        with override_settings(PAYMENT_METHODS_ENABLED=True, MEMBERS_ENABLED=True):
            self.assertEqual(payments.blank_payments(one, None, True)["site_payment_accounts"], 1)
            self.assertEqual(members.blank_members(one, True)["members"], 1)
            payments.blank_payments(one, self.first.service_ended_at)
            members.blank_members(one)
        mine = SitePaymentAccount.objects.get(site=self.first.site)
        theirs = SitePaymentAccount.objects.get(site=self.other_site)
        self.assertEqual((mine.account_number, theirs.account_number), ("", "123"))
        self.assertEqual(Member.objects.get(site=self.other_site).full_name, "M")
        self.assertEqual(Member.objects.get(site=self.first.site).full_name, "")

    def test_the_wrappers_still_answer_empty_while_their_switch_is_off(self):
        with override_settings(
            PAYMENT_METHODS_ENABLED=False, MEMBERS_ENABLED=False, PACK_ENABLED=False,
            SHOP_ENABLED=False, SANTE_SETTINGS_ENABLED=False,
        ):
            self.assertEqual(payments.blank_payments_of(self.account, None, True), {})
            self.assertEqual(members.blank_members_of(self.account, True), {})
            self.assertEqual(pack.blank_packs_of(self.account, True), {})
            self.assertEqual(shop.blank_shop_of(self.account, True), {})
            self.assertEqual(sante.blank_settings_of(self.account, True), {})
            # D13: the keyword reads the table whatever the switch says; the default is unchanged.
            self.assertEqual(
                payments.blank_payments(self.all_sites, None, True, ignore_switch=True),
                {"site_payment_accounts": 2, "site_payment_qr_files": 0},
            )
            self.assertEqual(members.blank_members(self.all_sites, True, ignore_switch=True)["members"], 2)
            self.assertEqual(
                sante.blank_settings(self.all_sites, True, ignore_switch=True), {"sante_site_settings": 2}
            )

    @override_settings(PACK_ENABLED=True)
    def test_keep_files_leaves_the_pack_proof_files_and_their_keys(self):
        # D14: the keyword only. No pack proof row is needed to see that no file is counted.
        counts = pack.blank_packs(self.all_sites, True, keep_files=True)
        self.assertEqual(counts["pack_proof_files"], 0)
        self.assertEqual(pack.blank_packs(self.all_sites, True)["pack_proof_files"], 0)
