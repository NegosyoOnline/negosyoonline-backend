"""Account disposal blanks a site's Meta token and test code and deletes its saved browser
identifiers. Delivery rows stay (no personal data). Another account is never reached."""
from django.test import override_settings

from apps.accounts import retention as rules
from apps.accounts.tests.disposal_base import EMAIL, OTHER_EMAIL, DisposalTestCase
from apps.meta_ads.models import MetaDelivery, MetaOrderContext, MetaSettings
from apps.meta_ads.tests.base import CODE, TOKEN, configure, switched_on
from apps.meta_ads.tests.base import setUpModule, tearDownModule  # noqa: F401
from apps.pack.tests.owner_base import make_order
from apps.payments.tests.base import use_local_private_store
from apps.sites.models import Site

RULE = "never_verified_accounts"


@switched_on
@override_settings(RETENTION_ENABLED=True)
class MetaDisposalTests(DisposalTestCase):
    def setUp(self):
        super().setUp()
        use_local_private_store(self)
        self.mine = Site.objects.get(account=self.account)
        self.theirs = Site.objects.get(account=self.other)
        for site in (self.mine, self.theirs):
            configure(site)
            order, _ = make_order(site)
            MetaOrderContext.objects.create(order=order, site=site, client_ip="203.0.113.9", user_agent="UA")
            MetaDelivery.objects.create(site=site, event_name="PageView", event_id="pv_" + "a" * 16)

    def dispose(self, **kw):
        return rules.dispose_account(self.account, now=self.now, rule=RULE, **kw)

    def test_a_dry_run_counts_and_changes_nothing(self):
        counts = self.dispose(dry_run=True)
        self.assertEqual((counts["meta_settings"], counts["meta_order_contexts"]), (1, 1))
        self.assertEqual(MetaSettings.objects.exclude(token_encrypted="").count(), 2)
        self.assertEqual(MetaOrderContext.objects.count(), 2)

    def test_a_disposal_blanks_the_token_and_code_and_deletes_the_contexts(self):
        counts = self.dispose()
        self.assertEqual((counts["meta_settings"], counts["meta_order_contexts"]), (1, 1))
        mine = MetaSettings.objects.get(site=self.mine)
        self.assertEqual((mine.token_encrypted, mine.test_event_code), ("", ""))
        self.assertTrue(mine.pixel_id)  # the Pixel ID is not personal data
        self.assertFalse(MetaOrderContext.objects.filter(site=self.mine).exists())
        self.assertEqual(MetaDelivery.objects.filter(site=self.mine).count(), 1)

    def test_another_accounts_rows_are_untouched(self):
        self.dispose()
        theirs = MetaSettings.objects.get(site=self.theirs)
        self.assertNotEqual(theirs.token_encrypted, "")
        self.assertEqual(theirs.test_event_code, CODE)
        self.assertEqual(MetaOrderContext.objects.filter(site=self.theirs).count(), 1)

    def test_it_runs_with_the_meta_switch_off_and_the_audit_names_the_fields(self):
        with override_settings(META_EVENTS_ENABLED=False):
            self.dispose()
        self.assertEqual(MetaSettings.objects.get(site=self.mine).token_encrypted, "")
        from apps.operators.models import OperatorAuditEntry

        entry = OperatorAuditEntry.objects.filter(action="account_disposed").latest("pk")
        self.assertEqual(entry.detail["blanked"]["meta_settings"], ["token_encrypted", "test_event_code"])
        self.assertNotIn(TOKEN, str(entry.detail))
        self.assertNotIn(EMAIL, str(entry.detail))
        self.assertNotEqual(OTHER_EMAIL, "")
